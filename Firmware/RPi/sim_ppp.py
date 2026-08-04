import RPi.GPIO as GPIO
import time
import subprocess
import serial
import logging
import os

logging.basicConfig(level=logging.INFO)

PWRKEY_PIN = 17
REG_PIN = 27
SERIAL_PORT = "/dev/serial0"
BAUDRATE = 115200
MAX_PPP_RETRIES = 3
CFUN_REBOOT_WAIT = 10  # seconds for module to restart


def log_no_time(message, log_path = "/home/WeatherDevice/Firmware/RPi/Logs/capture.log"):
    with open(log_path, "a") as f:
        f.write(f"{message}\n")

def ensure_dns():
    try:
        result = subprocess.run(
            ["sudo", "cp", "/etc/ppp/resolv.conf", "/etc/resolv.conf"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("Copied /etc/ppp/resolv.conf to /etc/resolv.conf")
            log_no_time("Copied /etc/ppp/resolv.conf to /etc/resolv.conf")
            return True
        else:
            print(f"Failed to copy DNS config: {result.stderr}")
            log_no_time(f"Failed to copy DNS config: {result.stderr}")
            return False
    except Exception as e:
        log_no_time(f"Failed to copy DNS config: {e}")
        return False
    

def power_on_sim():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PWRKEY_PIN, GPIO.OUT)
    GPIO.setup(REG_PIN, GPIO.OUT)
    print("Turning on Regulator")
    GPIO.output(REG_PIN,GPIO.HIGH)
    time.sleep(1)
    print("Turning SIM7070 on")
    GPIO.output(PWRKEY_PIN, GPIO.HIGH)
    time.sleep(1)
    GPIO.output(PWRKEY_PIN, GPIO.LOW)
    print("Waiting for SIM7070G to boot")
    time.sleep(5)  # adjust as needed

def check_sim_at(timeout=10):
    print("Checking SIM7070 via AT command")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
        start_time = time.time()
        while time.time() - start_time < timeout:
            ser.reset_input_buffer()
            ser.write(b'AT\r')
            time.sleep(0.5)
            response = ser.read_all().decode(errors='ignore')
            if "OK" in response and checkConnection():
                ser.close()
                print("SIM7070 is alive")
                log_no_time("SIM7070G is alive")
                return True
        ser.close()
        print("No response from SIM7070")
        log_no_time("No response from SIM7070G")
        return False
    except Exception as e:
        print("AT check failed")
        return False

def set_cmnb_mode():
    try:
        print("Setting CMNB mode")
        log_no_time("Setting CMNB mode")
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)
        ser.write(b'AT+CMNB=3\r\n')
        time.sleep(1)
        response = ser.read_all().decode(errors='ignore')
        ser.close()
        print(response)
    except Exception as e:
        print("Failed to set CMNB mode")
        log_no_time(f"Failed to set CMNB mode: {e}")


def reset_sim7070():
    """Send CFUN reset to SIM7070G."""
    print("Resetting SIM7070G with AT+CFUN=1,1...")
    log_no_time("Resetting SIM7070G with AT+CFUN=1,1...")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)
        ser.write(b'AT+CFUN=1,1\r\n')
        time.sleep(1)
        resp = ser.read_all().decode(errors='ignore')
        ser.close()

        print(f"Waiting {CFUN_REBOOT_WAIT}s for module to reboot...")
        time.sleep(CFUN_REBOOT_WAIT)
    except Exception as e:
        print("Failed to send CFUN reset")
        log_no_time(f"Failed to send CFUN reset: {e}")


def checkConnection():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)
        ser.write(b'AT+CSQ\r\n')
        time.sleep(1)
        raw = ser.read_all().decode('utf-8',errors='ignore')
        ser.close()
        if "+CSQ:" in raw:
            value = raw.split("+CSQ:")[1].split(",")[0].strip()
            csq = int (value)
            if csq<10:
                status = "Bad, CSQ is <10"
            elif csq<15:
                status = "Weak, CSQ is <15"
            elif csq<21:
                status = "OK, CSQ is <21"
            elif csq <31:
                status = "Good, CSQ is <31"
            else:
                status = "Excellent"
            result = f"CSQ:{csq} ({status})"
        else:
            result = "NO Data about CSQ"
            
        log_no_time(result)
        return True
        
    except Exception as e:
        print (e)
    
    
def ensure_serial_free():
    subprocess.run(["sudo", "killall", "pppd"])
    subprocess.run(["sudo", "systemctl", "stop", "serial-getty@ttyS0.service"])
    subprocess.run(["sudo", "rm", "-f", "/var/lock/LCK..ttyS0"])

def start_ppp():
    print("Starting PPP...")
    log_no_time("Starting PPP...")
    ppp_process = subprocess.Popen(
        ["sudo", "pppd", "call", "sim7070", "noauth", "nodetach"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # Wait until interface is up
    for _ in range(60):
        if "ppp0" in subprocess.getoutput("ifconfig"):
            log_no_time("ppp0 is up!")
            ensure_dns()
            return ppp_process
        time.sleep(1)
    log_no_time("PPP failed to come up")
    ppp_process.terminate()
    return None

def init_connection():
    power_on_sim()
    ensure_serial_free()
    set_cmnb_mode()
    
    for attempt in range(MAX_PPP_RETRIES):
        print(f"PPP init attempt {attempt+1}...")
        
        if not check_sim_at(timeout=15):
            log_no_time("SIM7070G not responding, setting CMNB mode and sending CFUN reset...")
            reset_sim7070()

        ppp = start_ppp()
        if ppp:
            print("PPP connetion established")
            log_no_time("PPP connection established!")
            return ppp
        else:
            print("PPP failed, sending CFUN reset before retrying,,,")
            log_no_time("PPP failed, sending CFUN reset before retrying...")
            reset_sim7070()

    print("All PPP attempts failed,aborting...")
    log_no_time("All PPP attempts failed, aborting.")
    return None


def close_connection(ppp_process):
    try:
        if ppp_process:
            ppp_process.terminate()
            ppp_process.wait(timeout=10)
        else:
            subprocess.run(["sudo", "killall", "pppd"])

    except Exception as e:
        log_no_time(f"Error closing PPP: {e}")
        subprocess.run(["sudo", "killall", "pppd"])
        

if __name__ == "__main__":
    init_connection()

