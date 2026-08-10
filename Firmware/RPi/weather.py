from picamera2 import Picamera2
from PIL import Image
import requests
import RPi.GPIO as GPIO
import time
import paramiko
from datetime import datetime
from ftplib import FTP, error_perm
from pathlib import Path
import os
import json
import numpy as np
import onnxruntime as ort
import subprocess
import logging
import sim_ppp
from uart_comm import UARTComm
from io import BytesIO
import socket
import sys  


BASE_DIR = "/home/WeatherDevice/Firmware/RPi"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "Logs/capture.log")
IMAGE_PATH = os.path.join(BASE_DIR, "Images/picture.jpg")
OUTPUT_IMAGE_PATH = os.path.join(BASE_DIR, "Images/out.jpg")
PIPELINE_JSON = os.path.join(BASE_DIR, "out_pipeline.json")

UPLOAD_URL = "https://emea-edu.com/cameraDashboard/upload.php"



SIGNAL_TO_ESP32 = 23
SHUTDOWN_FROM_ESP32 = 24
SHUTDOWN_COMPLETED = 25
PULSE_TIME = 1
MAX_RETRIES = 3
RETRY_DELAY = 5 #seconds
FPS = 20  # Default frame rate

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    filemode="a"
)

logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("picamera2").setLevel(logging.WARNING)
logging.getLogger("libcamera").setLevel(logging.WARNING)


def log_no_time(message, log_path = LOG_PATH):
    with open(log_path, "a") as f:
        f.write(f"{message}\n") 


def mark_session_start():
    with open(LOG_PATH, "a") as f:
        f.write(f"=== SESSION START ===\n")


def get_logs():
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()

        starts = [i for i, line in enumerate(lines) if "=== SESSION START" in line]

        if len(starts) >= 2:
            return "".join(lines[starts[-2]:])
        elif len(starts) == 1:
            return "".join(lines[starts[-1]:])
        else:
            return "".join(lines[-50:])
    except Exception as e:
        return f"Error reading logs: {e}"


def keep_last_two_sessions():
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()

        starts = [i for i, line in enumerate(lines) if "=== SESSION START" in line]

        if len(starts) >= 3:
            lines = lines[starts[-2]:]

        with open(LOG_PATH, "w") as f:
            f.writelines(lines)

    except Exception as e:
        print(f"Error trimming log file: {e}")


def reinit_logging():
    """Re-initialize logging after time sync so all future timestamps are correct."""
    logging.shutdown()
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", force=True, filemode='a')


