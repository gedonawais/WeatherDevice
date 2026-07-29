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

for svc in \
  NetworkManager.service \
  NetworkManager-wait-online.service \
  cloud-init-main.service \
  cloud-init-local.service \
  cloud-init.service \
  cloud-config.service \
  cloud-final.service \
  cloud-init-hotplugd.socket
do
  if systemctl list-unit-files | grep -q "^${svc}"; then
    echo "Disabling $svc"
    sudo systemctl disable --now "$svc" || true
  else
    echo "Skipping $svc (not found)"
  fi
done

sudo mkdir -p /etc/cloud
sudo touch /etc/cloud/cloud-init.disabled

echo "=== Done! All dependencies installed and services optimized ==="
echo "=== Reboot recommended ==="
