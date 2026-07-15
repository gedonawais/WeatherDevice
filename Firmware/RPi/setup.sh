#!/usr/bin/env bash
# =============================================================================
# WeatherDevice – Raspberry Pi Setup Script
# Prepares a fresh Raspberry Pi OS Lite installation.
# Run as root or with sudo: sudo bash setup.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Require root
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root. Try: sudo bash $0"
fi

# ---------------------------------------------------------------------------
# Configuration – adjust these variables if needed
# ---------------------------------------------------------------------------
APP_USER="${SUDO_USER:-pi}"           # the non-root user that will run the app
APP_DIR="/home/${APP_USER}/Weather"   # where Weather scripts live
LOG_DIR="${APP_DIR}/Logs"
CONFIG_TXT="/boot/firmware/config.txt"
CHATSCRIPTS_DIR="/etc/chatscripts"
PPP_PEERS_DIR="/etc/ppp/peers"

# Detect script directory so relative paths work regardless of CWD
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. System update
# ---------------------------------------------------------------------------
info "Updating package lists and upgrading existing packages …"
apt-get update -y
apt-get upgrade -y
success "System up to date."

# ---------------------------------------------------------------------------
# 2. Install required apt packages
# ---------------------------------------------------------------------------
info "Installing required apt packages …"

APT_PACKAGES=(
    # Core utilities
    git
    curl
    wget
    build-essential
    ca-certificates
    openssl
    python-is-python3

    # Python runtime & packaging
    python3
    python3-dev
    python3-pip
    python3-venv
    python3-setuptools
    python3-wheel

    # Camera support
    python3-picamera2
    libcamera-tools
    rpicam-apps
    v4l-utils
    python3-libcamera

    # Computer vision
    python3-opencv
    python3-pil

    # Numeric / ML
    python3-numpy

    # GPIO / hardware interfaces
    python3-gpiozero
    python3-rpi-lgpio
    python3-lgpio
    python3-libgpiod
    gpiod
    raspi-gpio
    pigpio
    pigpiod
    libpigpio1
    libpigpio-dev
    python3-pigpio

    # I2C / SPI / serial
    i2c-tools
    python3-smbus2
    python3-smbus
    python3-spidev
    python3-serial

    # Sense HAT
    sense-hat
    python3-sense-hat
    python3-rtimulib

    # Networking & modem
    ppp
    network-manager

    # Web / HTTP
    python3-flask
    python3-requests
    python3-urllib3

    # Misc Python libs used by the application
    python3-pillow
    python3-dotenv
    python3-psutil
    python3-simplejson
    python3-tqdm
    python3-v4l2

    # Fonts (used by image annotation code)
    fonts-dejavu-core
    fonts-liberation
)

for pkg in "${APT_PACKAGES[@]}"; do
    if apt-get install -y "$pkg" 2>/dev/null; then
        success "  apt: $pkg"
    else
        warn "  apt: $pkg – not found, skipping."
    fi
done

# ---------------------------------------------------------------------------
# 3. Enable Raspberry Pi hardware interfaces
#    (I2C, SPI, UART, Camera, GPIO legacy overlay)
# ---------------------------------------------------------------------------
info "Configuring Raspberry Pi hardware interfaces in ${CONFIG_TXT} …"

if [[ ! -f "$CONFIG_TXT" ]]; then
    # Older Raspberry Pi OS path
    CONFIG_TXT="/boot/config.txt"
fi

if [[ ! -f "$CONFIG_TXT" ]]; then
    warn "config.txt not found at /boot/firmware/config.txt or /boot/config.txt – skipping hardware interface configuration."
else
    # Helper: append a line only if it is not already present
    append_once() {
        local line="$1"
        grep -qxF "$line" "$CONFIG_TXT" || echo "$line" >> "$CONFIG_TXT"
    }

    # I2C
    sed -i 's/^#dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' "$CONFIG_TXT"
    grep -qE '^dtparam=i2c_arm=' "$CONFIG_TXT" || append_once "dtparam=i2c_arm=on"

    # SPI
    sed -i 's/^#dtparam=spi=.*/dtparam=spi=on/' "$CONFIG_TXT"
    grep -qE '^dtparam=spi=' "$CONFIG_TXT" || append_once "dtparam=spi=on"

    # UART (required for SIM7070G modem on /dev/serial0)
    append_once "enable_uart=1"

    # Disable Bluetooth to free up the hardware UART
    append_once "dtoverlay=disable-bt"

    # Camera auto-detect
    grep -qE '^camera_auto_detect=' "$CONFIG_TXT" || append_once "camera_auto_detect=1"

    # Legacy GPIO overlay (required by RPi.GPIO / gpiozero on newer kernels)
    append_once "dtoverlay=legacy-gpio"

    # 1-Wire (optional sensor bus)
    append_once "dtoverlay=w1-gpio"

    success "Hardware interfaces configured."
