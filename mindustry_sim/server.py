import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from mindustry_sim.bin.download_server import ensure_server_jar, get_server_jar_path
from mindustry_sim.router import LAUNCH_LISTENER_JS, LaunchPadRouter
from mindustry_sim.saves import SectorMetadata, discover_sectors, get_default_mindustry_save_dir
from mindustry_sim.worker import PortPool, SectorWorker, WorkerConfig

logger = logging.getLogger("mindustry_sim")


class OrchestratorManager:
    """Coordinates multiple SectorWorkers and the LaunchPadRouter."""

    def __init__(
        self,
        save_dir: Optional[Path] = None,
        worker_base_dir: Optional[Path] = None,
        server_jar: Optional[Path] = None,
        start_port: int = 6567,
        max_workers: int = 10
    ):
        self.save_dir = save_dir or get_default_mindustry_save_dir()
        self.worker_base_dir = worker_base_dir or (Path(__file__).resolve().parent.parent / "worker_instances")
        self.server_jar = server_jar or get_server_jar_path()
        self.port_pool = PortPool(start_port=start_port, max_ports=max_workers)
        self.router = LaunchPadRouter()
        self.workers: Dict[int, SectorWorker] = {}
        self.active_player_sector: Optional[int] = None
        self.suspended_sectors: Dict[int, dict] = {}
        self.last_resources: Dict[int, dict] = {}
        self._cached_sectors: List[SectorMetadata] = []
        self._last_discover_time: float = 0.0
        self._discover_cache_ttl: float = 5.0
        self._lock = threading.Lock()

    def get_sectors(self, force: bool = False) -> List[dict]:
        now = time.time()
        with self._lock:
            if force or (now - self._last_discover_time >= self._discover_cache_ttl) or not self._cached_sectors:
                self._cached_sectors = discover_sectors(self.save_dir)
                self._last_discover_time = now
            discovered = list(self._cached_sectors)

            results = []
            for sector in discovered:
                worker = self.workers.get(sector.sector_id)
                if worker and worker.is_running():
                    info = worker.status()
                    if hasattr(worker, "resources") and worker.resources:
                        self.last_resources[sector.sector_id] = worker.resources
                else:
                    info = {
                        "sector_id": sector.sector_id,
                        "planet": sector.planet,
                        "save_name": sector.save_name,
                        "running": False,
                        "port": None,
                        "pid": None,
                        "uptime_seconds": 0,
                        "wave": 0,
                        "fps": 0,
                        "players": 0,
                        "resources": self.last_resources.get(sector.sector_id, {}),
                        "last_log": ""
                    }
                results.append(info)
        return results

    def start_sector(self, sector_id: int) -> dict:
        with self._lock:
            if sector_id in self.workers and self.workers[sector_id].is_running():
                return self.workers[sector_id].status()

            # Find metadata (force fresh discovery if not in cache)
            now = time.time()
            if (now - self._last_discover_time >= self._discover_cache_ttl) or not self._cached_sectors:
                self._cached_sectors = discover_sectors(self.save_dir)
                self._last_discover_time = now

            discovered = {s.sector_id: s for s in self._cached_sectors}
            if sector_id not in discovered:
                self._cached_sectors = discover_sectors(self.save_dir)
                self._last_discover_time = time.time()
                discovered = {s.sector_id: s for s in self._cached_sectors}
                if sector_id not in discovered:
                    raise ValueError(f"Sector {sector_id} not found in saves directory: {self.save_dir}")

            sector_meta = discovered[sector_id]
            port = self.port_pool.acquire(str(sector_id))

            def on_worker_event(sec_key: str, line: str):
                event = self.router.parse_launch_line(line)
                if event:
                    self.router.route_transfer(event)

            worker_dir = self.worker_base_dir / f"sector_{sector_id}"
            worker_mem = int(os.environ.get("SIM_WORKER_MEMORY_MB", "1024"))
            config = WorkerConfig(
                sector=sector_meta,
                port=port,
                server_jar=self.server_jar,
                base_dir=worker_dir,
                memory_mb=worker_mem,
                appdata_save_dir=self.save_dir
            )
            worker = SectorWorker(config, on_event=on_worker_event)
            self.workers[sector_id] = worker
            self.router.register_worker(sector_id, worker)

        # Start JVM outside of global lock so other API queries (GET /api/sectors, status, etc.)
        # are not blocked while the map is loading
        try:
            worker.start(timeout=90.0)
        except Exception:
            with self._lock:
                self.workers.pop(sector_id, None)
                self.router.unregister_worker(sector_id)
                self.port_pool.release(str(sector_id))
            raise

        # Inject launch listener JS and flush any pending cargo
        threading.Timer(2.0, lambda: worker.send_js(LAUNCH_LISTENER_JS)).start()
        threading.Timer(3.0, lambda: self.router.flush_pending(sector_id)).start()

        return worker.status()

    def stop_sector(self, sector_id: int) -> dict:
        with self._lock:
            self.suspended_sectors.pop(sector_id, None)
            worker = self.workers.get(sector_id)
            if not worker:
                return {"sector_id": sector_id, "running": False}

            if hasattr(worker, "resources") and worker.resources:
                self.last_resources[sector_id] = worker.resources

            worker.stop()
            self.router.unregister_worker(sector_id)
            self.port_pool.release(str(sector_id))
            self.workers.pop(sector_id, None)
            return {"sector_id": sector_id, "running": False}

    def save_sector(self, sector_id: int) -> dict:
        with self._lock:
            worker = self.workers.get(sector_id)
            if worker and worker.is_running():
                sync_path = worker.save()
                return {"sector_id": sector_id, "saved": True, "path": str(sync_path) if sync_path else None}
            return {"sector_id": sector_id, "saved": False, "error": "Not running"}

    def set_speed(self, sector_id: int, multiplier: float) -> dict:
        with self._lock:
            worker = self.workers.get(sector_id)
            if not worker or not worker.is_running():
                return {"sector_id": sector_id, "error": "Sector not running", "speed": 1.0}
            worker.set_speed(multiplier)
            return {"sector_id": sector_id, "speed": worker.speed}

    def set_goal(self, sector_id: int, goal_data: dict) -> dict:
        with self._lock:
            worker = self.workers.get(sector_id)
            if not worker or not worker.is_running():
                return {"sector_id": sector_id, "error": "Sector not running", "goal": None}
            worker.set_goal(goal_data)
            return {"sector_id": sector_id, "goal": worker.goal, "goal_reached": worker.goal_reached}

    def player_enter(self, sector_id: int) -> dict:
        with self._lock:
            self.active_player_sector = sector_id
            worker = self.workers.get(sector_id)
            was_running = False
            saved_resources = {}
            if worker and worker.is_running():
                was_running = True
                self.suspended_sectors[sector_id] = {
                    "speed": getattr(worker, "speed", 1.0),
                    "goal": getattr(worker, "goal", None)
                }
                if hasattr(worker, "resources") and worker.resources:
                    saved_resources = dict(worker.resources)
                    self.last_resources[sector_id] = dict(worker.resources)
                if hasattr(worker, "save"):
                    try:
                        worker.save()
                    except Exception:
                        pass
                if hasattr(worker, "stop"):
                    try:
                        worker.stop()
                    except Exception:
                        pass
                self.router.unregister_worker(sector_id)
                self.port_pool.release(str(sector_id))
                self.workers.pop(sector_id, None)
                logger.info(f"Auto-suspended sector {sector_id} on player entry.")
            return {
                "active_sector": self.active_player_sector,
                "suspended": sector_id in self.suspended_sectors,
                "was_running": was_running,
                "resources": saved_resources or self.last_resources.get(sector_id, {})
            }

    def player_leave(self, sector_id: Optional[int] = None, async_resume: bool = False, delay: float = 1.0) -> dict:
        with self._lock:
            target_sector = sector_id if sector_id is not None else self.active_player_sector
            if self.active_player_sector == target_sector:
                self.active_player_sector = None

            resumed_sectors = []
            if target_sector is not None and target_sector in self.suspended_sectors:
                saved_settings = self.suspended_sectors.pop(target_sector)
                resumed_sectors.append(target_sector)
                logger.info(f"Auto-resuming sector {target_sector} with settings {saved_settings}")

                def _resume():
                    if delay > 0 and async_resume:
                        time.sleep(delay)
                    try:
                        self.start_sector(target_sector)
                        worker = self.workers.get(target_sector)
                        if worker:
                            if saved_settings.get("speed"):
                                worker.set_speed(saved_settings["speed"])
                            if saved_settings.get("goal"):
                                worker.set_goal(saved_settings["goal"])
                    except Exception as e:
                        logger.error(f"Failed to auto-resume sector {target_sector}: {e}")

                if async_resume:
                    threading.Thread(target=_resume, daemon=True).start()
                else:
                    _resume()

            return {
                "active_sector": self.active_player_sector,
                "resumed": resumed_sectors
            }

    def stop_all(self):
        with self._lock:
            self.suspended_sectors.clear()
            self.active_player_sector = None
            for sector_id, worker in list(self.workers.items()):
                try:
                    if hasattr(worker, "resources") and worker.resources:
                        self.last_resources[sector_id] = worker.resources
                    worker.stop()
                except Exception:
                    pass
                self.router.unregister_worker(sector_id)
                self.port_pool.release(str(sector_id))
            self.workers.clear()


class OrchestratorHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Orchestrator REST API."""

    manager: OrchestratorManager

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = path.split("/")

        if path == "/api/sectors":
            sectors = self.manager.get_sectors()
            self._send_json({"sectors": sectors})
        elif len(parts) >= 4 and parts[1] == "api" and parts[2] == "sectors":
            try:
                sector_id = int(parts[3])
                worker = self.manager.workers.get(sector_id)
                if worker:
                    self._send_json(worker.status())
                else:
                    self._send_json({"sector_id": sector_id, "running": False})
            except ValueError:
                self._send_json({"error": "Invalid sector ID"}, status=400)
        elif path == "/api/status":
            running_count = sum(1 for w in self.manager.workers.values() if w.is_running())
            self._send_json({
                "status": "online",
                "running_sectors": running_count,
                "total_sectors": len(self.manager.get_sectors())
            })
        elif path == "/api/player":
            self._send_json({
                "active_sector": self.manager.active_player_sector,
                "suspended_sectors": list(self.manager.suspended_sectors.keys())
            })
        elif path == "/api/transfers":
            history = [
                {
                    "src": e.source_sector,
                    "dst": e.target_sector,
                    "item": e.item,
                    "amount": e.amount,
                    "timestamp": e.timestamp
                }
                for e in self.manager.router.history[-50:]
            ]
            self._send_json({"transfers": history})
        else:
            self._send_json({"error": "Not Found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = path.split("/")

        if path == "/api/player/enter":
            content_len = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_len > 0:
                try:
                    payload = json.loads(self.rfile.read(content_len).decode("utf-8"))
                except Exception:
                    pass
            sector_id = payload.get("sector_id")
            if sector_id is None:
                self._send_json({"error": "Missing sector_id"}, status=400)
            else:
                res = self.manager.player_enter(int(sector_id))
                self._send_json(res)
            return
        elif path == "/api/player/leave":
            content_len = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_len > 0:
                try:
                    payload = json.loads(self.rfile.read(content_len).decode("utf-8"))
                except Exception:
                    pass
            sector_id = payload.get("sector_id")
            res = self.manager.player_leave(int(sector_id) if sector_id is not None else None, async_resume=True, delay=1.0)
            self._send_json(res)
            return

        # Format: /api/sectors/{id}/{action}
        if len(parts) >= 5 and parts[1] == "api" and parts[2] == "sectors":
            try:
                sector_id = int(parts[3])
            except ValueError:
                self._send_json({"error": "Invalid sector ID"}, status=400)
                return

            action = parts[4]
            content_len = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_len > 0:
                try:
                    payload = json.loads(self.rfile.read(content_len).decode("utf-8"))
                except Exception:
                    payload = {}

            try:
                if action == "start":
                    res = self.manager.start_sector(sector_id)
                    self._send_json(res)
                elif action == "stop":
                    res = self.manager.stop_sector(sector_id)
                    self._send_json(res)
                elif action == "save":
                    res = self.manager.save_sector(sector_id)
                    self._send_json(res)
                elif action == "speed":
                    multiplier = float(payload.get("speed", 1.0))
                    res = self.manager.set_speed(sector_id, multiplier)
                    self._send_json(res)
                elif action == "goal":
                    res = self.manager.set_goal(sector_id, payload)
                    self._send_json(res)
                else:
                    self._send_json({"error": f"Unknown action: {action}"}, status=400)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
        else:
            self._send_json({"error": "Not Found"}, status=404)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError, OSError):
            self.close_connection = True

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        pass


class OrchestratorServer:
    """HTTP Server hosting the REST API with multithreading support."""

    def __init__(self, manager: OrchestratorManager, host: str = "0.0.0.0", port: int = 8765):
        self.manager = manager
        self.host = host
        self.port = port
        handler_class = type("BoundHandler", (OrchestratorHandler,), {"manager": manager})
        self.httpd = ThreadingHTTPServer((host, port), handler_class)
        self.httpd.daemon_threads = True

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_orchestrator(host: str = "0.0.0.0", port: int = 8765):
    ensure_server_jar()
    manager = OrchestratorManager()
    server = OrchestratorServer(manager, host=host, port=port)
    print(f"Mindustry Sector Orchestrator running on http://{host}:{port}")
    try:
        while True:
            try:
                server.serve_forever()
                break
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError) as e:
                logger.warning(f"Handled transient socket exception: {e}")
                continue
    except KeyboardInterrupt:
        print("\nShutting down workers and server...")
        manager.stop_all()
        server.shutdown()
    except Exception as e:
        logger.error(f"Fatal error in orchestrator: {e}")
        manager.stop_all()
        server.shutdown()


if __name__ == "__main__":
    run_orchestrator()
