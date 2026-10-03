import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SECTOR_PATTERN = re.compile(r"^sector-([a-zA-Z0-9_-]+)-(\d+)\.msav(\-backup\.msav)?$")


@dataclass
class SectorMetadata:
    planet: str
    sector_id: int
    file_path: Path
    is_backup: bool
    save_name: str
    backup_path: Optional[Path] = None
    size_bytes: int = 0


def get_default_mindustry_save_dir() -> Path:
    """Returns the default Mindustry save directory on Windows/Linux."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "Mindustry" / "saves"
        if p.exists():
            return p
    # Fallback to user home
    user_home = Path.home()
    linux_p = user_home / ".local" / "share" / "Mindustry" / "saves"
    if linux_p.exists():
        return linux_p
    return user_home / "AppData" / "Roaming" / "Mindustry" / "saves"


def parse_sector_filename(path: Path) -> Optional[SectorMetadata]:
    """Parses a sector save filename into SectorMetadata."""
    name = path.name
    match = SECTOR_PATTERN.match(name)
    if not match:
        return None

    planet = match.group(1)
    sector_id = int(match.group(2))
    is_backup = bool(match.group(3))
    save_name = f"sector-{planet}-{sector_id}"
    size = path.stat().st_size if path.exists() else 0

    return SectorMetadata(
        planet=planet,
        sector_id=sector_id,
        file_path=path,
        is_backup=is_backup,
        save_name=save_name,
        size_bytes=size
    )


def discover_sectors(save_dir: Optional[Path] = None) -> List[SectorMetadata]:
    """Discovers all valid non-backup campaign sector saves in the given directory."""
    directory = save_dir or get_default_mindustry_save_dir()
    if not directory.exists():
        return []

    sectors_by_key = {}
    backups_by_key = {}

    for item in directory.iterdir():
        if not item.is_file():
            continue
        meta = parse_sector_filename(item)
        if not meta:
            continue

        key = (meta.planet, meta.sector_id)
        if meta.is_backup:
            backups_by_key[key] = item
        else:
            sectors_by_key[key] = meta

    for key, meta in sectors_by_key.items():
        if key in backups_by_key:
            meta.backup_path = backups_by_key[key]

    return sorted(sectors_by_key.values(), key=lambda s: (s.planet, s.sector_id))


def prepare_worker_save(source_save: Path, worker_save_dir: Path) -> Path:
    """Copies source save to worker directory for isolated simulation."""
    worker_save_dir.mkdir(parents=True, exist_ok=True)
    dest_path = worker_save_dir / source_save.name
    shutil.copy2(source_save, dest_path)
    return dest_path


_sync_lock = threading.Lock()


def sync_worker_save_back(worker_save: Path, appdata_save_dir: Path, max_retries: int = 10) -> Path:
    """Safely creates backup and copies modified worker save back to appdata save dir."""
    with _sync_lock:
        appdata_save_dir.mkdir(parents=True, exist_ok=True)
        target_file = appdata_save_dir / worker_save.name
        backup_file = appdata_save_dir / f"{worker_save.name}-backup.msav"
        tmp_file = appdata_save_dir / f"{worker_save.name}.tmp"

        for attempt in range(max_retries):
            try:
                if target_file.exists():
                    shutil.copy2(target_file, backup_file)

                shutil.copy2(worker_save, tmp_file)
                os.replace(tmp_file, target_file)
                break
            except (PermissionError, OSError):
                if attempt == max_retries - 1:
                    # Final attempt fallback
                    shutil.copy2(worker_save, target_file)
                    if tmp_file.exists():
                        try:
                            tmp_file.unlink(missing_ok=True)
                        except Exception:
                            pass
                else:
                    time.sleep(0.05)

        size = target_file.stat().st_size if target_file.exists() else 0
        print(f"[multi-sector-sim:sync] Synced worker save to {target_file} (size={size} bytes)")
        return target_file