fi

# Disable the serial console so the UART is free for the modem
CMDLINE_TXT=""
for path in "/boot/firmware/cmdline.txt" "/boot/cmdline.txt"; do
    if [[ -f "$path" ]]; then
        CMDLINE_TXT="$path"
        break
    fi
done

if [[ -n "$CMDLINE_TXT" ]]; then
    if grep -q "console=serial0" "$CMDLINE_TXT" || grep -q "console=ttyAMA0" "$CMDLINE_TXT" || grep -q "console=ttyS0" "$CMDLINE_TXT"; then
        sed -i 's/console=serial0,[0-9]* //g' "$CMDLINE_TXT"
        sed -i 's/console=ttyAMA0,[0-9]* //g' "$CMDLINE_TXT"
        sed -i 's/console=ttyS0,[0-9]* //g' "$CMDLINE_TXT"
        success "Serial console removed from ${CMDLINE_TXT} (UART now free for modem)."
    fi
fi

# Stop & disable the serial getty that occupies ttyS0/ttyAMA0
systemctl stop serial-getty@ttyS0.service 2>/dev/null || true
systemctl disable serial-getty@ttyS0.service 2>/dev/null || true
systemctl stop serial-getty@ttyAMA0.service 2>/dev/null || true
systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
success "Serial getty services disabled."

# Load i2c-dev module now and persist it
modprobe i2c-dev 2>/dev/null || true
grep -qxF 'i2c-dev' /etc/modules || echo 'i2c-dev' >> /etc/modules

# ---------------------------------------------------------------------------
# 4. Install required Python packages via pip
#    Only packages not readily available (or current enough) via apt
# ---------------------------------------------------------------------------
info "Installing Python packages via pip …"

PIP_PACKAGES=(
    "onnxruntime>=1.16.0"   # ML inference engine (no apt package)
    "pyserial>=3.5"         # Serial port access
    "spidev>=3.5"           # SPI access
    "picamera2>=0.3"        # Camera (fallback if apt version unavailable)
    "RPi.GPIO>=0.7"         # GPIO (fallback if apt version unavailable)
    "gpiozero>=2.0"         # High-level GPIO
    "lgpio>=0.2"            # Low-level GPIO
    "numpy>=1.24"           # Numerical computing
    "Pillow>=9.4"           # Image processing
    "requests>=2.28"        # HTTP client
    "Flask>=2.2"            # Lightweight web server
    "python-dotenv>=0.21"   # .env file support
    "psutil>=5.9"           # Process / system utilities
    "simplejson>=3.18"      # JSON library
    "tqdm>=4.64"            # Progress bars
    "v4l2-python3>=0.3"     # V4L2 bindings
    "simplejpeg>=1.6"       # Fast JPEG encode/decode
    "piexif>=1.1"           # EXIF data handling
    "pidng>=4.0"            # DNG raw file support
    "av>=10.0"              # PyAV (libav bindings for picamera2)
    "coloredlogs>=15.0"     # Coloured console logging
    "smbus2>=0.4"           # SMBus/I2C
)

for pkg in "${PIP_PACKAGES[@]}"; do
    if pip3 install --break-system-packages "$pkg" 2>/dev/null; then
        success "  pip: $pkg"
    else
        warn "  pip: $pkg – installation failed, continuing."
    fi
done

# ---------------------------------------------------------------------------
# 5. Configure PPP / SIM7070G modem
# ---------------------------------------------------------------------------
info "Configuring PPP peer and chat script for SIM7070G …"

mkdir -p "$PPP_PEERS_DIR" "$CHATSCRIPTS_DIR"

# Write /etc/ppp/peers/sim7070
cat > "${PPP_PEERS_DIR}/sim7070" <<'EOF'
/dev/serial0 115200
connect "/usr/sbin/chat -v -f /etc/chatscripts/sim7070.chat"
noauth
defaultroute
usepeerdns
persist
EOF
chmod 600 "${PPP_PEERS_DIR}/sim7070"

