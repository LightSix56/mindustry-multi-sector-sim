import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from mindustry_sim.saves import (
    SectorMetadata,
    prepare_worker_save,
    sync_worker_save_back,
    get_default_mindustry_save_dir
)


class PortPool:
    """Manages a pool of UDP/TCP ports for worker instances."""

    def __init__(self, start_port: int = 6567, max_ports: int = 20):
        self.start_port = start_port
        self.max_ports = max_ports
        self._allocated: Dict[str, int] = {}  # sector_key -> port
        self._lock = threading.Lock()

    def acquire(self, sector_key: str) -> int:
        with self._lock:
            if sector_key in self._allocated:
                return self._allocated[sector_key]

            used_ports = set(self._allocated.values())
            for port in range(self.start_port, self.start_port + self.max_ports):
                if port not in used_ports:
                    self._allocated[sector_key] = port
                    return port

            raise RuntimeError(f"Port pool exhausted! Maximum {self.max_ports} concurrent workers.")

    def release(self, sector_key: str):
        with self._lock:
            self._allocated.pop(sector_key, None)

    def get_port(self, sector_key: str) -> Optional[int]:
        with self._lock:
            return self._allocated.get(sector_key)


@dataclass
class WorkerConfig:
    sector: SectorMetadata
    port: int
    server_jar: Path
    base_dir: Path
    memory_mb: int = 1024
    java_cmd: str = "java"
    appdata_save_dir: Optional[Path] = None


CLIENT_ONLY_MOD_PATTERNS = (
    "mindustrytool",
    "multi-sector-sim",
    "timecontrol",
    "time-control",
)


