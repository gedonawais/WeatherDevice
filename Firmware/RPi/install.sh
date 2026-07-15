#!/bin/bash
# Install all dependencies for the WeatherDevice RPi scripts

set -e

echo "=== Installing apt packages ==="
sudo apt update
sudo apt install -y \
    python3-picamera2 \
    python3-libcamera \
    python3-pip \
    python3-dev \
    libatlas-base-dev \
    ppp \
    ntpdate \
    net-tools \
    libopencv-dev

echo "=== Installing pip packages ==="
pip install -r "$(dirname "$0")/requirements.txt"

echo "=== Done! All dependencies installed ==="
