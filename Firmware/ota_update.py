import json
import shutil
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
        return VERSION_FILE.read_text().strip()
    return "0"


def write_local_version(version):
    VERSION_FILE.write_text(str(version).strip())


def fetch_meta():
    with urlopen(META_URL, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_zip(url):
    if TMP_ZIP.exists():
        TMP_ZIP.unlink()
    urlretrieve(url, TMP_ZIP)


def extract_zip():
    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
    TMP_EXTRACT.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(TMP_ZIP, "r") as zf:
        zf.extractall(TMP_EXTRACT)

    new_rpi = TMP_EXTRACT / "RPi"
    if not new_rpi.is_dir():
        raise RuntimeError("ZIP must contain a top-level RPi folder")

    return new_rpi


def run_ota_update():
    meta = fetch_meta()
    remote_version = str(meta.get("version", "0")).strip()
    zip_url = str(meta.get("url", "")).strip()
    local_version = read_local_version()

    if not zip_url or remote_version == local_version:
        return False

    download_zip(zip_url)
    new_rpi = extract_zip()

    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)

    if TARGET_DIR.exists():
        shutil.copytree(TARGET_DIR, BACKUP_DIR)
        shutil.rmtree(TARGET_DIR)

    shutil.move(str(new_rpi), str(TARGET_DIR))
    write_local_version(remote_version)
    return True