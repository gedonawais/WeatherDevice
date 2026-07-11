from uart_comm import UARTComm
uart = UARTComm(port='/dev/serial0', baudrate=9600)

uart.send("SEND VOLTAGE")
try:
    while True:
        data = uart.receive()
        if data:
            print(data)

except KeyboardInterrupt:
    uart.close()