def sync_time_after_ppp():
    try:
        time.sleep(1)

        result = subprocess.run(
            ["chronyc", "waitsync", "20"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30
        )
        result2 = subprocess.run(
            ["chronyc", "makestep"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15
        )

        if result.returncode == 0 and result2.returncode == 0:
            logging.info("Time sync successful")
        else:
            logging.error("Time sync failed")

    except subprocess.TimeoutExpired:
        logging.error("Time sync timed out")
    except Exception as e:
        logging.error(f"Time sync failed: {e}")
    finally:
        reinit_logging()


def uploadLogs(config):
    logs = get_logs()
    #Upload to FTP with retries
    FTP_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            resp = upload_log(config,logs,"Capture.log")
            if config.get("protocol", "ftp").lower() == "sftp":
                print(f"SFTP- Logs Upload Successful on attempt {attempt}")
                logging.info(f"SFTP- Logs Upload Successful on attempt {attempt}")
                FTP_success = True
                break

            else:
                if resp.startswith('226'):
                    print(f"FTP- Logs Upload Successful on attempt {attempt}")
                    logging.info(f"FTP- Logs Upload Successful on attempt {attempt}")
                    FTP_success = True
                    break
                else:
                    print("Unexpected FTP response")
                    logging.error(f"Unexpected FTP response: {resp}")

        except error_perm as e:
            print(f"Upload Failed {type(e).__name__}: {e}")
            logging.error(f"Upload failed: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"Upload Failed {type(e).__name__}: {e}")
            logging.error(f"Upload failed: {type(e).__name__}: {e}")


        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    # Upload image to HTML with retries
    HTML_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            response = requests.post(UPLOAD_URL,
                                     data={
                                         "secret": config.get("secret", "GeiseitoFi"),
                                         "cameraID": config.get("cameraID", ""),
                                         "deviceID": config.get("deviceID", ""),
                                         "logs":logs}, timeout=60)
            if response.status_code == 200:
                HTML_success = True
                break

            else:
                print(f"Upload attempt {attempt} failed, HTTP status:{response.status_code}")
        except Exception as e:
                print(f"Upload attempt {attempt} raised exception: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)


def wait_for_uart(uart, timeout=5):
    start = time.time()

    while time.time() - start < timeout:
        data = uart.receive()
        if data:
            return data
        time.sleep(0.5)

    return None


def parse_config_line(data):
    parts = data.strip().split('|')
    if len(parts) != 9:
        raise ValueError(f"Expected 9 parts in config line, got {len(parts)}: {data}")
    cameraID, location, ftpHost, ftpPort, ftpUser, ftpPass, ftpPath, protocol, framRate = parts
    return {
        "cameraID": cameraID.strip(),
        "location": location.strip(),
        "ftpHost": ftpHost.strip(),
        "ftpPort": int(ftpPort.strip()),
        "ftpUser": ftpUser.strip(),
        "ftpPass": ftpPass.strip(),
        "ftpPath": ftpPath.strip(),
        "protocol": protocol.strip().lower(),
        "frameRate": int(framRate.strip())
    }


def save_config(config, path=CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(config, f, indent=4)

def get_device_id():
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.split(':')[1].strip()
    except:
        pass
    # Fallback to eth0 MAC
    try:
        with open('/sys/class/net/eth0/address', 'r') as f:
            return f.read().strip().replace(':', '')
    except:
        pass
    return None

def load_config(path=CONFIG_PATH):
    device_id = get_device_id()

    if not os.path.exists(path):
        default_config = {
            "cameraID": "", 
            "location": "", 
            "ftpHost": "",
            "ftpPort": 21,  
            "ftpUser": "", 
            "ftpPass": "",
            "ftpPath": "/", 
            "protocol": "ftp", 
            "frameRate": 20,
            "secret": "GeiseitoFi",
            "deviceID": device_id
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(default_config, f, indent=4)
        return default_config

    try:
        with open(path) as f:
            config = json.load(f)
        return config
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        os.remove(path)
        return load_config(path)  # Retry loading after deletion



def receive_and_save_config(line, path=CONFIG_PATH):
    config = parse_config_line(line)
    config["deviceID"] = get_device_id()
    config["secret"] = "GeiseitoFi"  # Ensure secret is always set
    save_config(config, path)
    return config


def wait_for_internet(host="8.8.8.8", dns_host="google.com", retries=10, delay=1):
    for _ in range(retries):
        ip_ok = subprocess.run(
            ["ping", "-c", "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ).returncode == 0

        dns_ok = False
        try:
            socket.gethostbyname(dns_host)
            dns_ok = True
        except socket.gaierror:
            dns_ok = False

        if ip_ok and dns_ok:
            return True

        time.sleep(delay)
    return False


def upload_file(config, local_path, remote_path):
    protocol = config.get("protocol", "ftp").lower()

    if protocol == "sftp":
        transport = paramiko.Transport((config["ftpHost"], config["ftpPort"]))
        transport.connect(username=config["ftpUser"], password=config["ftpPass"])
        sftp = paramiko.SFTPClient.from_transport(transport)

        try:
            full_remote_path = f"{config['ftpPath'].rstrip('/')}/{remote_path}"
            sftp.put(local_path, full_remote_path)
        finally:
            sftp.close()
            transport.close()
        return True

    else:
        ftp = None
        try:
            ftp = FTP(timeout=60)
            ftp.connect(config["ftpHost"], config["ftpPort"])
            ftp.login(config["ftpUser"], config["ftpPass"])
            ftp.set_pasv(True)
            ftp.cwd(config["ftpPath"])

            with open(local_path, "rb") as f:
                resp = ftp.storbinary(f"STOR {remote_path}", f)

        finally:
            if ftp:
                try:
                    ftp.quit()
                except: 
                    pass
        return resp
    



def upload_log(config, log_text, remote_name="Capture.log"):
    protocol = config.get("protocol", "ftp").lower()

    if protocol == "sftp":
        transport = paramiko.Transport((config["ftpHost"], config["ftpPort"]))
        transport.connect(username=config["ftpUser"], password=config["ftpPass"])
        sftp = paramiko.SFTPClient.from_transport(transport)

        try:
            full_remote_path = f"{config['ftpPath'].rstrip('/')}/{remote_name}"
            with sftp.open(full_remote_path, "w") as remote_file:
                remote_file.write(log_text)

        finally:
            sftp.close()
            transport.close()
        return True

    else:
        ftp = None
        try:
            ftp = FTP(timeout=60)
            ftp.connect(config["ftpHost"], config["ftpPort"])
            ftp.login(config["ftpUser"], config["ftpPass"])
            ftp.set_pasv(True)
            ftp.cwd(config["ftpPath"])

            bio = BytesIO(log_text.encode())
            resp = ftp.storbinary(f"STOR {remote_name}", bio)
            bio.close()
            return resp
        
        finally:
            if ftp:
                try:    
                    ftp.quit()
                except:
                    pass


def safeShutdown(reason=""):
    if reason:
        logging.error(f"Safe Shutdown initiated due to: {reason}")

    try:
        GPIO.cleanup()
    except:
        pass
    os.system("sudo shutdown now")
    sys.exit(0)



def getFrameRate(config):
    try:
        response= requests.post (
            UPLOAD_URL, data = {
            "secret": config.get("secret", "GeiseitoFi"),
            "cameraID" : config.get("cameraID", ""),
            "deviceID" : config.get("deviceID", ""),
            "getFrameRate" : 1
            },
            timeout=5
        )

        if response.status_code == 200:
            return response.json().get("frameRate")

    except Exception as e:
        print(f"Error occurred: {e}")
        return None

#--------------------------------------------- Main Execution ------------------------------------------

GPIO.setmode(GPIO.BCM)
GPIO.setup(SIGNAL_TO_ESP32, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(SHUTDOWN_FROM_ESP32, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(SHUTDOWN_COMPLETED, GPIO.OUT, initial=GPIO.HIGH)

uart = UARTComm(port='/dev/serial0', baudrate=9600)
mark_session_start()
config = load_config()

try:
    BatteryData = "N/A"

    try:
        #------------------ UART Communication with ESP32 -----------------

        #------------------ Request Battery Voltage -----------------
        uart.send("SEND VOLTAGE\n")
        BatteryData = wait_for_uart(uart)

        if BatteryData is None:
            log_no_time("No response from ESP about Battery")
        else:
            log_no_time(f"Battery: {BatteryData}! Safe Battery Levels are 13V - 9V")

        time.sleep(1)

        #------------------ Request Config Data -----------------
        uart.send("SEND CONFIG\n")
        config_data = wait_for_uart(uart)

        if config_data is None:
            log_no_time("No response from ESP about Config")
            config = load_config()

        elif config_data == "NO NEW CONFIG":
            log_no_time("No new config data")
            print ("No new config data")
            config = load_config()
        else:
            config = receive_and_save_config(config_data)
            uart.send("CONFIG SAVED\n")
            log_no_time ("Config received and saved")
        
        #------------------ Send Frame Rate to ESP32 -----------------
        uart.send(f"SET FRAMERATE {config.get('frameRate')}\n")
        ack = wait_for_uart(uart)
        if ack == "FRAMERATE UPDATED":
            log_no_time(f"Frame rate {config.get('frameRate')} sent to ESP32 successfully")
        else:
            log_no_time(f"Failed to send frame rate to ESP32. Response: {ack}")

        
        uart.close()
        time.sleep(1)  #ensure UART is closed before proceeding

    except Exception as e:
        print(f"UART Error: {e}")
        logging.error(f"UART Error: {e}")



    #------------------ Initialize PPP Connection -----------------
    ppp_process = sim_ppp.init_connection()
    if ppp_process is None:
        logging.error("No PPP connection. Shutting down")
        print("PPP connection failed. Shutting Down")
        keep_last_two_sessions()
        safeShutdown("PPP connection failed")
    else:
        if wait_for_internet():
            sync_time_after_ppp()  # syncs clock AND re-inits logging with correct timestamps
        else:
            logging.error("Internet not available after PPP.")
            print("Internet not available after PPP.")


    #------------------- Get Frame Rate from Server -----------------
    newFrameRate = getFrameRate(config)
    if newFrameRate is not None and newFrameRate!= config.get("frameRate"):
        config["frameRate"] = newFrameRate
        save_config(config)
        logging.info(f"Frame rate updated to {newFrameRate} from server")
    else:
        logging.info(f"Frame rate remains {config.get('frameRate')}")


    #------------------ Capture Image -----------------
    try:
        print("Capturing image...")
        picam2 = Picamera2()
        camera_config = picam2.create_still_configuration(main={"size":(1280,720)})
        picam2.configure(camera_config)
        picam2.start()
        picam2.capture_file(IMAGE_PATH)
        picam2.stop()
        picam2.close()

        print("Image Captured")
        logging.info("Image captured")

    except Exception as e:
        print(f"Camera Error:{e}") 
        logging.error(f"Camera Error:{e}")
        keep_last_two_sessions()
        safeShutdown("Camera Error")
    try:
        subprocess.run(
            [
            "python3",
            os.path.join(BASE_DIR, "run_pipeline.py"),
            "--input", IMAGE_PATH,
            "--output", OUTPUT_IMAGE_PATH,
            "--weather-onnx", os.path.join(BASE_DIR, "weathernet.onnx"),
            "--classes", os.path.join(BASE_DIR, "class_to_idx.json"),
            "--yolox-onnx", os.path.join(BASE_DIR, "model.onnx"),
            "--yolox-classes", os.path.join(BASE_DIR, "classes.txt"),
            ],
        check=True,
        capture_output=True,
        text=True,
        )
        logging.info("Pipeline finished successfully")

    except subprocess.CalledProcessError as e:
        logging.error(f"Pipeline failed with return code {e.returncode}")
        logging.error(f"Pipeline stdout:\n{e.stdout}")
        logging.error(f"Pipeline stderr:\n{e.stderr}")


    # Appending JSON with battery data
    try:
        with open(PIPELINE_JSON, "r") as f:
            data = json.load(f)

        data["Parameters"] = {
            "Charging": BatteryData
        }

        with open(PIPELINE_JSON, "w") as f:
            json.dump(data, f, indent=4)

    except Exception as e:
        print (e)

    #Uploading FTP
    FTP_success = False
    nameImage = f"Image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    json_name = f"json_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    logging.info(
    f"FTP config: protocol={config.get('protocol')}, "
    f"host={config.get('ftpHost')}, "
    f"port={config.get('ftpPort')}, "
    f"user={config.get('ftpUser')}, "
    f"path={config.get('ftpPath')}"
    )

    for attempt in range(1, MAX_RETRIES):
        try:
            logging.info(f"Uploading image via {config.get('protocol', 'ftp')}: images/{nameImage}")
            resp1 = upload_file(config, OUTPUT_IMAGE_PATH, f'images/{nameImage}')

            logging.info(f"Uploading json via {config.get('protocol', 'ftp')}: json/{json_name}")
            resp2 = upload_file(config, PIPELINE_JSON, f'json/{json_name}')

            if config.get("protocol", "ftp").lower() == "sftp":
                print(f"SFTP- Image and JSON Upload Successful on attempt {attempt}")
                logging.info(f"SFTP- Image and JSON Upload Successful on attempt {attempt}")
                FTP_success = True
                break

            else:
                if resp1.startswith('226') and resp2.startswith('226'):
                    print(f"FTP- Image and JSON Upload Successful on attempt {attempt}")
                    logging.info(f"FTP- Image and JSON Upload Successful on attempt {attempt}")
                    FTP_success = True
                    break
                else:
                    print("Unexpected FTP response")
                    logging.error(f"Unexpected FTP response: {resp1}, {resp2}")


        except error_perm as e:
            print(f"Upload Failed {type(e).__name__}: {e}")
            logging.error(f"Upload failed: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"Upload Failed {type(e).__name__}: {e}")
            logging.error(f"Upload failed: {type(e).__name__}: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)


    # Upload image with retries
    HTML_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            with open(OUTPUT_IMAGE_PATH, 'rb') as f:
                response = requests.post(UPLOAD_URL,

                                         data={
                                             "secret": config.get("secret", "GeiseitoFi"),
                                             "cameraID": config.get("cameraId", ""),
                                             "deviceID": config.get("deviceID", ""),
                                             "location": config.get("location", ""),
                                             "frameRate": config.get("frameRate", 20),
                                             "battery": BatteryData, }, 

                                         files={
                                             "image": f, 
                                             }, 

                                        timeout=(20,180))
                
            if response.status_code == 200:
                print (f"HTML- Image Upload successful on attempt {attempt}")
                logging.info(f"HTML- Image Upload successful on attempt {attempt}")
                HTML_success = True

                break

            else:
                logging.error(f"Upload attempt {attempt} failed, HTTP status:{response.status_code}")
                print(f"Upload attempt {attempt} failed, HTTP status:{response.status_code}")
        except Exception as e:
                logging.error(f"Upload attempt {attempt} raised exception: {e}")
                print(f"Upload attempt {attempt} raised exception: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    if not HTML_success and not FTP_success:
        logging.error(f"All HTML and {config.get('protocol', 'ftp').upper()} upload attempts failed. SHUTTING DOWN")
        keep_last_two_sessions()   
        safeShutdown("All HTML and FTP upload attempts failed")

    elif not HTML_success:
        logging.error("All HTML upload attempts failed.")

    elif not FTP_success:
        logging.error(f"All {config.get('protocol', 'ftp').upper()} upload attempts failed.")


    #------------------ Pulse ESP32 after successful upload -----------------
    GPIO.output(SIGNAL_TO_ESP32, GPIO.HIGH)
    print("Waiting for ESP32 to ACK....")
    ack_start = time.time()
    ACK_TIMEOUT = 60  # seconds

    while GPIO.input(SHUTDOWN_FROM_ESP32) == GPIO.LOW:
        if time.time() -ack_start > ACK_TIMEOUT:
            logging.error("Timeout waiting for ACK from ESP32. Shutting down.")
            break 
        time.sleep(0.1)

    GPIO.output(SIGNAL_TO_ESP32, GPIO.LOW)
    print("Signaled ESP32 that image was sent.")

    # Wait for shutdown
    print("Waiting for shutdown signal from ESP32")
    shutdown_wait_start = time.time()
    SHUTDOWN_WAIT_TIMEOUT = 60  # seconds
    while True:
        if GPIO.input(SHUTDOWN_FROM_ESP32) == GPIO.HIGH:
            time.sleep(1)
            print("Got signal from ESP32 for shutdown")
            logging.info("Got Signal from ESP32 after sending Image,Uploading Logs and Going to SHUT DOWN")
            logging.info("===================================================================================\n")
            uploadLogs(config)

            #Close ppp connection
            try:
                sim_ppp.close_connection(ppp_process)
            except Exception as e:
                logging.error(f"Error closing PPP connection: {e}")

            keep_last_two_sessions()
            time.sleep(1)
            safeShutdown("Received shutdown signal from ESP32")
            break

        if time.time() - shutdown_wait_start > SHUTDOWN_WAIT_TIMEOUT:
            logging.error("Timeout waiting for shutdown signal from ESP32. Shutting Down anyway.")
            uploadLogs(config)
            keep_last_two_sessions()
            time.sleep(1)    
            safeShutdown("Timeout waiting for shutdown signal from ESP32")
            break

        time.sleep(0.5)

except Exception as e:
    print (e)
    logging.critical(f"Error: {e}")
    keep_last_two_sessions()
    safeShutdown(f"Unhandled Exception: {e}")
