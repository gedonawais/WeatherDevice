import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen
import requests

BASE_URL = "https://emea-edu.com/cameraDashboard/OTA_Updates"
UPLOAD_URL = "https://emea-edu.com/cameraDashboard/upload.php"

BASE_DIR = Path("/home/WeatherDevice/Firmware")
OTA_LOG_PATH = BASE_DIR / "ota.log"
TARGET_DIR = BASE_DIR / "RPi"
BACKUP_DIR = BASE_DIR / "RPi_backup"
VERSION_FILE = BASE_DIR / "version.txt"

TMP_EXTRACT = Path(tempfile.gettempdir()) / "rpi_update_extract"

PENDING_DIR = BASE_DIR / "ota_pending"
PENDING_ZIP = PENDING_DIR / "pending_update.zip"
PENDING_META = PENDING_DIR / "pending_meta.json"


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


def fetch_json(url):
    logOTA(f"Fetching OTA metadata from: {url}")
    with urlopen(url, timeout=30) as response:
        meta = json.loads(response.read().decode("utf-8"))
    logOTA(f"Remote metadata: {meta}")
    return meta


def fetch_meta_for_device(config):
    device_id = str(config.get("deviceID", "")).strip()

    if device_id:
        device_meta_url = f"{BASE_URL}/{device_id}/meta.json"
        try:
            logOTA(f"Trying device-specific OTA metadata: {device_meta_url}")
            return fetch_json(device_meta_url)
        except Exception as e:
            logOTA(f"No device-specific OTA metadata found: {type(e).__name__}: {e}")

    global_meta_url = f"{BASE_URL}/all/meta.json"
    logOTA(f"Falling back to global OTA metadata: {global_meta_url}")
    return fetch_json(global_meta_url)


def get_remote_file_info(url, timeout=30):
    req = Request(url, method="HEAD")
    with urlopen(req, timeout=timeout) as response:
        headers = response.headers
        content_length = headers.get("Content-Length")
        accept_ranges = headers.get("Accept-Ranges", "")
        total_size = int(content_length) if content_length is not None else None
        range_supported = "bytes" in accept_ranges.lower()

    logOTA(f"Remote file info: size={total_size}, range_supported={range_supported}")
    return total_size, range_supported


def verify_zip_sha256(zip_path, expected_sha256):
    expected = expected_sha256.strip().lower()
    logOTA(f"Verifying ZIP SHA-256: {zip_path}")

    sha256 = hashlib.sha256()
    with open(zip_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)

    actual = sha256.hexdigest().lower()
    logOTA(f"Calculated ZIP SHA-256: {actual}")

    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch: expected {expected}, got {actual}")

    logOTA("ZIP SHA-256 verified successfully")


def download_zip(url, dest_path, expected_sha256=None, retries=5, timeout=60, chunk_size=1024 * 256):
    logOTA(f"Downloading update ZIP from: {url}")

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_part = Path(str(dest_path) + ".part")
    last_error = None

    total_size, range_supported = get_remote_file_info(url)

    for attempt in range(1, retries + 1):
        try:
            downloaded_size = tmp_part.stat().st_size if tmp_part.exists() else 0

            if total_size is not None and downloaded_size > total_size:
                logOTA(f"Partial file larger than remote file ({downloaded_size} > {total_size}), restarting")
                tmp_part.unlink(missing_ok=True)
                downloaded_size = 0

            use_resume = range_supported and downloaded_size > 0
            headers = {}
            mode = "wb"

            if use_resume:
                headers["Range"] = f"bytes={downloaded_size}-"
                mode = "ab"
                logOTA(f"Download attempt {attempt}/{retries}: resuming from byte {downloaded_size}")
            else:
                if downloaded_size > 0:
                    logOTA("Server does not support Range or resume not possible, restarting download from zero")
                    tmp_part.unlink(missing_ok=True)
                    downloaded_size = 0
                logOTA(f"Download attempt {attempt}/{retries}: starting from zero")

            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", None)

                if use_resume and status != 206:
                    logOTA(f"Server did not honor Range request (HTTP {status}), restarting from zero")
                    tmp_part.unlink(missing_ok=True)
                    downloaded_size = 0
                    with urlopen(Request(url), timeout=timeout) as response2:
                        with open(tmp_part, "wb") as f:
                            while True:
                                chunk = response2.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded_size += len(chunk)
                else:
                    with open(tmp_part, mode) as f:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded_size += len(chunk)

            logOTA(f"Downloaded {downloaded_size} bytes so far")

            if total_size is not None and downloaded_size != total_size:
                raise RuntimeError(f"Download incomplete: got {downloaded_size} out of {total_size} bytes")

            if dest_path.exists():
                dest_path.unlink()

            os.replace(tmp_part, dest_path)
            logOTA(f"ZIP downloaded successfully to: {dest_path}")

            if expected_sha256:
                verify_zip_sha256(dest_path, expected_sha256)

            return

        except Exception as e:
            last_error = e
            logOTA(f"Download attempt {attempt} failed: {type(e).__name__}: {e}")

            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass

            if attempt < retries:
                logOTA("Retrying download in 5 seconds...")
                time.sleep(5)

    raise RuntimeError(f"ZIP download failed after {retries} attempts: {last_error}")


