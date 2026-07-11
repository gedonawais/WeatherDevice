import requests
import json

URL = "https://www.emea-edu.com/camera1/Scripts/versionControl.json"
currentVersionJSON = "versionControl.json"


def getCurrentVersion(currentVersionJSON):
    try:
        with open(currentVersionJSON, 'r') as f:
            data = json.load(f)
            return data["version"]
    except FileNotFoundError:
        print("Current version file not found.")
        return None

def checkForUpdate(url):
    json_data = requests.get(url).json()
    json_data = json_data["version"]
    return json_data

def downloadNewScript(URL):
    try:
        json_data = requests.get(URL).json()
        script_url = json_data["script_url"]
        print ("Downloading new JSON")
        with open("versionControl.json", 'wb') as f:
            f.write(requests.get(URL).content)
            f.close()

        print(f"Downloading new script from: {script_url}")
        with open("weather.py", 'wb') as f:
            f.write(requests.get(script_url).content)
            f.close()
            print("New script downloaded successfully.")

    except Exception as e:
        print(f"Error downloading new script: {e}")



if __name__ == "__main__":
    current_version = getCurrentVersion(currentVersionJSON)
    latest_version = checkForUpdate(URL)
    if current_version != latest_version:
        print(f"New version available: {latest_version}")
        downloadNewScript(URL)
    else:
        print("You are already using the latest version.")
