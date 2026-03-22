#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Oasis Hub — Raspberry Pi Setup (GitHub install)
#  Run once on the Pi after cloning the repo:
#
#    git clone https://github.com/occnj/occhub.git /home/occnj/occ_hub
#    cd /home/occnj/occ_hub
#    sudo bash setup_pi.sh
# ─────────────────────────────────────────────────────────────────
set -e

APP_DIR="/home/occnj/occ_hub"
SERVICE_NAME="oasis-hub"

echo ""
echo "╔════════════════════════════════════════╗"
echo "║       Oasis Hub — Pi Setup             ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "  App directory: $APP_DIR"
echo ""

# 1. Make sure we're in the right place
if [ ! -f "$APP_DIR/member.py" ]; then
  echo "✗ member.py not found in $APP_DIR"
  echo "  Make sure you cloned the repo first:"
  echo "  git clone https://github.com/occnj/occhub.git $APP_DIR"
  exit 1
fi

cd "$APP_DIR"

# 2. Create uploads folder (not tracked by git)
mkdir -p static/uploads
chown -R occnj:occnj "$APP_DIR"

# 3. Create Python virtual environment
echo "▶  Creating virtual environment ..."
python3 -m venv venv

# 4. Install dependencies
echo "▶  Installing Python packages ..."
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet

# 5. Initialise the database (safe — only seeds if empty)
echo "▶  Initialising database ..."
venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from member import init_db
init_db()
print('   Database ready.')
"

# 6. Install and enable the systemd service
echo "▶  Installing systemd service ..."
cp oasis-hub.service /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

# 7. Done
echo ""
echo "✅  Done! Status:"
systemctl status $SERVICE_NAME --no-pager -l
echo ""
echo "  Local:        http://192.168.1.32:5500"
echo "  Tailscale:    https://occnj.tail812f78.ts.net/hub"
echo "  Admin:        https://occnj.tail812f78.ts.net/admin"
echo "  Default login: admin / oasis2025  ← change this!"
echo ""
echo "  Update commands (after git pull):"
echo "    sudo systemctl restart $SERVICE_NAME"
echo ""
echo "  Logs:"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo ""
