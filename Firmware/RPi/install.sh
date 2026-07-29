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
    libatlas3-base \
    ppp \
    chrony \
    net-tools \
    libopencv-dev

echo "=== Installing pip packages ==="
sudo pip install --break-system-packages -r "$(dirname "$0")/requirements.txt"

echo "=== Disabling unnecessary services ==="

# Disable wait-online
sudo systemctl disable --now NetworkManager-wait-online.service 2>/dev/null || true

# Disable NetworkManager only if something else manages networking
# sudo systemctl disable --now NetworkManager.service 2>/dev/null || true

# Disable cloud-init
sudo systemctl disable --now cloud-init-local.service 2>/dev/null || true
sudo systemctl disable --now cloud-init-network.service 2>/dev/null || true
sudo systemctl disable --now cloud-init.service 2>/dev/null || true
sudo systemctl disable --now cloud-config.service 2>/dev/null || true
sudo systemctl disable --now cloud-final.service 2>/dev/null || true
sudo systemctl disable --now cloud-init-hotplugd.socket 2>/dev/null || true

# Prevent cloud-init from starting again
sudo touch /etc/cloud/cloud-init.disabled

echo "=== Done! All dependencies installed and services optimized ==="
