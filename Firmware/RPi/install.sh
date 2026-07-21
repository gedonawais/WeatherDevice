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

# NetworkManager wait-online can cause boot delays
sudo systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
sudo systemctl stop NetworkManager-wait-online.service 2>/dev/null || true

# Disable NetworkManager only if PPP handles connectivity
sudo systemctl disable NetworkManager.service 2>/dev/null || true
sudo systemctl stop NetworkManager.service 2>/dev/null || true

# Disable cloud-init services
sudo systemctl disable cloud-init-local.service 2>/dev/null || true
sudo systemctl disable cloud-init-network.service 2>/dev/null || true
sudo systemctl disable cloud-init.service 2>/dev/null || true
sudo systemctl disable cloud-config.service 2>/dev/null || true
sudo systemctl disable cloud-final.service 2>/dev/null || true
sudo systemctl disable cloud-init-hotplugd.socket 2>/dev/null || true

echo "=== Done! All dependencies installed and services optimized ==="
