import os
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

EXPECTED_BUILD = "155.4"
SERVER_DOWNLOAD_URL = f"https://github.com/Anuken/Mindustry/releases/download/v{EXPECTED_BUILD}/server-release.jar"

DEFAULT_INSTALL_PATHS = [
    Path(r"C:\Games\mindustry\server-release.jar"),
    Path(__file__).resolve().parent / "server-release.jar"
]


def get_server_jar_path(custom_path: Optional[Path] = None) -> Path:
    """Returns the primary path for server-release.jar."""
    if custom_path:
        return Path(custom_path)
    return DEFAULT_INSTALL_PATHS[0]


def is_valid_server_jar(jar_path: Path) -> bool:
    """Checks whether the jar file exists and is a valid Mindustry server archive."""
    p = Path(jar_path)
    if not p.is_file():
        return False
    if p.stat().st_size < 1024 * 1024:  # At least 1 MB
        return False
    try:
        with zipfile.ZipFile(p, "r") as zf:
            names = set(zf.namelist())
            return "version.properties" in names or "mindustry/server/ServerLauncher.class" in names
    except Exception:
        return False


def download_server_jar(target_path: Optional[Path] = None, force: bool = False) -> Path:
    """Downloads server-release.jar from the official GitHub release."""
    target = get_server_jar_path(target_path)
    if not force and is_valid_server_jar(target):
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".tmp")

    # Clear proxy environment variables for the download request if needed
    old_https = os.environ.pop("HTTPS_PROXY", None)
    old_http = os.environ.pop("HTTP_PROXY", None)

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(SERVER_DOWNLOAD_URL) as response, open(temp_target, "wb") as out_file:
            chunk_size = 64 * 1024
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)

        temp_target.replace(target)
    finally:
        if old_https:
            os.environ["HTTPS_PROXY"] = old_https
        if old_http:
            os.environ["HTTP_PROXY"] = old_http

    if not is_valid_server_jar(target):
        raise RuntimeError(f"Downloaded file {target} is not a valid Mindustry server jar.")

    return target


def ensure_server_jar(target_path: Optional[Path] = None) -> Path:
    """Returns a valid server jar, downloading it if missing."""
    target = get_server_jar_path(target_path)
    if is_valid_server_jar(target):
        return target
    # Check secondary default path
    if is_valid_server_jar(DEFAULT_INSTALL_PATHS[1]):
        return DEFAULT_INSTALL_PATHS[1]
    return download_server_jar(target)


if __name__ == "__main__":
    print(f"Ensuring server jar for Mindustry v{EXPECTED_BUILD}...")
    jar = ensure_server_jar()
    print(f"Server jar ready at: {jar}")
