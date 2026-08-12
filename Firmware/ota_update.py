import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, urlretrieve

META_URL = "https://emea-edu.com/cameraDashboard/ota_meta.json"
BASE_DIR = Path("/home/WeatherDevice/Firmware")
TARGET_DIR = BASE_DIR / "RPi"
BACKUP_DIR = BASE_DIR / "RPi_backup"
VERSION_FILE = BASE_DIR / "version.txt"
TMP_ZIP = Path(tempfile.gettempdir()) / "rpi_update_latest.zip"
TMP_EXTRACT = Path(tempfile.gettempdir()) / "rpi_update_extract"


def read_local_version():
    if VERSION_FILE.exists():
        version = VERSION_FILE.read_text().strip()
        print(f"Local version: {version}")
        return version
    print("Local version file not found, using 0")
    return "0"


def write_local_version(version):
    VERSION_FILE.write_text(str(version).strip())
    print(f"Saved new local version: {version}")


def fetch_meta():
    print(f"Fetching OTA metadata from: {META_URL}")
    with urlopen(META_URL, timeout=30) as response:
        meta = json.loads(response.read().decode("utf-8"))
    print(f"Remote metadata: {meta}")
    return meta


def download_zip(url):
    print(f"Downloading update ZIP from: {url}")
    if TMP_ZIP.exists():
        TMP_ZIP.unlink()
    urlretrieve(url, TMP_ZIP)
    print(f"ZIP downloaded to: {TMP_ZIP}")


def extract_zip():
    print("Extracting ZIP...")
    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
    TMP_EXTRACT.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(TMP_ZIP, "r") as zf:
        zf.extractall(TMP_EXTRACT)

    new_rpi = TMP_EXTRACT / "RPi"
    if not new_rpi.is_dir():
        raise RuntimeError("ZIP must contain a top-level RPi folder")

    print(f"Extracted RPi folder found at: {new_rpi}")
    return new_rpi


def ignore_special_files(src, names):
    ignored = []
    for name in names:
        full_path = os.path.join(src, name)
        try:
            mode = os.lstat(full_path).st_mode
            if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                print(f"Skipping special file in backup: {full_path}")
                ignored.append(name)
        except Exception as e:
            print(f"Could not inspect {full_path}: {e}")
    return ignored


def run_ota_update():
    print("Starting OTA update check...")
    meta = fetch_meta()
    remote_version = str(meta.get("version", "0")).strip()
    zip_url = str(meta.get("url", "")).strip()
    local_version = read_local_version()

    print(f"Remote version: {remote_version}")

    if not zip_url:
        print("No ZIP URL found in metadata")
        return False

    if remote_version == local_version:
        print("No update available")
        return False

    print("New update available")
    download_zip(zip_url)
    new_rpi = extract_zip()

    if BACKUP_DIR.exists():
        print(f"Removing old backup: {BACKUP_DIR}")
        shutil.rmtree(BACKUP_DIR)

    if TARGET_DIR.exists():
        print(f"Backing up current RPi folder from {TARGET_DIR} to {BACKUP_DIR}")
        shutil.copytree(TARGET_DIR, BACKUP_DIR, ignore=ignore_special_files)

        print(f"Removing current RPi folder: {TARGET_DIR}")
        shutil.rmtree(TARGET_DIR)

    print(f"Moving new RPi folder into place: {TARGET_DIR}")
    shutil.move(str(new_rpi), str(TARGET_DIR))

    write_local_version(remote_version)
    print("OTA update completed successfully")
    return True


if __name__ == "__main__":
    run_ota_update()