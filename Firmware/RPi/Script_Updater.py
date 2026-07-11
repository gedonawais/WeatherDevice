import json
import os
import shutil
import sys
from urllib.parse import urlparse
import requests

VERSION_JSON_URL = "https://emea-edu.com/camera1/Scripts/versionControl.json"
LOCAL_SCRIPT_PATH = "/home/gedonsoft/Weather/weather.py"      
LOCAL_VERSION_FILE = "/home/gedonsoft/Weather/current_version.txt"
TIMEOUT = 15

def parse_version(v):
    """
    Converts '1.0.3' -> (1,0,3), '2' -> (2,)
    """
    return tuple(int(x) for x in str(v).strip().split("."))

def read_local_version():
    if not os.path.exists(LOCAL_VERSION_FILE):
        return "0"
    with open(LOCAL_VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip() or "0"

def write_local_version(v):
    with open(LOCAL_VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(str(v))

def download_text(url):
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def download_binary(url, dst_tmp):
    with requests.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with open(dst_tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def make_backup(path):
    if os.path.exists(path):
        backup = path + ".bak"
        shutil.copy2(path, backup)
        return backup
    return None

def atomic_replace(src_tmp, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src_tmp, dst)  # atomic on same filesystem

def main():
    try:

        # check for new version
        print("Checking remote version info...")
        raw = download_text(VERSION_JSON_URL)
        meta = json.loads(raw)

        remote_version = str(meta["version"]).strip()
        script_url = meta["script_url"].strip()

        # check for existing version
        local_version = read_local_version()
        print(f"Local version : {local_version}")
        print(f"Remote version: {remote_version}")

        if parse_version(remote_version) <= parse_version(local_version):
            print("No update available.")
            return 0

        print("New version found. Downloading script...")

        # download to temp file first
        tmp_path = LOCAL_SCRIPT_PATH + ".tmp"
        download_binary(script_url, tmp_path)

        # backup current script
        backup = make_backup(LOCAL_SCRIPT_PATH)
        if backup:
            print(f"Backup created: {backup}")

        # replace old script with new
        atomic_replace(tmp_path, LOCAL_SCRIPT_PATH)


        # save new version
        write_local_version(remote_version)

        print(f"Update complete. Now running version {remote_version}.")
        return 0

    except Exception as e:
        print(f"Update failed: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())