class SectorWorker:
    """Controls a single headless Mindustry server process simulating a specific sector."""

    def __init__(self, config: WorkerConfig, on_event: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.on_event = on_event
        self.process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False
        self._start_time: Optional[float] = None
        self.wave: int = 0
        self.wavetime: float = 0.0
        self.enemies: int = 0
        self.resources: Dict[str, int] = {}
        self.fps: float = 60.0
        self.speed: float = 1.0
        self.goal: Optional[dict] = None
        self.goal_reached: bool = False
        self.players: int = 0
        self.last_log: str = ""
        self.auto_sync_interval: float = 15.0
        self._server_loaded_event = threading.Event()
        self._save_loaded_event = threading.Event()
        self._save_completed_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def sector_key(self) -> str:
        return f"{self.config.sector.planet}-{self.config.sector.sector_id}"

    def status(self) -> dict:
        uptime = time.time() - self._start_time if (self._running and self._start_time) else 0
        return {
            "sector_id": self.config.sector.sector_id,
            "planet": self.config.sector.planet,
            "save_name": self.config.sector.save_name,
            "running": self.is_running(),
            "port": self.config.port,
            "pid": self.process.pid if self.process else None,
            "uptime_seconds": round(uptime, 1),
            "wave": self.wave,
            "wavetime": round(self.wavetime, 1),
            "enemies": self.enemies,
            "resources": self.resources,
            "fps": self.fps,
            "speed": self.speed,
            "goal": self.goal,
            "goal_reached": self.goal_reached,
            "players": self.players,
            "last_log": self.last_log
        }

    def is_running(self) -> bool:
        if self.process is None:
            return False
        if self.process.poll() is not None:
            self._running = False
            return False
        return self._running

    def set_speed(self, multiplier: float):
        """Sets the simulation speed multiplier (e.g. 1.0, 2.0, 5.0, 10.0)."""
        self.speed = max(0.1, min(float(multiplier), 50.0))
        if self.is_running():
            js = (
                f"Time.setDeltaProvider(new Packages.arc.func.Floatp({{ get: function() {{ return {self.speed}; }} }})); "
                f"print('[multi-sector-sim:worker] Speed set to {self.speed}x');"
            )
            self.send_js(js)

    def set_goal(self, goal_data: dict):
        """Sets the simulation goal (e.g. target_wave)."""
        self.goal = goal_data
        self.goal_reached = False
        if self.is_running() and goal_data and goal_data.get("target_wave"):
            self.send_js(
                "Vars.state.rules.waves = true; "
                "Vars.state.rules.waveTimer = true; "
                "Vars.state.rules.blockHealthMultiplier = 1.0f;"
            )
        self._check_goal()

    def _check_goal(self):
        if not self.goal or self.goal_reached:
            return
        target_wave = self.goal.get("target_wave")
        if target_wave is not None and self.wave >= target_wave:
            self.goal_reached = True
            self.save()
            if self.goal.get("auto_stop", False):
                threading.Thread(target=self.stop, daemon=True).start()

    def get_pre_stabilize_js(self) -> str:
        planet = self.config.sector.planet
        sector_id = self.config.sector.sector_id
        return (
            f"if (Packages.mindustry.server.ServerControl.instance) {{"
            f"  Packages.mindustry.server.ServerControl.instance.gameOverListener = cons(function(e) {{}}); "
            f"}} "
            f"Events.on(EventType.WorldLoadEvent, cons(function() {{"
            f"  Vars.state.rules.canGameOver = false; "
            f"  Vars.state.rules.winWave = 0; "
            f"  Vars.state.rules.waves = false; "
            f"  Vars.state.rules.waveTimer = false; "
            f"  Vars.state.rules.waitEnemies = false; "
            f"  Vars.state.rules.blockHealthMultiplier = 1000.0; "
            f"  Time.setDeltaProvider(new Packages.arc.func.Floatp({{ get: function() {{ return {self.speed}; }} }})); "
            f"  Vars.state.set(Packages.mindustry.core.GameState.State.playing); "
            f'  try {{ '
            f'    var _p = Vars.content.planet("{planet}") || Vars.content.planets().find(function(p) {{ return String(p.name).toLowerCase() == String("{planet}").toLowerCase(); }}); '
            f'    if (_p && _p.sectors && _p.sectors.size > {sector_id}) Vars.state.rules.sector = _p.sectors.get({sector_id}); '
            f'  }} catch(e) {{}} '
            f"  if (Packages.mindustry.server.ServerControl.instance) {{"
            f"    Packages.mindustry.server.ServerControl.instance.cancelPlayTask(); "
            f"    Packages.mindustry.server.ServerControl.instance.inGameOverWait = false; "
            f"    Packages.mindustry.server.ServerControl.instance.gameOverListener = cons(function(e) {{}}); "
            f"  }} "
            f"}}));"
        )

    def get_post_stabilize_js(self) -> str:
        planet = self.config.sector.planet
        sector_id = self.config.sector.sector_id
        return (
            f"Vars.state.rules.canGameOver = false; "
            f"Vars.state.rules.winWave = 0; "
            f"Vars.state.rules.waves = false; "
            f"Vars.state.rules.waveTimer = false; "
            f"Vars.state.rules.waitEnemies = false; "
            f"Vars.state.rules.blockHealthMultiplier = 1000.0; "
            f"Time.setDeltaProvider(new Packages.arc.func.Floatp({{ get: function() {{ return {self.speed}; }} }})); "
            f"Vars.state.set(Packages.mindustry.core.GameState.State.playing); "
            f"Groups.unit.each(cons(function(u) {{ if (u.team != Team.sharded) u.kill(); }})); "
            f'try {{ '
            f'  var _p = Vars.content.planet("{planet}") || Vars.content.planets().find(function(p) {{ return String(p.name).toLowerCase() == String("{planet}").toLowerCase(); }}); '
            f'  if (_p && _p.sectors && _p.sectors.size > {sector_id}) {{ '
            f'    Vars.state.rules.sector = _p.sectors.get({sector_id}); '
            f'    if (Vars.state.rules.sector && Vars.state.rules.sector.info && Vars.state.rules.sector.info.items) {{ '
            f'      var _c = Team.sharded.core(); '
            f'      if (!_c && Vars.state.teams && Vars.state.teams.get(Team.sharded)) _c = Vars.state.teams.get(Team.sharded).core(); '
            f'      if (_c && _c.items) {{ '
            f'        Vars.state.rules.sector.info.items.each(cons(function(st) {{ '
            f'          if (_c.items.get(st.item) < st.amount) _c.items.set(st.item, st.amount); '
            f'        }})); '
            f'      }} '
            f'    }} '
            f'  }} '
            f'}} catch(e) {{}} '
            f"if (Packages.mindustry.server.ServerControl.instance) {{"
            f"  Packages.mindustry.server.ServerControl.instance.cancelPlayTask(); "
            f"  Packages.mindustry.server.ServerControl.instance.inGameOverWait = false; "
            f"  Packages.mindustry.server.ServerControl.instance.gameOverListener = cons(function(e) {{}}); "
            f"}}"
        )

    def start(self, timeout: float = 60.0):
        """Prepares directory, copies save, starts JVM, and sends load commands."""
        with self._lock:
            if self.is_running():
                return

            working_dir = self.config.base_dir
            working_dir.mkdir(parents=True, exist_ok=True)
            saves_dir = working_dir / "config" / "saves"
            saves_dir.mkdir(parents=True, exist_ok=True)

            # Copy mods if available, but filter out client-only mods to prevent headless JVM crashes
            appdata_mods = Path.home() / "AppData" / "Roaming" / "Mindustry" / "mods"
            worker_mods = working_dir / "config" / "mods"
            worker_mods.mkdir(parents=True, exist_ok=True)

            if appdata_mods.exists():
                for item in appdata_mods.iterdir():
                    item_lower = item.name.lower()
                    if any(pat in item_lower for pat in CLIENT_ONLY_MOD_PATTERNS):
                        continue
                    dest = worker_mods / item.name
                    if not dest.exists():
                        try:
                            if item.is_dir():
                                shutil.copytree(item, dest, dirs_exist_ok=True)
                            else:
                                shutil.copy2(item, dest)
                        except Exception:
                            pass

            # Clean up any client-only mods from previous copies
            for item in list(worker_mods.iterdir()):
                item_lower = item.name.lower()
                if any(pat in item_lower for pat in CLIENT_ONLY_MOD_PATTERNS):
                    try:
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Copy the sector save to the worker's save dir
            worker_save = prepare_worker_save(self.config.sector.file_path, saves_dir)

            cmd = [
                self.config.java_cmd,
                f"-Xms128m",
                f"-Xmx{self.config.memory_mb}m",
                "-jar",
                str(self.config.server_jar.resolve())
            ]

            self._server_loaded_event.clear()
            self._save_loaded_event.clear()

            self.process = subprocess.Popen(
                cmd,
                cwd=str(working_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace"
            )
            self._running = True
            self._start_time = time.time()

            # Start background stdout reader
            self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader_thread.start()

            # 1. Wait for server engine to initialize
            if not self._server_loaded_event.wait(timeout=timeout):
                if not self.is_running():
                    raise RuntimeError(f"Server process terminated during startup. Last log: {self.last_log}")
                raise TimeoutError("Timed out waiting for 'Server loaded' prompt.")

            # 2. Configure server flags (disable pause on 0 players, allow custom clients)
            self.send_command("config autoPause false")
            self.send_command("config allowCustomClients true")
            time.sleep(0.05)

            # 3. Inject pre-load game-over suppression hook
            self.send_js(self.get_pre_stabilize_js())
            time.sleep(0.05)

            # 4. Configure port
            self.send_command(f"config port {self.config.port}")
            time.sleep(0.05)

            # 5. Load save
            self.send_command(f"load {self.config.sector.save_name}")

            # 6. Wait for save to load
            if not self._save_loaded_event.wait(timeout=timeout):
                if not self.is_running():
                    raise RuntimeError(f"Server process terminated while loading save. Last log: {self.last_log}")
                raise TimeoutError(f"Timed out waiting for save {self.config.sector.save_name} to load.")

            # 7. Apply post-load stabilization script
            self.send_js(self.get_post_stabilize_js())

            # 8. Apply speed if configured
            if self.speed != 1.0:
                self.set_speed(self.speed)

            # 9. Start periodic auto-sync and metrics polling thread
            self._sync_thread = threading.Thread(target=self._auto_sync_loop, daemon=True)
            self._sync_thread.start()

    def send_command(self, cmd: str):
        if self.process and self.process.stdin and self.is_running():
            try:
                self.process.stdin.write(cmd.strip() + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def send_js(self, script: str):
        self.send_command(f"js {script.strip()}")

    def save(self, wait_timeout: float = 20.0) -> Path:
        """Saves current sector state synchronously, waiting for headless confirmation, and syncs to AppData."""
        self._save_completed_event.clear()
        if self.is_running():
            planet = self.config.sector.planet
            sector_id = self.config.sector.sector_id
            sync_info_js = (
                "try { "
                "  if (Vars.state && Vars.state.rules) { "
                "    if (!Vars.state.rules.sector) { "
                f"      var _p = Vars.content.planet('{planet}') || Vars.content.planets().find(function(p) {{ return String(p.name).toLowerCase() == String('{planet}').toLowerCase(); }}); "
                f"      if (_p && _p.sectors && _p.sectors.size > {sector_id}) Vars.state.rules.sector = _p.sectors.get({sector_id}); "
                "    } "
                "    var _c = Team.sharded.core(); "
                "    if (!_c && Vars.state.teams && Vars.state.teams.get(Team.sharded)) { "
                "      _c = Vars.state.teams.get(Team.sharded).core(); "
                "    } "
                "    if (_c && _c.items && Vars.state.rules.sector && Vars.state.rules.sector.info && Vars.state.rules.sector.info.items) { "
                "      Vars.state.rules.sector.info.items.clear(); "
                "      Vars.state.rules.sector.info.items.add(_c.items); "
                "    } "
                "  } "
                "} catch(e) {}"
            )
            self.send_js(sync_info_js)
            self.send_command(f"save {self.config.sector.save_name}")
            self._save_completed_event.wait(timeout=wait_timeout)
        return self.sync_save()

    def sync_save(self) -> Path:
        """Syncs the current worker save file back to Mindustry AppData."""
        appdata_dir = self.config.appdata_save_dir or get_default_mindustry_save_dir()
        worker_save = self.config.base_dir / "config" / "saves" / self.config.sector.file_path.name
        if worker_save.exists():
            return sync_worker_save_back(worker_save, appdata_dir)
        return worker_save

    def _auto_sync_loop(self):
        """Periodically requests live metrics (every 2s) and triggers save + sync (every 10s)."""
        last_save_time = time.time()
        metric_interval = float(os.environ.get("SIM_METRIC_INTERVAL", "2.0"))
        save_interval = float(os.environ.get("SIM_SAVE_INTERVAL", "10.0"))

        while self.is_running():
            time.sleep(metric_interval)
            if not self.is_running():
                break
            try:
                # Query live metrics from game engine
                metric_script = (
                    "var _core = null; "
                    "try { _core = Team.sharded.core(); } catch(e) {} "
                    "if (!_core) { "
                    "  try { if (Vars.state && Vars.state.teams && Vars.state.teams.get(Team.sharded)) { _core = Vars.state.teams.get(Team.sharded).core(); } } catch(e) {} "
                    "} "
                    "if (_core) { "
                    "  try { Team.sharded.cores.each(cons(function(c) { c.health = c.maxHealth; })); } catch(e) {} "
                    "} "
                    "var _res = ''; "
                    "if (_core && _core.items) { "
                    "  try { "
                    "    Vars.content.items().each(cons(function(i) { "
                    "      var amt = _core.items.get(i); "
                    "      if (amt > 0) _res += i.name + ':' + amt + ';'; "
                    "    })); "
                    "  } catch(e) {} "
                    "} "
                    "print('SIM_METRICS: wavetime=' + Math.round(Vars.state.wavetime) + "
                    "' enemies=' + Vars.state.enemies + ' wave=' + Vars.state.wave + ' items=' + _res);"
                )
                self.send_js(metric_script)

                now = time.time()
                if now - last_save_time >= save_interval:
                    last_save_time = now
                    self.save()
            except Exception:
                pass

    def stop(self, timeout: float = 5.0):
        with self._lock:
            if not self.is_running():
                return

            self.save()
            time.sleep(0.5)
            self.send_command("stop")
            time.sleep(0.2)
            self.send_command("exit")

            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

            self._running = False

            # Final sync of modified save back to Mindustry AppData
            self.sync_save()

    def _read_stdout(self):
        while self.process and self.process.stdout:
            line = self.process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            self.last_log = line_str

            if "Server loaded. Type 'help' for help." in line_str:
                self._server_loaded_event.set()
            if "Save loaded." in line_str:
                self._save_loaded_event.set()

            # Auto-sync when server saves
            if "Saved to config/saves/" in line_str:
                self._save_completed_event.set()
                try:
                    self.sync_save()
                except Exception:
                    pass

            # Parse live simulation metrics
            if "SIM_METRICS:" in line_str:
                try:
                    parts = line_str.split("SIM_METRICS:")[1].strip().split()
                    for p in parts:
                        if p.startswith("wavetime="):
                            wt = float(p.split("=")[1])
                            self.wavetime = max(0.0, wt / 60.0)
                        elif p.startswith("enemies="):
                            self.enemies = int(p.split("=")[1])
                        elif p.startswith("wave="):
                            self.wave = int(p.split("=")[1])
                            self._check_goal()
                        elif p.startswith("items="):
                            item_str = p.split("=")[1]
                            res_dict = {}
                            for item_pair in item_str.split(";"):
                                if ":" in item_pair:
                                    k, v = item_pair.split(":")
                                    try:
                                        res_dict[k] = int(v)
                                    except ValueError:
                                        pass
                            self.resources = res_dict
                    print(f"[multi-sector-sim:worker] SIM_METRICS: sector={self.config.sector.sector_id} resources={self.resources}")
                except Exception:
                    pass

            # Parse wave updates from game log
            if "Wave " in line_str:
                try:
                    parts = line_str.split("Wave ")
                    if len(parts) > 1:
                        num = int(parts[1].split()[0].replace(":", ""))
                        self.wave = num
                        self._check_goal()
                except Exception:
                    pass

            # Notify event listener if registered
            if self.on_event and line_str:
                self.on_event(self.sector_key, line_str)
