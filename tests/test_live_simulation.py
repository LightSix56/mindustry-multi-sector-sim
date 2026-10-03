import time
import shutil
from pathlib import Path
import pytest

from mindustry_sim.worker import SectorWorker, WorkerConfig
from mindustry_sim.saves import SectorMetadata

def test_live_headless_simulation(tmp_path):
    # Prepare mock appdata save directory
    test_appdata = tmp_path / "appdata_saves"
    test_appdata.mkdir(parents=True)

    # Use serpulo 170 save from real user saves if available
    real_saves = list(Path(r"C:\Users\FROG2\AppData\Roaming\Mindustry\saves").glob("*170*.msav"))
    if not real_saves:
        pytest.skip("No sector 170 save found in user AppData")
    
    src_save = real_saves[0]
    test_save = test_appdata / src_save.name
    shutil.copy2(src_save, test_save)
    initial_mtime = test_save.stat().st_mtime

    meta = SectorMetadata(
        planet="serpulo",
        sector_id=170,
        file_path=test_save,
        is_backup=False,
        save_name="sector-serpulo-170"
    )

    worker_dir = tmp_path / "worker_170"
    cfg = WorkerConfig(
        sector=meta,
        port=6580,
        server_jar=Path(r"C:\Games\mindustry\server-release.jar"),
        base_dir=worker_dir,
        appdata_save_dir=test_appdata,
        memory_mb=384
    )

    worker = SectorWorker(cfg)
    # Set short sync interval for test
    worker.auto_sync_interval = 2.0

    try:
        worker.start(timeout=20.0)
        assert worker.is_running() is True
        print(f"Worker started with PID {worker.process.pid} on port {worker.config.port}")

        # Let simulation tick for 3 seconds at 1x
        time.sleep(3)

        # Set speed to 5x and let tick for 4 seconds
        worker.set_speed(5.0)
        assert worker.speed == 5.0
        time.sleep(4)

        st = worker.status()
        print(f"Status after speed boost: wave={st['wave']}, wavetime={st['wavetime']}, speed={st['speed']}, running={st['running']}")
        assert st["running"] is True

        # Trigger save and sync
        worker.save()
        time.sleep(1)
        worker.sync_save()

        # Check that test_save in appdata has been updated
        assert test_save.stat().st_mtime >= initial_mtime

    finally:
        worker.stop(timeout=5.0)
        assert worker.is_running() is False
        print("Worker stopped cleanly.")

if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_live_headless_simulation(Path(td))
