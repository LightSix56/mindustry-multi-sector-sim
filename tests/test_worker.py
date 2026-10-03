import threading
import time
from pathlib import Path
import pytest
from mindustry_sim.worker import PortPool, SectorWorker, WorkerConfig
from mindustry_sim.saves import SectorMetadata

def test_port_pool_allocation():
    pool = PortPool(start_port=6567, max_ports=10)
    p1 = pool.acquire("170")
    p2 = pool.acquire("64")
    assert p1 == 6567
    assert p2 == 6568
    assert pool.get_port("170") == 6567
    assert pool.get_port("64") == 6568

    # Release and reacquire
    pool.release("170")
    assert pool.get_port("170") is None
    p3 = pool.acquire("120")
    assert p3 == 6567  # Reuses lowest free port

def test_port_pool_exhaustion():
    pool = PortPool(start_port=7000, max_ports=2)
    p1 = pool.acquire("s1")
    p2 = pool.acquire("s2")
    with pytest.raises(RuntimeError):
        pool.acquire("s3")

def test_worker_config_creation(tmp_path):
    save_file = tmp_path / "sector-serpulo-170.msav"
    save_file.write_bytes(b"dummy_save")
    meta = SectorMetadata(
        planet="serpulo",
        sector_id=170,
        file_path=save_file,
        is_backup=False,
        save_name="sector-serpulo-170"
    )
    cfg = WorkerConfig(
        sector=meta,
        port=6567,
        server_jar=Path(r"C:\Games\mindustry\server-release.jar"),
        base_dir=tmp_path / "worker_170",
        memory_mb=384
    )
    worker = SectorWorker(cfg)
    assert worker.status()["running"] is False
    assert worker.status()["port"] == 6567
    assert worker.status()["sector_id"] == 170
    assert worker.status()["speed"] == 1.0
    assert worker.status()["goal"] is None
    assert worker.status()["goal_reached"] is False

def test_worker_speed_and_goal(tmp_path):
    save_file = tmp_path / "sector-serpulo-64.msav"
    save_file.write_bytes(b"dummy")
    meta = SectorMetadata(planet="serpulo", sector_id=64, file_path=save_file, is_backup=False, save_name="sector-serpulo-64")
    cfg = WorkerConfig(sector=meta, port=6568, server_jar=tmp_path / "server.jar", base_dir=tmp_path / "w64")
    worker = SectorWorker(cfg)

    # Test speed clamping and status
    worker.set_speed(5.0)
    assert worker.speed == 5.0
    assert worker.status()["speed"] == 5.0
    worker.set_speed(100.0)
    assert worker.speed == 50.0  # Clamped to 50 max
    worker.set_speed(0.01)
    assert worker.speed == 0.1   # Clamped to 0.1 min

    # Test goal and goal reaching
    worker.set_goal({"target_wave": 10, "auto_stop": False})
    assert worker.goal == {"target_wave": 10, "auto_stop": False}
    assert worker.goal_reached is False

    worker.wave = 5
    worker._check_goal()
    assert worker.goal_reached is False

    worker.wave = 10
    worker._check_goal()
    assert worker.goal_reached is True
    assert worker.status()["goal_reached"] is True

def test_worker_stabilization_scripts(tmp_path):
    save_file = tmp_path / "sector-serpulo-170.msav"
    save_file.write_bytes(b"dummy")
    meta = SectorMetadata(planet="serpulo", sector_id=170, file_path=save_file, is_backup=False, save_name="sector-serpulo-170")
    cfg = WorkerConfig(sector=meta, port=6567, server_jar=tmp_path / "server.jar", base_dir=tmp_path / "w170")
    worker = SectorWorker(cfg)

    pre_js = worker.get_pre_stabilize_js()
    assert "ServerControl.instance" in pre_js
    assert "WorldLoadEvent" in pre_js
    assert "canGameOver = false" in pre_js
    assert "Vars.state.rules.waves = false" in pre_js
    assert "Vars.state.rules.waveTimer = false" in pre_js
    assert 'planet("serpulo")' in pre_js
    assert 'sectors.get(170)' in pre_js

    post_js = worker.get_post_stabilize_js()
    assert "winWave = 0" in post_js
    assert "canGameOver = false" in post_js
    assert 'planet("serpulo")' in post_js
    assert 'sectors.get(170)' in post_js
    assert "Vars.state.rules.waveTimer = false" in post_js
    assert "Vars.state.rules.waves = false" in post_js
    assert "blockHealthMultiplier" in post_js
    assert "Time.setDeltaProvider" in post_js
    assert "Groups.unit.each" in post_js
    assert "Vars.state.set(Packages.mindustry.core.GameState.State.playing)" in post_js

