# Weather Device - Raspberry Pi Zero 2W Setup Guide

This document explains how to set up the Raspberry Pi Zero 2W for the Weather Device project.

---

# 1. Install Raspberry Pi OS Lite

1. Download and install **Raspberry Pi OS Lite** using Raspberry Pi Imager.
2. Flash the OS to the SD card.
3. Before writing the image, configure:
   - Enable SSH
   - Set username and password
   - Configure Wi-Fi (optional)

Boot the Raspberry Pi.

---

# 2. Configure Raspberry Pi

## 2.1 Enable Automatic Login

Open the Raspberry Pi configuration tool:

```bash
sudo raspi-config
```

Navigate to:

```
System Options
    → Boot / Auto Login
        → Console Autologin
```

Select console auto login.

This allows the Raspberry Pi to automatically log in after boot.

Also, set System Date and Time in Localisation menu

---

## 2.2 Disable sudo Password Requirement

Open the sudo configuration file:

```bash
sudo visudo
```

Add the following line at the end:

```text
<username> ALL=(ALL) NOPASSWD: ALL
```

Replace:

```
<username>
```

with your Raspberry Pi username.

Example:

```text
pi ALL=(ALL) NOPASSWD: ALL
```

Save and exit.

This allows scripts and services to execute sudo commands without requiring a password.

---

# 3. Configure Network

## 3.1 Connect to Wi-Fi

Open Raspberry Pi configuration:

```bash
sudo raspi-config
```

Navigate to:

```
System Options
    → Wireless LAN
```

Enter:

- Wi-Fi SSID
- Wi-Fi password

Reboot if required.

---

# 4. Install Required Software

## 4.1 Update Package List

Run:

```bash
sudo apt update
```

---

## 4.2 Install Git

Install Git:

```bash
sudo apt install git -y
```

Verify installation:

```bash
git --version
```

---

# 5. Download Weather Device Repository
If you wish to enable Wifi use the following commands

```bash
sudo systemctl enable NetworkManager.service
sudo systemctl start NetworkManager.service
sudo nmcli radio wifi on
```
Move to the home directory:

```bash
cd /home
```

Clone the repository:

```bash
sudo git clone <REPOSITORY_URL>
```

Example:

```bash
sudo git clone https://github.com/<username>/WeatherDevice.git
```

The project should now be available at:

```
/home/WeatherDevice
```

---

# 6. Configure Git Repository Permission

If Git shows a **"detected dubious ownership in repository"** error, run:

```bash
git config --global --add safe.directory /home/WeatherDevice
```

---

# 7. Deploy System Configuration

Run the configuration deployment script:

```bash
bash /home/WeatherDevice/Firmware/RPi/deploy_config.sh
```

This installs required system files, services, and configurations.

---

# 8. Restart Raspberry Pi

Reboot the device:

```bash
sudo reboot
```

Wait until the Raspberry Pi boots again.

---

# 9. Install Python Dependencies

Run the installation script:

```bash
bash /home/WeatherDevice/Firmware/RPi/install.sh
```

This installs all required libraries and dependencies.

---

# 10. Disable Wi-Fi (Optional)
Network Manager is already disabled so no need to run this
Disable Wi-Fi:

```bash
sudo nmcli radio wifi off
```

Check Wi-Fi status:

```bash
nmcli radio
```

---

# 11. Weather Service Management

## Start Weather Service

```bash
sudo systemctl start weather.service
```

---

## Enable Service at Boot

To automatically start the weather application after every reboot:

```bash
sudo systemctl enable weather.service
```

---

## Check Service Status

```bash
sudo systemctl status weather.service
```

---

## View Live Service Logs

To monitor real-time logs:

```bash
journalctl -u weather.service -f
```

---

## Restart Service if there are any issues in logs

After changing code:

```bash
sudo systemctl restart weather.service
```

---

# 12. Updating the Software

To update the Weather Device software:

```bash
sudo nmcli radio wifi on
cd /home/WeatherDevice
```

Download the latest changes:

```bash
sudo git pull
sudo nmcli radio wifi off
```

Restart the service:

```bash
sudo systemctl restart weather.service
```

---

# Setup Complete

The Raspberry Pi Zero 2W is now configured and ready to run the Weather Device application.