def extract_zip(zip_path):
    logOTA("Extracting ZIP...")
    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
        logOTA(f"Removed old extract directory: {TMP_EXTRACT}")
    TMP_EXTRACT.mkdir(parents=True, exist_ok=True)
    logOTA(f"Created extract directory: {TMP_EXTRACT}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corrupt ZIP entry detected: {bad_file}")
        zf.extractall(TMP_EXTRACT)

    new_rpi = TMP_EXTRACT / "RPi"
    if new_rpi.is_dir():
        logOTA(f"Extracted RPi folder found at: {new_rpi}")
        return new_rpi

    extracted_items = list(TMP_EXTRACT.iterdir())
    if not extracted_items:
        raise RuntimeError("ZIP is empty")

    logOTA("No top-level RPi folder found, using ZIP root as update source")
    return TMP_EXTRACT


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


def restore_backup():
    if BACKUP_DIR.exists():
        if TARGET_DIR.exists():
            logOTA(f"Removing failed target directory: {TARGET_DIR}")
            shutil.rmtree(TARGET_DIR)
        logOTA(f"Restoring backup from {BACKUP_DIR} to {TARGET_DIR}")
        shutil.move(str(BACKUP_DIR), str(TARGET_DIR))
        logOTA("Backup restored successfully")
    else:
        logOTA("No backup available to restore")


def merge_tree(src, dst):
    src = Path(src)
    dst = Path(dst)

    for item in src.iterdir():
        src_item = src / item.name
        dst_item = dst / item.name

        if src_item.is_dir():
            dst_item.mkdir(parents=True, exist_ok=True)
            merge_tree(src_item, dst_item)
        else:
            dst_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_item, dst_item)
            logOTA(f"Copied file: {src_item} -> {dst_item}")


def has_pending_ota():
    return PENDING_ZIP.exists() and PENDING_META.exists()


def save_pending_meta(meta):
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    with open(PENDING_META, "w") as f:
        json.dump(meta, f, indent=4)


def load_pending_meta():
    if not PENDING_META.exists():
        return None
    try:
        with open(PENDING_META, "r") as f:
            return json.load(f)
    except Exception:
        return None


def clear_pending_ota():
    try:
        if PENDING_DIR.exists():
            shutil.rmtree(PENDING_DIR)
            logOTA("Cleared pending OTA")
    except Exception as e:
        logOTA(f"Failed to clear pending OTA: {e}")


def apply_pending_ota(config):
    if not has_pending_ota():
        return "no_pending"

    clear_ota_log()

    try:
        meta = load_pending_meta()
        if not meta:
            raise RuntimeError("Pending OTA metadata missing")

        pending_version = str(meta.get("version", "")).strip()
        pending_sha256 = str(meta.get("sha256", "")).strip().lower()

        if not pending_version:
            raise RuntimeError("Pending OTA version missing")

        verify_zip_sha256(PENDING_ZIP, pending_sha256)
        new_rpi = extract_zip(PENDING_ZIP)

        if BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)

        if TARGET_DIR.exists():
            logOTA(f"Backing up current RPi folder from {TARGET_DIR} to {BACKUP_DIR}")
            shutil.copytree(TARGET_DIR, BACKUP_DIR, ignore=ignore_special_files)

        try:
            if not TARGET_DIR.exists():
                TARGET_DIR.mkdir(parents=True, exist_ok=True)

            logOTA(f"Applying pending OTA from {new_rpi} into {TARGET_DIR}")
            merge_tree(new_rpi, TARGET_DIR)
            write_local_version(pending_version)

            if BACKUP_DIR.exists():
                shutil.rmtree(BACKUP_DIR)

            clear_pending_ota()
            upload_ota_status(config, "success", "OTA applied successfully", pending_version)
            return "updated"

        except Exception as install_error:
            logOTA(f"Installation failed: {type(install_error).__name__}: {install_error}")
            restore_backup()
            raise

    except Exception as e:
        logOTA(f"Apply pending OTA failed: {type(e).__name__}: {e}")
        upload_ota_status(config, "failed", f"{type(e).__name__}: {e}")
        return "failed"


def check_and_download_ota(config):
    clear_ota_log()

    try:
        logOTA("Starting OTA update check...")
        meta = fetch_meta_for_device(config)
        remote_version = str(meta.get("version", "0")).strip()
        zip_url = str(meta.get("url", "")).strip()
        zip_sha256 = str(meta.get("sha256", "")).strip().lower()
        local_version = read_local_version()

        logOTA(f"Remote version: {remote_version}")

        if not zip_url:
            upload_ota_status(config, "failed", "No ZIP URL found in metadata", remote_version)
            return "failed"

        if not zip_sha256:
            upload_ota_status(config, "failed", "No SHA-256 found in metadata", remote_version)
            return "failed"

        if remote_version == local_version:
            upload_ota_status(config, "no_update", "No update available", remote_version)
            return "no_update"

        pending = load_pending_meta()
        if pending and str(pending.get("version", "")).strip() == remote_version and PENDING_ZIP.exists():
            upload_ota_status(config, "pending_update", "Update already downloaded", remote_version)
            return "pending_update"

        download_zip(zip_url, PENDING_ZIP, expected_sha256=zip_sha256)

        save_pending_meta({
            "version": remote_version,
            "sha256": zip_sha256,
            "url": zip_url,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S")
        })

        upload_ota_status(config, "pending_update", "Update downloaded and pending install", remote_version)
        return "pending_update"

    except Exception as e:
        logOTA(f"OTA download failed: {type(e).__name__}: {e}")
        upload_ota_status(config, "failed", f"{type(e).__name__}: {e}")
        return "failed"