# Write /etc/chatscripts/sim7070.chat
cat > "${CHATSCRIPTS_DIR}/sim7070.chat" <<'EOF'
ABORT "BUSY"
ABORT "NO CARRIER"
ABORT "ERROR"
ABORT "NO DIALTONE"
"" AT
OK AT+CPIN?
OK AT+CGDCONT=1,"IP","WSIM"
OK ATD*99#
CONNECT ""
EOF
chmod 640 "${CHATSCRIPTS_DIR}/sim7070.chat"

success "PPP configuration written."

# ---------------------------------------------------------------------------
# 6. Create application directories and copy scripts
# ---------------------------------------------------------------------------
info "Creating application directory at ${APP_DIR} …"

mkdir -p "$APP_DIR" "$LOG_DIR"

# Copy firmware scripts if running from within the repository
if [[ -d "$SCRIPT_DIR" && "$SCRIPT_DIR" != "$APP_DIR" ]]; then
    COPY_PATTERNS=(
        "*.py"
        "*.json"
        "*.txt"
        "*.onnx"
        "*.otf"
    )
    for pattern in "${COPY_PATTERNS[@]}"; do
        for f in "${SCRIPT_DIR}"/${pattern}; do
            [[ -e "$f" ]] && cp "$f" "$APP_DIR/" && success "  Copied: $(basename "$f")"
        done
    done

    # Copy Logs directory if it exists
    if [[ -d "${SCRIPT_DIR}/Logs" ]]; then
        cp -r "${SCRIPT_DIR}/Logs/." "$LOG_DIR/"
    fi
fi

# Ensure log file exists
touch "${LOG_DIR}/capture.log"

# Set ownership
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
success "Application directory ready: ${APP_DIR}"

# ---------------------------------------------------------------------------
# 7. User group membership (GPIO, I2C, SPI, dialout, video, camera)
# ---------------------------------------------------------------------------
info "Adding ${APP_USER} to required groups …"

GROUPS_NEEDED=(gpio i2c spi dialout video camera tty)
for grp in "${GROUPS_NEEDED[@]}"; do
    if getent group "$grp" > /dev/null 2>&1; then
        usermod -aG "$grp" "$APP_USER"
        success "  ${APP_USER} added to group: $grp"
    else
        warn "  Group '$grp' does not exist, skipping."
    fi
done

# ---------------------------------------------------------------------------
# 8. Install and enable the weather systemd service
# ---------------------------------------------------------------------------
info "Installing weather.service systemd unit …"

SERVICE_FILE="/etc/systemd/system/weather.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Weather Camera Service
After=NetworkManager.service network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 ${APP_DIR}/weather.py
WorkingDirectory=${APP_DIR}
StandardOutput=journal
StandardError=journal
Restart=always
User=${APP_USER}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable weather.service
success "weather.service installed and enabled."

# ---------------------------------------------------------------------------
# 9. Enable the pigpio daemon
# ---------------------------------------------------------------------------
info "Enabling pigpiod (pigpio daemon) …"
systemctl enable pigpiod 2>/dev/null && success "pigpiod enabled." || warn "pigpiod service not found, skipping."

# ---------------------------------------------------------------------------
# 10. Verify key components
# ---------------------------------------------------------------------------
info "Running quick verification checks …"

CHECKS_PASSED=0
CHECKS_FAILED=0

check_cmd() {
    local label="$1"
    shift
    if "$@" &>/dev/null; then
        success "  [PASS] $label"
        ((CHECKS_PASSED++))
    else
        warn "  [FAIL] $label"
        ((CHECKS_FAILED++))
    fi
}

check_cmd "python3 available"       python3 --version
check_cmd "pip3 available"          pip3 --version
check_cmd "pppd available"          which pppd
check_cmd "i2cdetect available"     which i2cdetect
check_cmd "raspi-config available"  which raspi-config

for mod in onnxruntime picamera2 serial gpiozero; do
    check_cmd "Python module: $mod" python3 -c "import $mod"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Setup complete."
printf " Checks passed : %d\n" "$CHECKS_PASSED"
printf " Checks failed : %d\n" "$CHECKS_FAILED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $CHECKS_FAILED -gt 0 ]]; then
    warn "Some checks failed. Review the output above and resolve any issues before running the weather service."
else
    success "All checks passed. Reboot to apply hardware interface changes."
    echo ""
    echo "  sudo reboot"
fi