def test_client_only_mods_filtered():
    from mindustry_sim.worker import CLIENT_ONLY_MOD_PATTERNS
    mods_to_test = [
        ("mindustrytoolmindustrytoolmod.zip", True),
        ("sk7725timecontrol.zip", True),
        ("time-control", True),
        ("multi-sector-sim", True),
        ("catana791asthosus.zip", False),
        ("flintyxautoresearch.zip", False),
        ("xeloboyofactoriodustry.zip", False),
    ]
    for mod_name, should_filter in mods_to_test:
        filtered = any(pat in mod_name.lower() for pat in CLIENT_ONLY_MOD_PATTERNS)
        assert filtered == should_filter, f"Mod {mod_name} expected filter={should_filter}, got {filtered}"

def test_worker_periodic_sync_and_metrics(tmp_path):
    appdata_saves = tmp_path / "appdata_saves"
    appdata_saves.mkdir()
    initial_save = appdata_saves / "sector-serpulo-170.msav"
    initial_save.write_bytes(b"initial_save_content")

    meta = SectorMetadata(planet="serpulo", sector_id=170, file_path=initial_save, is_backup=False, save_name="sector-serpulo-170")
    worker_dir = tmp_path / "w170"
    cfg = WorkerConfig(
        sector=meta,
        port=6567,
        server_jar=tmp_path / "server.jar",
        base_dir=worker_dir,
        appdata_save_dir=appdata_saves
    )
    worker = SectorWorker(cfg)

    # Check status contains live metrics fields
    status = worker.status()
    assert "wavetime" in status
    assert "enemies" in status
    assert "resources" in status

    # Simulate worker updating save in worker_instances/config/saves
    worker_saves = worker_dir / "config" / "saves"
    worker_saves.mkdir(parents=True)
    worker_save_file = worker_saves / "sector-serpulo-170.msav"
    worker_save_file.write_bytes(b"updated_simulated_save_content")

    # Call sync_save()
    synced_path = worker.sync_save()
    assert synced_path.exists()
    assert synced_path.read_bytes() == b"updated_simulated_save_content"
    # Verify appdata file was updated
    assert initial_save.read_bytes() == b"updated_simulated_save_content"
    # Verify backup was created
    backup_file = appdata_saves / "sector-serpulo-170.msav-backup.msav"
    assert backup_file.exists()
    assert backup_file.read_bytes() == b"initial_save_content"

def test_worker_set_speed_js_command(tmp_path):
    save_file = tmp_path / "sector-serpulo-170.msav"
    save_file.write_bytes(b"dummy")
    meta = SectorMetadata(planet="serpulo", sector_id=170, file_path=save_file, is_backup=False, save_name="sector-serpulo-170")
    cfg = WorkerConfig(sector=meta, port=6567, server_jar=tmp_path / "server.jar", base_dir=tmp_path / "w170")
    worker = SectorWorker(cfg)

    sent_commands = []
    worker.is_running = lambda: True
    worker.send_js = lambda script: sent_commands.append(script)

    worker.set_speed(3.5)
    assert worker.speed == 3.5
    assert len(sent_commands) == 1
    assert "Time.setDeltaProvider" in sent_commands[0]
    assert "Floatp" in sent_commands[0]
    assert "return 3.5" in sent_commands[0]

def test_worker_synchronous_save(tmp_path):
    appdata_saves = tmp_path / "appdata_saves"
    appdata_saves.mkdir()
    initial_save = appdata_saves / "sector-serpulo-170.msav"
    initial_save.write_bytes(b"initial_data")

    meta = SectorMetadata(planet="serpulo", sector_id=170, file_path=initial_save, is_backup=False, save_name="sector-serpulo-170")
    worker_dir = tmp_path / "w170"
    cfg = WorkerConfig(sector=meta, port=6567, server_jar=tmp_path / "server.jar", base_dir=worker_dir, appdata_save_dir=appdata_saves)
    worker = SectorWorker(cfg)

    worker_saves = worker_dir / "config" / "saves"
    worker_saves.mkdir(parents=True)
    worker_save_file = worker_saves / "sector-serpulo-170.msav"
    worker_save_file.write_bytes(b"saved_data_v2")

    commands_sent = []
    worker.is_running = lambda: True
    worker.send_command = lambda cmd: commands_sent.append(cmd)

    def simulate_stdout():
        time.sleep(0.05)
        worker._save_completed_event.set()

    t = threading.Thread(target=simulate_stdout)
    t.start()

    start_t = time.time()
    result = worker.save(wait_timeout=2.0)
    elapsed = time.time() - start_t
    t.join()

    assert "save sector-serpulo-170" in commands_sent
    assert elapsed >= 0.04
    assert result == initial_save
    assert initial_save.read_bytes() == b"saved_data_v2"

def test_atomic_sync_worker_save_back(tmp_path):
    from mindustry_sim.saves import sync_worker_save_back

    appdata_dir = tmp_path / "appdata"
    appdata_dir.mkdir()
    worker_save = tmp_path / "sector-serpulo-77.msav"
    worker_save.write_bytes(b"atomic_content")

    target = sync_worker_save_back(worker_save, appdata_dir)
    assert target.exists()
    assert target.read_bytes() == b"atomic_content"
    # Verify .tmp file was cleaned up by os.replace
    assert not (appdata_dir / "sector-serpulo-77.msav.tmp").exists()


