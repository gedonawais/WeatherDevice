import serial
import time

class UARTComm:
    def __init__(self, port='/dev/serial0', baudrate=9600, timeout=0.1):
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        time.sleep(2)  # wait for device to be ready

    def send(self, message: str):
        """Send a string message over UART"""
        if not message.endswith('\n'):
            message += '\n'  # ensure newline for ESP32 readStringUntil
        self.ser.write(message.encode())

    def receive(self):
        """Check for incoming data and return it, or None"""
        if self.ser.in_waiting:
            line = self.ser.readline().decode(errors='ignore').strip()
            if line:
                return line
        return None

    def close(self):
        """Close UART connection"""
        self.ser.close()
