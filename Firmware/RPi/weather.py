from picamera2 import Picamera2
from PIL import Image
import requests
import RPi.GPIO as GPIO
import time
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

LOG_PATH = "/home/WeatherDevice/Firmware/RPi/Logs/capture.log"
IMAGE_PATH = "/home/WeatherDevice/Firmware/RPi/Images/picture.jpg"
UPLOAD_IMAGE_PATH = "/home/WeatherDevice/Firmware/RPi/Images/out.jpg"
UPLOAD_URL = "https://emea-edu.com/camera1/upload.php"
FTP_DIR = "ftp.metops.net"
FTP_USER = "gedonsoft"
FTP_PWD = "loHtWAkvpDjEC47RzmhjC"
FTP_FOLDER = "upload/camera1"
file_path = "/home/WeatherDevice/Firmware/RPi/out_pipeline.json"

SIGNAL_TO_ESP32 = 23
SHUTDOWN_FROM_ESP32 = 24
SHUTDOWN_COMPLETED = 25
PULSE_TIME = 1
MAX_RETRIES = 3
RETRY_DELAY = 5 #seconds
SSID = ""
PW =""

def trim_log_file(filepath, max_lines):
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return  # Nothing to trim

    # Keep only last max_lines
    lines = lines[-max_lines:]

    # Overwrite file
    with open(filepath, "w") as f:
        f.writelines(lines)


def log_no_time(message, log_path = LOG_PATH):
    with open(log_path, "a") as f:
        f.write(f"{message}\n") 

def get_logs(lines=40):
    try:
        return subprocess.check_output(["tail",f"-n{lines}",LOG_PATH]).decode("utf-8",errors="ignore")
    except Exception as e:
        return f"Error reading logs: {e}"


def sync_time_after_ppp():
    try:
        # Bookworm: systemd-resolved manages resolv.conf and may override pppd's
        # usepeerdns. Write Google DNS directly so name resolution works over PPP.
        subprocess.run(
            ["sudo", "bash", "-c", "echo 'nameserver 8.8.8.8\nnameserver 8.8.4.4' > /etc/resolv.conf"],
            check=True
        )
        subprocess.run(["sudo", "chronyc", "makestep"], check=True)
    except Exception as e:
        logging.error(f"Time sync failed: {e}")



