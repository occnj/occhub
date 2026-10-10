#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  OccHub — set up the DROPLET to receive backups from the Pi.   Run once, as root:
#
#      sudo ./deploy/droplet_setup_backup_receiver.sh /path/to/pi_backup_key.pub
#
#  What it does
#    * installs rsync + gnupg
#    * creates a locked-down account (default: occbackup) whose ONLY ability is to run
#      "rrsync /srv/occhub-backup": the Pi's key cannot open a shell, forward ports,
#      or touch any file outside the backup folder
#    * creates /srv/occhub-backup/{db,uploads}
#    * installs a daily job: prune old database snapshots + a quick integrity/freshness check
#
#  Safe to re-run.  Override with env vars: BACKUP_USER, BACKUP_ROOT, KEEP, MAX_AGE_HOURS.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BACKUP_USER="${BACKUP_USER:-occbackup}"
BACKUP_ROOT="${BACKUP_ROOT:-/srv/occhub-backup}"
KEEP="${KEEP:-30}"                 # database snapshots to keep (one per day => a month)
MAX_AGE_HOURS="${MAX_AGE_HOURS:-30}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="${TOOL:-$(cd "$HERE/.." && pwd)/tools/occhub_backup.py}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
PUBKEY_FILE="${1:-}"
[ -n "$PUBKEY_FILE" ] && [ -f "$PUBKEY_FILE" ] || { echo "Usage: $0 /path/to/pi_backup_key.pub"; exit 1; }
PUBKEY="$(grep -v '^#' "$PUBKEY_FILE" | head -n1 | tr -d '\r')"
case "$PUBKEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *|sk-ssh-ed25519@openssh.com\ *) ;;
  *) echo "That does not look like a public key (expected a line starting with ssh-ed25519 ...)."; exit 1 ;;
esac
[ -f "$TOOL" ] || { echo "Cannot find $TOOL (set TOOL=/path/to/occhub_backup.py)"; exit 1; }

echo "▶ Installing packages ..."
if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q rsync gnupg python3 >/dev/null
fi

RRSYNC="$(command -v rrsync || true)"
if [ -z "$RRSYNC" ]; then
  for c in /usr/share/doc/rsync/scripts/rrsync /usr/share/doc/rsync/scripts/rrsync.gz; do
    if [ -f "$c" ]; then
      case "$c" in *.gz) gunzip -c "$c" > /usr/local/bin/rrsync ;; *) cp "$c" /usr/local/bin/rrsync ;; esac
      chmod 755 /usr/local/bin/rrsync; RRSYNC=/usr/local/bin/rrsync; break
    fi
  done
fi
[ -n "$RRSYNC" ] || { echo "rrsync not found. Install a newer rsync package."; exit 1; }
echo "  rrsync: $RRSYNC"

echo "▶ Creating account '$BACKUP_USER' and folders in $BACKUP_ROOT ..."
if ! id "$BACKUP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/$BACKUP_USER" --shell /bin/sh \
          --comment "OccHub backup receiver" "$BACKUP_USER"
fi
mkdir -p "$BACKUP_ROOT/db" "$BACKUP_ROOT/uploads"
chown -R "$BACKUP_USER:$BACKUP_USER" "$BACKUP_ROOT"
chmod 750 "$BACKUP_ROOT"

SSH_DIR="/home/$BACKUP_USER/.ssh"
mkdir -p "$SSH_DIR"
# 'restrict' turns off shell/pty/port/agent/X11 forwarding; command= pins the key to rrsync in one folder.
echo "restrict,command=\"$RRSYNC $BACKUP_ROOT\" $PUBKEY" > "$SSH_DIR/authorized_keys"
chown -R "$BACKUP_USER:$BACKUP_USER" "$SSH_DIR"
chmod 700 "$SSH_DIR"; chmod 600 "$SSH_DIR/authorized_keys"
# an account created with --system has a locked password, which sshd treats as "locked" unless the
# password field is '*' (locked but key login allowed)
usermod -p '*' "$BACKUP_USER" 2>/dev/null || true

echo "▶ Installing the daily prune + check job ..."
cat > /etc/cron.daily/occhub-backup-check <<CRON
#!/bin/sh
# Installed by droplet_setup_backup_receiver.sh. Keeps the newest $KEEP database snapshots, then
# checks the newest one is complete, matches its checksum and is fresh. Result goes to the
# system log:  journalctl -t occhub-backup   (or: grep occhub-backup /var/log/syslog)
OUT=\$(python3 "$TOOL" prune --source "$BACKUP_ROOT" --keep $KEEP 2>&1 &&
       python3 "$TOOL" verify --quick --source "$BACKUP_ROOT" --max-age-hours $MAX_AGE_HOURS 2>&1)
STATUS=\$?
echo "\$OUT" | logger -t occhub-backup -p \$([ \$STATUS -eq 0 ] && echo user.info || echo user.err)
exit \$STATUS
CRON
chmod 755 /etc/cron.daily/occhub-backup-check

echo
echo "✅ Done. On the Pi, set in /home/occnj/occ_hub/.backup.env :"
echo "     BACKUP_REMOTE=$BACKUP_USER@<this droplet's address>:"
echo "   then run:  python3 tools/occhub_backup.py push"
