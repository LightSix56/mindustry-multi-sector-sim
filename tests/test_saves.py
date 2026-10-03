from pathlib import Path
import pytest
from mindustry_sim.saves import (
    parse_sector_filename,
    discover_sectors,
    get_default_mindustry_save_dir,
    prepare_worker_save,
    sync_worker_save_back,
    SectorMetadata
)

def test_parse_sector_filename():
    meta = parse_sector_filename(Path("sector-serpulo-170.msav"))
    assert meta is not None
    assert meta.planet == "serpulo"
    assert meta.sector_id == 170
    assert not meta.is_backup
    assert meta.save_name == "sector-serpulo-170"

def test_parse_backup_filename():
    meta = parse_sector_filename(Path("sector-serpulo-170.msav-backup.msav"))
    assert meta is not None
    assert meta.is_backup

def test_parse_non_sector_file():
    assert parse_sector_filename(Path("some_custom_save.msav")) is None

def test_discover_sectors_in_dir(tmp_path):
    (tmp_path / "sector-serpulo-170.msav").write_bytes(b"data1")
    (tmp_path / "sector-serpulo-170.msav-backup.msav").write_bytes(b"data1_bak")
    (tmp_path / "sector-erekir-12.msav").write_bytes(b"data2")
    (tmp_path / "random.txt").write_text("hello")

    sectors = discover_sectors(tmp_path)
    assert len(sectors) == 2
    ids = {s.sector_id for s in sectors}
    assert ids == {170, 12}
    s170 = next(s for s in sectors if s.sector_id == 170)
    assert s170.backup_path is not None

def test_prepare_and_sync_worker_save(tmp_path):
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()

    orig_file = save_dir / "sector-serpulo-170.msav"
    orig_file.write_bytes(b"initial_save_bytes")

    worker_save = prepare_worker_save(orig_file, worker_dir)
    assert worker_save.exists()
    assert worker_save.read_bytes() == b"initial_save_bytes"

    # Simulate worker modification
    worker_save.write_bytes(b"updated_worker_bytes")

    # Sync back to save_dir
    synced = sync_worker_save_back(worker_save, save_dir)
    assert synced.read_bytes() == b"updated_worker_bytes"
    # Ensure backup was created
    backup = save_dir / "sector-serpulo-170.msav-backup.msav"
    assert backup.exists()
    assert backup.read_bytes() == b"initial_save_bytes"

def test_discover_real_user_saves():
    real_saves = discover_sectors()
    # If the user has Mindustry installed on this PC, we expect at least 1-2 sectors
    if real_saves:
        ids = [s.sector_id for s in real_saves]
        assert 170 in ids or 64 in ids

