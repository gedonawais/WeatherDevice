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


def get_remote_file_info(url, timeout=30):
    req = Request(url, method="HEAD")
    with urlopen(req, timeout=timeout) as response:
        headers = response.headers
        content_length = headers.get("Content-Length")
        accept_ranges = headers.get("Accept-Ranges", "")
        total_size = int(content_length) if content_length is not None else None
        range_supported = "bytes" in accept_ranges.lower()

    logOTA(
        f"Remote file info: size={total_size}, range_supported={range_supported}"
    )
    return total_size, range_supported


def download_zip(url, expected_sha256=None, retries=5, timeout=60, chunk_size=1024 * 256):
    logOTA(f"Downloading update ZIP from: {url}")

    tmp_part = Path(str(TMP_ZIP) + ".part")
    last_error = None

    total_size, range_supported = get_remote_file_info(url)

    for attempt in range(1, retries + 1):
        try:
            downloaded_size = tmp_part.stat().st_size if tmp_part.exists() else 0

            if total_size is not None and downloaded_size > total_size:
                logOTA(
                    f"Partial file larger than remote file ({downloaded_size} > {total_size}), restarting"
                )
                tmp_part.unlink(missing_ok=True)
                downloaded_size = 0

            use_resume = range_supported and downloaded_size > 0

            headers = {}
            mode = "wb"

            if use_resume:
                headers["Range"] = f"bytes={downloaded_size}-"
                mode = "ab"
                logOTA(
                    f"Download attempt {attempt}/{retries}: resuming from byte {downloaded_size}"
                )
            else:
                if downloaded_size > 0:
                    logOTA(
                        "Server does not support Range or resume not possible, restarting download from zero"
                    )
                    tmp_part.unlink(missing_ok=True)
                    downloaded_size = 0
                logOTA(f"Download attempt {attempt}/{retries}: starting from zero")

            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", None)

                if use_resume and status != 206:
                    logOTA(
                        f"Server did not honor Range request (HTTP {status}), restarting from zero"
                    )
                    tmp_part.unlink(missing_ok=True)
                    downloaded_size = 0
                    headers = {}
                    req = Request(url, headers=headers)
                    response.close()
                    with urlopen(req, timeout=timeout) as response2:
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
                raise RuntimeError(
                    f"Download incomplete: got {downloaded_size} out of {total_size} bytes"
                )

            if TMP_ZIP.exists():
                TMP_ZIP.unlink()

            os.replace(tmp_part, TMP_ZIP)
            logOTA(f"ZIP downloaded successfully to: {TMP_ZIP}")

            if expected_sha256:
                verify_zip_sha256(TMP_ZIP, expected_sha256)

            return

        except Exception as e:
            last_error = e
            logOTA(f"Download attempt {attempt} failed: {type(e).__name__}: {e}")

            if TMP_ZIP.exists():
                try:
                    TMP_ZIP.unlink()
                except Exception:
                    pass

            if attempt < retries:
                logOTA("Retrying download in 5 seconds...")
                time.sleep(5)

    raise RuntimeError(f"ZIP download failed after {retries} attempts: {last_error}")


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
        raise RuntimeError(
            f"SHA-256 mismatch: expected {expected}, got {actual}"
        )

    logOTA("ZIP SHA-256 verified successfully")


def extract_zip():
    logOTA("Extracting ZIP...")
    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
        logOTA(f"Removed old extract directory: {TMP_EXTRACT}")
    TMP_EXTRACT.mkdir(parents=True, exist_ok=True)
    logOTA(f"Created extract directory: {TMP_EXTRACT}")

    with zipfile.ZipFile(TMP_ZIP, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corrupt ZIP entry detected: {bad_file}")
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


def run_ota_update(config):
    clear_ota_log()

    try:
        logOTA("Starting OTA update check...")
        meta = fetch_meta()
        remote_version = str(meta.get("version", "0")).strip()
        zip_url = str(meta.get("url", "")).strip()
        zip_sha256 = str(meta.get("sha256", "")).strip().lower()
        local_version = read_local_version()

        logOTA(f"Remote version: {remote_version}")

        if not zip_url:
            logOTA("No ZIP URL found in metadata")
            upload_ota_status(config, "failed", "No ZIP URL found in metadata", remote_version)
            return "failed"

        if not zip_sha256:
            logOTA("No SHA-256 found in metadata")
            upload_ota_status(config, "failed", "No SHA-256 found in metadata", remote_version)
            return "failed"

        if remote_version == local_version:
            logOTA("No update available")
            upload_ota_status(config, "no_update", "No update available", remote_version)
            return "no_update"

        logOTA("New update available")

        download_zip(zip_url, expected_sha256=zip_sha256)
        new_rpi = extract_zip()

        if BACKUP_DIR.exists():
            logOTA(f"Removing old backup: {BACKUP_DIR}")
            shutil.rmtree(BACKUP_DIR)

        if TARGET_DIR.exists():
            logOTA(f"Backing up current RPi folder from {TARGET_DIR} to {BACKUP_DIR}")
            shutil.copytree(TARGET_DIR, BACKUP_DIR, ignore=ignore_special_files)

        try:
            if not TARGET_DIR.exists():
                logOTA(f"Creating target directory: {TARGET_DIR}")
                TARGET_DIR.mkdir(parents=True, exist_ok=True)

            logOTA(f"Merging extracted files from {new_rpi} into {TARGET_DIR}")
            merge_tree(new_rpi, TARGET_DIR)

            write_local_version(remote_version)
            logOTA("OTA update completed successfully")

            if BACKUP_DIR.exists():
                logOTA(f"Removing backup after successful install: {BACKUP_DIR}")
                shutil.rmtree(BACKUP_DIR)

            upload_ota_status(config, "success", "OTA update completed successfully", remote_version)
            return "updated"

        except Exception as install_error:
            logOTA(f"Installation failed: {type(install_error).__name__}: {install_error}")
            restore_backup()
            raise

    except Exception as e:
        logOTA(f"OTA update failed: {type(e).__name__}: {e}")
        upload_ota_status(config, "failed", f"{type(e).__name__}: {e}")
        return "failed"