#!/bin/bash
# WeatherDevice - RPi Dependency Installer
# Run with: bash install.sh

set -e

echo "========================================"
echo " WeatherDevice RPi Setup"
echo "========================================"

# ---- apt packages ----
echo ""
echo "[1/3] Installing apt packages..."
sudo apt update
sudo apt install -y \
    python3-pip \
    python3-picamera2 \
    libcamera-dev \
    pppd \
    ntpdate \
    net-tools \
    i2c-tools \
    python3-smbus

# ---- pip packages ----
echo ""
echo "[2/3] Installing pip packages..."
pip install -r "$(dirname "$0")/requirements.txt"

# ---- Verification ----
echo ""
echo "[3/3] Verifying installations..."

check() {
    if python3 -c "import $1" 2>/dev/null; then
        echo "  [OK] $1"
    else
        echo "  [FAIL] $1 - not importable"
    fi
}

check numpy
check PIL
check onnxruntime
check requests
check serial
check cv2
check RPi.GPIO
check picamera2

# system commands
for cmd in pppd ntpdate ifconfig; do
    if command -v "$cmd" &>/dev/null; then
        echo "  [OK] $cmd"
    else
        echo "  [FAIL] $cmd - not found"
    fi
done

echo ""
echo "========================================"
echo " Setup complete!"
echo "========================================"
