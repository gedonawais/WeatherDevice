import time
import smbus2


class SHT3x:
    """
    Efficient SHT3x driver using periodic measurement mode.
    Supports averaged reads with CRC validation.
    """

    CMD_START_PERIODIC_1MPS = (0x21, 0x30)  # 1 measurement/sec, high repeatability
    CMD_FETCH_DATA = (0xE0, 0x00)
    CMD_STOP_PERIODIC = (0x30, 0x93)

    def __init__(self, bus_id=1, address=0x44):
        self.bus = smbus2.SMBus(bus_id)
        self.addr = address
        self._start_periodic()
        time.sleep(0.05)  # allow first measurement

    # ---------- CRC ----------
    @staticmethod
    def _crc8(data):
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    # ---------- Commands ----------
    def _write_cmd(self, cmd):
        self.bus.write_i2c_block_data(self.addr, cmd[0], [cmd[1]])

    def _start_periodic(self):
        self._write_cmd(self.CMD_START_PERIODIC_1MPS)

    def stop(self):
        try:
            self._write_cmd(self.CMD_STOP_PERIODIC)
        finally:
            self.bus.close()

    # ---------- Read ----------
    def _read_once(self):
        self._write_cmd(self.CMD_FETCH_DATA)
        data = self.bus.read_i2c_block_data(self.addr, 0x00, 6)

        if self._crc8(data[0:2]) != data[2]:
            raise IOError("Temp CRC fail")
        if self._crc8(data[3:5]) != data[5]:
            raise IOError("Hum CRC fail")

        raw_t = (data[0] << 8) | data[1]
        raw_h = (data[3] << 8) | data[4]

        temp = -45 + 175 * raw_t / 65535.0
        hum = 100 * raw_h / 65535.0
        return temp, hum

    # ---------- Public ----------
    def read_avg(self, samples=5, delay=0.2):
        """
        Averaged temperature & humidity.

        samples: number of reads to average (5–10 typical)
        delay: spacing between reads (sec). 0.2–1s reasonable.

        returns: (temp_c, hum_rh)
        """
        temps = []
        hums = []

        for _ in range(samples):
            try:
                t, h = self._read_once()
                temps.append(t)
                hums.append(h)
            except IOError:
                pass
            time.sleep(delay)

        if not temps:
            raise IOError("SHT3x: no valid samples")

        return sum(temps) / len(temps), sum(hums) / len(hums)
