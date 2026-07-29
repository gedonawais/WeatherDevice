#!/bin/bash
# deploy_config.sh
# Copies WeatherDevice config files to the correct system locations on a fresh RPi OS.
# Run from the repo root: bash Firmware/RPi/deploy_config.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/backup_config"

echo "=== WeatherDevice - Deploy Config ==="
echo "Config source: $CONFIG_DIR"
echo ""

# ── 1. RPi boot config ──────────────────────────────────────────────────────
echo "[1/6] Copying boot config..."
sudo cp "$CONFIG_DIR/config.txt" /boot/firmware/config.txt
echo "      -> /boot/firmware/config.txt"

# ── 2. PPP peer config (SIM7070 modem) ──────────────────────────────────────
echo "[2/6] Copying PPP peer config..."
sudo mkdir -p /etc/ppp/peers
sudo cp "$CONFIG_DIR/sim7070" /etc/ppp/peers/sim7070
echo "      -> /etc/ppp/peers/sim7070"

# ── 3. PPP chat script ───────────────────────────────────────────────────────
echo "[3/6] Copying PPP chat script..."
sudo mkdir -p /etc/chatscripts
sudo cp "$CONFIG_DIR/sim7070.chat" /etc/chatscripts/sim7070.chat
echo "      -> /etc/chatscripts/sim7070.chat"

# ── 4. Systemd weather service ───────────────────────────────────────────────
echo "[4/6] Installing weather systemd service..."
sudo cp "$CONFIG_DIR/weather.service" /etc/systemd/system/weather.service
sudo systemctl daemon-reload
sudo systemctl enable weather.service
echo "      -> /etc/systemd/system/weather.service (enabled)"

# ── 5. Create required directories ───────────────────────────────────────────
echo "[5/6] Creating required runtime directories..."
sudo mkdir -p /home/WeatherDevice/Firmware/RPi/Logs
sudo mkdir -p /home/WeatherDevice/Firmware/RPi/Images
echo "      -> /home/WeatherDevice/Firmware/RPi/Logs"
echo "      -> /home/WeatherDevice/Firmware/RPi/Images"

# ── 6. Disable serial console, enable hardware UART ─────────────────────────
echo "[6/7] Configuring serial port (disable console, enable hardware UART)..."
sudo raspi-config nonint do_serial_hw 0    # enable hardware serial port
sudo raspi-config nonint do_serial_cons 1  # disable login shell over serial
echo "      -> Serial console disabled, hardware UART enabled"

# ── 7. Hardcode DNS (prevent pppd usepeerdns from overwriting) ───────────────
echo "[7/7] Writing static DNS (Google) to /etc/resolv.conf..."
sudo chattr -i /etc/resolv.conf 2>/dev/null
sudo cp "$CONFIG_DIR/resolv.conf" /etc/resolv.conf
echo "      -> /etc/resolv.conf written"

echo ""
echo "=== Done! ==="
echo ""
echo "Next steps:"
echo "  1. Reboot for boot config changes to take effect: sudo reboot"
echo "  2. After reboot, install dependencies:           bash $SCRIPT_DIR/install.sh"
echo "  3. Start the weather service manually to test:   sudo systemctl start weather.service"
echo "  4. Check service logs:                           journalctl -u weather.service -f"
echo "  NOTE: /etc/resolv.conf is now immutable (Google DNS hardcoded)."
echo "        To edit it: sudo chattr -i /etc/resolv.conf"