def uploadLogs():
    # Re-apply DNS — resolv.conf may have been reset by systemd-resolved
    # by the time this is called (after waiting for the ESP32 shutdown signal).
    try:
        subprocess.run(
            ["sudo", "bash", "-c", "echo 'nameserver 8.8.8.8\nnameserver 8.8.4.4' > /etc/resolv.conf"],
            check=True
        )
    except Exception as e:
        logging.error(f"DNS refresh failed in uploadLogs: {e}")

    #Upload to FTP with retries
    FTP_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            ftp = FTP(FTP_DIR)
            ftp.login(FTP_USER, FTP_PWD)
            ftp.set_pasv(True)
            ftp.cwd(FTP_FOLDER)

            logs = get_logs()
            data = logs.encode()
            k = BytesIO(data)
            resp = ftp.storbinary('STOR LOGS.log', k)
            k.close()

            if resp.startswith('226'):
                print(f"FTP- Logs Upload Successful on attempt {attempt}")
                FTP_success = True
                break
            else:
                print("Unexpected FTP response")

        except error_perm as e:
            print("Permission or FTP error")
        except Exception as e:
            print(f"Upload Failed {e}")

        finally:
            try:
                ftp.quit()
            except:
                pass

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    # Upload image to HTML with retries
    HTML_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            logs = get_logs()
            response = requests.post(UPLOAD_URL,data={"logs":logs}, timeout=60)
            if response.status_code == 200:
                HTML_success = True
                break

            else:
                print(f"Upload attempt {attempt} failed, HTTP status:{response.status_code}")
        except Exception as e:
                print(f"Upload attempt {attempt} raised exception: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    if not HTML_success:
        with open(LOG_PATH, "a") as f:
            logs = get_logs()
            f.write(f"{logs}\n")



def wait_for_uart(uart, timeout=5):
    start = time.time()

    while time.time() - start < timeout:
        data = uart.receive()
        if data:
            return data
        time.sleep(0.5)

    return None




# --- Pi + upload + GPIO setup + UART Battery Monitoring ---

logging.basicConfig (filename=LOG_PATH, level= logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", force=True, filemode='a')

GPIO.setmode(GPIO.BCM)
GPIO.setup(SIGNAL_TO_ESP32, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(SHUTDOWN_FROM_ESP32, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(SHUTDOWN_COMPLETED, GPIO.OUT, initial=GPIO.HIGH)

uart = UARTComm(port='/dev/serial0', baudrate=9600)

try:
    while True:
        uart.send("SEND VOLTAGE")
        BatteryData = wait_for_uart(uart)


        if BatteryData is None:
            log_no_time("No response from ESP about Battery")
            break
        else:
            log_no_time(f"Battery: {BatteryData}! Safe Battery Levels are 13V - 9V")
            break

    uart.close()
    time.sleep(1)

    ppp_process = sim_ppp.init_connection()
    if ppp_process is None:
        logging.error("No PPP connection. Shutting down")
        print("PPP connection failed. Shutting Down")
        os.system("sudo shutdown now")
    else:
        sync_time_after_ppp()


    # Temp and Humidity
    #try:
        #sensor = SHT3x(bus_id=1, address=0x44)
        #temp, hum = sensor.read_avg(samples=8, delay=0.2)
        #logging.info (f"{temp:.2f}C  {hum:.2f} %H")
    #except Exception as e:
        #logging.info(f"SHT3X Error:{e}")
        #print(f"SHT3X Error:{e}")

    # IR Temperature
    #try:
        #sensor = MLX90614()
        #IR_Temp = str(sensor.readObjectTemperature())
        #logging.info(f"IR Temp:{sensor.readObjectTemperature()}")
        #print(IR_Temp)

    #except Exception as e:
        #logging.info(f"IR Temp Error:{e}")
        #print (e)


    # Capture image
    try:
        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={"size":(720,1280)})
        picam2.configure(config)
        picam2.start()
        picam2.capture_file(IMAGE_PATH)
        picam2.stop()
        picam2.close()

        img = Image.open(IMAGE_PATH)
        img = img.rotate(-90, expand=True)  # clockwise rotation
        img.save(IMAGE_PATH)
        print("Image Captured")
        logging.info("Image captured")

    except Exception as e:
        print(f"Camera Error:{e}") 
        logging.info(f"Camera Error:{e}")
        os.system("sudo shutdown now")
    try:
        subprocess.run(["python3","/home/WeatherDevice/Firmware/RPi/run_pipeline.py", "--input", "/home/WeatherDevice/Firmware/RPi/Images/picture.jpg", "--output", "/home/WeatherDevice/Firmware/RPi/Images/out.jpg", "--save-json", "--output-size", "1280x720"],capture_output= True,text = True, check = True)
        logging.info("Pipeline finished successfully")

    except subprocess.CalledProcessError as e:
        logging.error(f"Pipeline failed with return code {e.returncode}")
        logging.error(f"Pipeline stdout:\n{e.stdout}")
        logging.error(f"Pipeline stderr:\n{e.stderr}")
    
    
    # Appending JSON with temp and battery data
    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        data["Parameters"] = {
            "Charging": BatteryData
        }

        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    
    except Exception as e:
        print (e)
        
    #Uploading FTP
    FTP_success = False
    nameImage = f"Image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    json_name = f"json_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # ── Upload image + JSON (one session) ────────────────────────────────────
    for attempt in range(1, MAX_RETRIES):
        try:
            ftp = FTP(FTP_DIR)
            ftp.login(FTP_USER, FTP_PWD)
            ftp.set_pasv(True)
            ftp.cwd(FTP_FOLDER)

            with open('/home/WeatherDevice/Firmware/RPi/Images/out.jpg', 'rb') as f:
                resp1 = ftp.storbinary(f'STOR images/{nameImage}', f)
            with open('/home/WeatherDevice/Firmware/RPi/out_pipeline.json', 'rb') as j:
                resp2 = ftp.storbinary(f'STOR json/{json_name}', j)

            if resp1.startswith('226') and resp2.startswith('226'):
                print(f"FTP- Image and JSON Upload Successful on attempt {attempt}")
                logging.info(f"FTP- Image and JSON Upload Successful on attempt {attempt}")
                FTP_success = True
                break
            else:
                print("Unexpected FTP response")
                logging.error(f"Unexpected FTP response: {resp1}, {resp2}")

        except error_perm as e:
            print("Permission or FTP error")
            logging.error(f"Permission or FTP error: {e}")
        except Exception as e:
            print(f"Upload Failed {e}")
            logging.error(f"Upload failed: {e}")
        finally:
            try:
                ftp.quit()
            except:
                pass

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    # ── Upload logs (separate session to avoid server connection reset) ───────
    for attempt in range(1, MAX_RETRIES):
        try:
            ftp = FTP(FTP_DIR)
            ftp.login(FTP_USER, FTP_PWD)
            ftp.set_pasv(True)
            ftp.cwd(FTP_FOLDER)

            with open(LOG_PATH, 'rb') as k:
                resp3 = ftp.storbinary('STOR LOGS.log', k)

            if resp3.startswith('226'):
                print(f"FTP- Logs Upload Successful on attempt {attempt}")
                logging.info(f"FTP- Logs Upload Successful on attempt {attempt}")
                break
            else:
                print("Unexpected FTP response for logs")
                logging.error(f"Unexpected FTP response for logs: {resp3}")

        except error_perm as e:
            print("Permission or FTP error (logs)")
            logging.error(f"Permission or FTP error (logs): {e}")
        except Exception as e:
            print(f"Log Upload Failed {e}")
            logging.error(f"Log upload failed: {e}")
        finally:
            try:
                ftp.quit()
            except:
                pass

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)



    # Upload image with retries
    HTML_success = False
    for attempt in range(1, MAX_RETRIES):
        try:
            with open(UPLOAD_IMAGE_PATH, 'rb') as f, open("/home/WeatherDevice/Firmware/RPi/out_pipeline.json", "rb") as j:
                logs = get_logs()
                response = requests.post(UPLOAD_URL, files={"image": f, "jsonfile": j}, timeout=(20,180))
            if response.status_code == 200:
                logging.info(f"HTML- Image and JSON Upload successful on attempt {attempt}!")
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
        logging.error("All HTML and FTP upload attempts failed.SHUTTING DOWN")
        with open(LOG_PATH, "a") as f:
            logs = get_logs()
            f.write(f"{logs}\n")
        os.system("sudo shutdown now")
    
    elif not HTML_success:
        logging.error("All HTML upload attempts failed.")
        with open(LOG_PATH, "a") as f:
            logs = get_logs()
            f.write(f"{logs}\n")
    
    elif not FTP_success:
        logging.error("All FTP upload attempts failed.")
        with open(LOG_PATH, "a") as f:
            logs = get_logs()
            f.write(f"{logs}\n")
        

    #------------------ Pulse ESP32 after successful upload -----------------
    GPIO.output(SIGNAL_TO_ESP32, GPIO.HIGH)
    print("Waiting for ESP32 to ACK....")
    print ("Pi sees pin state:", GPIO.input(SHUTDOWN_FROM_ESP32))
    while GPIO.input(SHUTDOWN_FROM_ESP32) == GPIO.LOW:
        print("Pin state:", GPIO.input(SHUTDOWN_FROM_ESP32))
        time.sleep(0.1)

    GPIO.output(SIGNAL_TO_ESP32, GPIO.LOW)
    print("Signaled ESP32 that image was sent.")
    
    trim_log_file(LOG_PATH, 41)
    # Wait for shutdown
    print("Waiting for shutdown signal from ESP32")
    while True:
        if GPIO.input(SHUTDOWN_FROM_ESP32) == GPIO.HIGH:
            time.sleep(1)
            print("Got signal from ESP32 for shutdown")
            logging.info("Got Signal from ESP32 after sending Image,Uploading Logs and Going to SHUT DOWN")
            logging.info("===================================================================================\n")
            uploadLogs()
            os.system("sudo shutdown now")
        time.sleep(0.5)

except Exception as e:
    print (e)
    logging.critical(f"Error: {e}")
    os.system("sudo shutdown now")




