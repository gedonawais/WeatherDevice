import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, urlretrieve
import requests

META_URL = "https://emea-edu.com/cameraDashboard/ota_meta.json"
UPLOAD_URL = "https://emea-edu.com/cameraDashboard/upload.php"

BASE_DIR = Path("/home/WeatherDevice/Firmware")
OTA_LOG_PATH = BASE_DIR / "ota.log"
TARGET_DIR = BASE_DIR / "RPi"
BACKUP_DIR = BASE_DIR / "RPi_backup"
VERSION_FILE = BASE_DIR / "version.txt"
TMP_ZIP = Path(tempfile.gettempdir()) / "rpi_update_latest.zip"
TMP_EXTRACT = Path(tempfile.gettempdir()) / "rpi_update_extract"


def logOTA(message):
    print(message)
    try:
        with open(OTA_LOG_PATH, "a") as f:
            f.write(message + "\n")
    except Exception:
        pass


def clear_ota_log():
    try:
        with open(OTA_LOG_PATH, "w") as f:
            f.write("=== OTA SESSION START ===\n")
    except Exception:
        pass


def get_ota_logs():
    try:
        with open(OTA_LOG_PATH, "r") as f:
            return f.read()
    except Exception as e:
        return f"Could not read OTA log: {e}"


def read_local_version():
    if VERSION_FILE.exists():
        version = VERSION_FILE.read_text().strip()
        logOTA(f"Local version: {version}")
        return version
    logOTA("Local version file not found, using 0")
    return "0"


def write_local_version(version):
    VERSION_FILE.write_text(str(version).strip())
    logOTA(f"Saved new local version: {version}")


def fetch_meta():
    logOTA(f"Fetching OTA metadata from: {META_URL}")
    with urlopen(META_URL, timeout=30) as response:
        meta = json.loads(response.read().decode("utf-8"))
    logOTA(f"Remote metadata: {meta}")
    return meta


def download_zip(url):
    logOTA(f"Downloading update ZIP from: {url}")
    if TMP_ZIP.exists():
        TMP_ZIP.unlink()
        logOTA(f"Removed old temp ZIP: {TMP_ZIP}")
    urlretrieve(url, TMP_ZIP)
    logOTA(f"ZIP downloaded to: {TMP_ZIP}")


def extract_zip():
    logOTA("Extracting ZIP...")
    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
        logOTA(f"Removed old extract directory: {TMP_EXTRACT}")
    TMP_EXTRACT.mkdir(parents=True, exist_ok=True)
    logOTA(f"Created extract directory: {TMP_EXTRACT}")

    with zipfile.ZipFile(TMP_ZIP, "r") as zf:
        zf.extractall(TMP_EXTRACT)

    new_rpi = TMP_EXTRACT / "RPi"
    if not new_rpi.is_dir():
        raise RuntimeError("ZIP must contain a top-level RPi folder")

    logOTA(f"Extracted RPi folder found at: {new_rpi}")
    return new_rpi


def ignore_special_files(src, names):
    ignored = []
    for name in names:
        full_path = os.path.join(src, name)
        try:
            mode = os.lstat(full_path).st_mode
            if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                logOTA(f"Skipping special file in backup: {full_path}")
                ignored.append(name)
        except Exception as e:
            logOTA(f"Could not inspect {full_path}: {e}")
    return ignored


def upload_ota_status(config, status, message="", version=""):
    try:
        ota_logs = get_ota_logs()

        response = requests.post(
            UPLOAD_URL,
            data={
                "secret": config.get("secret", "GeiseitoFi"),
                "cameraID": config.get("cameraID", ""),
                "deviceID": config.get("deviceID", ""),
                "otaStatus": status,
                "otaMessage": message,
                "otaVersion": version,
                "otaLogs": ota_logs
            },
            timeout=60
        )

        logOTA(f"OTA status upload HTTP {response.status_code}")
        return response.status_code == 200

    except Exception as e:
        logOTA(f"OTA status upload failed: {type(e).__name__}: {e}")
        return False


def run_ota_update(config):
    clear_ota_log()

    try:
        logOTA("Starting OTA update check...")
        meta = fetch_meta()
        remote_version = str(meta.get("version", "0")).strip()
        zip_url = str(meta.get("url", "")).strip()
        local_version = read_local_version()

        logOTA(f"Remote version: {remote_version}")

        if not zip_url:
            logOTA("No ZIP URL found in metadata")
            upload_ota_status(config, "failed", "No ZIP URL found in metadata", remote_version)
            return "failed"

        if remote_version == local_version:
            logOTA("No update available")
            upload_ota_status(config, "no_update", "No update available", remote_version)
            return "no_update"

        logOTA("New update available")
        download_zip(zip_url)
        new_rpi = extract_zip()

        if BACKUP_DIR.exists():
            logOTA(f"Removing old backup: {BACKUP_DIR}")
            shutil.rmtree(BACKUP_DIR)

        if TARGET_DIR.exists():
            logOTA(f"Backing up current RPi folder from {TARGET_DIR} to {BACKUP_DIR}")
            shutil.copytree(TARGET_DIR, BACKUP_DIR, ignore=ignore_special_files)

            logOTA(f"Removing current RPi folder: {TARGET_DIR}")
            shutil.rmtree(TARGET_DIR)

        logOTA(f"Moving new RPi folder into place: {TARGET_DIR}")
        shutil.move(str(new_rpi), str(TARGET_DIR))

        write_local_version(remote_version)
        logOTA("OTA update completed successfully")

        upload_ota_status(config, "success", "OTA update completed successfully", remote_version)
        return "updated"

    except Exception as e:
        logOTA(f"OTA update failed: {type(e).__name__}: {e}")
        upload_ota_status(config, "failed", f"{type(e).__name__}: {e}")
        return "failed"