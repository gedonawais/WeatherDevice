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

sudo systemctl disable NetworkManager.service || true
sudo systemctl disable NetworkManager-wait-online.service || true

sudo systemctl disable --now cloud-init-main.service || true
sudo systemctl disable --now cloud-init-local.service || true
sudo systemctl disable --now cloud-init.service || true
sudo systemctl disable --now cloud-config.service || true
sudo systemctl disable --now cloud-final.service || true
sudo systemctl disable --now cloud-init-hotplugd.socket || true

sudo touch /etc/cloud/cloud-init.disabled

echo "=== Done! All dependencies installed and services optimized ==="
echo "=== Reboot recommended ==="
