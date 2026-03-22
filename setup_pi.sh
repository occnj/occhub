#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Oasis Hub — Raspberry Pi Setup Script
#  Copies from USB drive GEJG and sets everything up
#  Run: bash /media/pi/GEJG/OCC_Hub/setup_pi.sh
# ─────────────────────────────────────────────────────────────────
set -e

USB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/home/pi/occ_hub"
SERVICE_NAME="oasis-hub"

echo ""
echo "╔════════════════════════════════════════╗"
echo "║       Oasis Hub — Pi Setup             ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "  Source: $USB_DIR"
echo "  Target: $APP_DIR"
echo ""

# 1. Copy files from USB to Pi
echo "▶  Copying app files from USB ..."
mkdir -p "$APP_DIR"
cp -r "$USB_DIR"/. "$APP_DIR/"
chown -R pi:pi "$APP_DIR"
cd "$APP_DIR"

# 2. Create Python virtual environment
echo "▶  Creating virtual environment ..."
python3 -m venv venv

# 3. Install dependencies
echo "▶  Installing Python packages ..."
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install flask flask-mail gunicorn --quiet

# 4. Initialise the database
echo "▶  Initialising database ..."
venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from member import init_db
init_db()
print('   Database ready.')
"

# 5. Install and enable the systemd service
echo "▶  Installing systemd service ..."
cp oasis-hub.service /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

# 6. Done
echo ""
echo "✅  Done! Status:"
systemctl status $SERVICE_NAME --no-pager -l
echo ""
echo "  Local network:  http://192.168.1.32:5500"
echo "  Tailscale IP:   http://100.90.22.46:5500"
echo "  Tailscale URL:  https://occnj.tail812f78.ts.net/hub"
echo "  Admin panel:    https://occnj.tail812f78.ts.net/admin"
echo "  Default login:  admin / oasis2025  ← change immediately!"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    sudo systemctl restart $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo ""
