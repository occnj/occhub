#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  OccHub — set up the app on a droplet (Ubuntu 24.04), next to PIBASE.   Run as root,
#  from a clone of this repo, AFTER PIBASE's own scripts/setup-droplet.sh:
#
#      bash deploy/droplet/setup_droplet.sh --domain hub.oasisnj.net
#
#  What it does
#    * installs Python, Caddy, LibreOffice Writer (for .doc sermon notes), rsync, gnupg
#    * creates the "occhub" account and puts the app in /home/occhub/occ_hub
#    * makes the venv, installs requirements.txt, writes .env with a new SECRET_KEY (once)
#    * installs and starts the occhub systemd service (gunicorn on 127.0.0.1:5500)
#    * serves https://<domain> through the same Caddy as PIBASE, in
#      /etc/caddy/pibase.d/occhub.caddy (PIBASE's setup rewrites /etc/caddy/Caddyfile, not that folder)
#
#  It leaves PIBASE, its firewall rules and its swap alone, and never overwrites an existing .env
#  or database. Safe to re-run: it updates the code and restarts the app.
#  The data (database + photos) comes from the Pi afterwards: docs/BACKUP_AND_MIGRATION.md.
#
#  Options:
#    --domain NAME   OccHub's public name (required), e.g. hub.oasisnj.net. Not PIBASE's API name.
#    --user NAME     account that runs the app (default: occhub)
#    --tls-internal  Caddy's own certificate instead of Let's Encrypt (testing without DNS)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

domain="" user="occhub" tls_internal=0
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) domain="${2:-}"; shift 2 ;;
    --user) user="${2:-}"; shift 2 ;;
    --tls-internal) tls_internal=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\n▶ %s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root"
[ -n "$domain" ] || die "--domain is required, for example --domain hub.oasisnj.net"
domain="$(printf '%s' "$domain" | tr 'A-Z' 'a-z')"
printf '%s' "$domain" | grep -Eq '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$' \
  || die "--domain must be a host name such as hub.oasisnj.net"
printf '%s' "$user" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$' || die "--user must be a plain lowercase account name"
command -v apt-get >/dev/null || die "this script supports Ubuntu and Debian (apt-get not found)"

src="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ -f "$src/member.py" ] && [ -f "$src/requirements.txt" ] || die "run this from inside an OccHub clone"
home="/home/$user"
app="$home/occ_hub"
site="/etc/caddy/pibase.d/occhub.caddy"
service="/etc/systemd/system/occhub.service"
export DEBIAN_FRONTEND=noninteractive

# PIBASE serves its own API names from /etc/caddy/pibase.d/<name>.caddy; OccHub must use a different one.
if [ -f "/etc/caddy/pibase.d/$domain.caddy" ] && grep -q '127.0.0.1:18080' "/etc/caddy/pibase.d/$domain.caddy"; then
  die "$domain is already PIBASE's API name on this server; give OccHub its own (e.g. hub.oasisnj.net)"
fi

# ---------------------------------------------------------------- packages
say "Packages: Python, Caddy, LibreOffice Writer, rsync, gnupg"
apt-get update -q >/dev/null
apt-get install -y -q python3 python3-venv python3-pip git caddy rsync gnupg sudo curl >/dev/null
apt-get install -y -q --no-install-recommends libreoffice-writer-nogui >/dev/null \
  || echo "  ⚠ LibreOffice not installed — .doc uploads will be refused until it is"

# ---------------------------------------------------------------- account
say "Account '$user'"
if ! id "$user" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$user" >/dev/null
  echo "  created $user"
fi

# ---------------------------------------------------------------- code
say "Code in $app"
if [ ! -d "$app/.git" ]; then
  if [ "$src" = "$app" ]; then die "the clone is already at $app but has no .git"; fi
  # Clone from this checkout (no second GitHub login needed), then point origin at GitHub.
  origin="$(git -C "$src" remote get-url origin 2>/dev/null || true)"
  # root clones (git won't let another account read root's checkout), then hands it over.
  git clone -q "$src" "$app"
  [ -z "$origin" ] || git -C "$app" remote set-url origin "$origin"
  chown -R "$user:$user" "$app"
else
  echo "  already there; update it with: sudo -u $user git -C $app pull && sudo systemctl restart occhub"
fi
install -d -o "$user" -g "$user" "$app/static/uploads"

# ---------------------------------------------------------------- python
say "Python packages"
[ -x "$app/venv/bin/python" ] || sudo -u "$user" -H python3 -m venv "$app/venv"
sudo -u "$user" -H "$app/venv/bin/pip" install -q --upgrade pip
sudo -u "$user" -H "$app/venv/bin/pip" install -q -r "$app/requirements.txt"

# ---------------------------------------------------------------- settings
say "Settings (.env)"
if [ -f "$app/.env" ]; then
  echo "  keeping the existing $app/.env"
else
  # A new SECRET_KEY only signs admins out; accounts and passwords come with the database.
  # RESEND_API_KEY / MAIL_FROM: copy them from the Pi's .env afterwards (needed for form emails).
  ( umask 077
    printf 'SECRET_KEY=%s\n# RESEND_API_KEY=re_xxxxxxxx\n# MAIL_FROM=Oasis Hub <media@oasisnj.net>\n' \
      "$(openssl rand -hex 32)" > "$app/.env" )
  chown "$user:$user" "$app/.env"
  echo "  wrote $app/.env with a new SECRET_KEY; add RESEND_API_KEY and MAIL_FROM from the Pi"
fi
chmod 600 "$app/.env"

# ---------------------------------------------------------------- service
say "systemd service 'occhub'"
sed -e "s|/home/occhub/|$home/|g" -e "s|^User=occhub|User=$user|" \
  "$app/deploy/droplet/occhub.service" > "$service"
systemctl daemon-reload
systemctl enable occhub >/dev/null 2>&1
systemctl restart occhub
for i in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5500/hub || true)" = 200 ] && break
  [ "$i" -lt 30 ] || { journalctl -u occhub -n 30 --no-pager >&2 || true; die "OccHub did not answer on 127.0.0.1:5500"; }
  sleep 1
done
echo "  OccHub answers on 127.0.0.1:5500"

# ---------------------------------------------------------------- HTTPS
say "Caddy (HTTPS) for $domain"
install -d -m 0755 /etc/caddy/pibase.d
if ! grep -qs '^import /etc/caddy/pibase.d/\*\.caddy' /etc/caddy/Caddyfile; then
  if [ ! -s /etc/caddy/Caddyfile ] || grep -q '/usr/share/caddy' /etc/caddy/Caddyfile; then
    # Caddy's stock welcome page (or nothing): use the same layout PIBASE's setup writes.
    printf '# Sites live in /etc/caddy/pibase.d/*.caddy (PIBASE and OccHub).\n{\n}\nimport /etc/caddy/pibase.d/*.caddy\n' \
      > /etc/caddy/Caddyfile
  else
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.before-occhub-$(date +%Y%m%d%H%M%S)"
    printf '\nimport /etc/caddy/pibase.d/*.caddy\n' >> /etc/caddy/Caddyfile
  fi
fi
tmp="$(mktemp)"
sed -e "s|^hub\.oasisnj\.net {|$domain {|" "$app/deploy/droplet/occhub.caddy" > "$tmp"
if [ "$tls_internal" -eq 1 ]; then sed -i "s|^$domain {|$domain {\n\ttls internal|" "$tmp"; fi
old=""; [ -f "$site" ] && old="$(cat "$site")"
install -m 0644 "$tmp" "$site"; rm -f "$tmp"
if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >&2 || true
  if [ -n "$old" ]; then printf '%s\n' "$old" > "$site"; else rm -f "$site"; fi
  die "Caddy rejected the configuration; put the old one back and reloaded nothing"
fi
systemctl enable caddy >/dev/null 2>&1
systemctl reload caddy 2>/dev/null || systemctl restart caddy

# ---------------------------------------------------------------- check
say "Checking https://$domain"
ok=0
for i in $(seq 1 30); do
  if [ "$tls_internal" -eq 1 ]; then
    curl -fsS -k --noproxy "*" --resolve "$domain:443:127.0.0.1" -o /dev/null "https://$domain/hub" 2>/dev/null && { ok=1; break; }
  else
    curl -fsS -o /dev/null "https://$domain/hub" 2>/dev/null && { ok=1; break; }
  fi
  sleep 2
done
ip="$(curl -fsS --max-time 2 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || hostname -I | awk '{print $1}')"
if [ "$ok" -eq 1 ]; then
  echo "  https://$domain is up."
else
  echo "  https://$domain did not answer yet. Point a DNS A record for $domain at $ip,"
  echo "  wait a few minutes, then check: curl -I https://$domain/hub"
fi

cat <<EOF

✅ Done. OccHub runs as '$user' in $app, next to PIBASE, behind the same Caddy.
  * This is an empty OccHub until the Pi's data arrives: docs/BACKUP_AND_MIGRATION.md,
    "Moving OccHub from the Pi to the droplet" (steps B and C).
  * Email: add RESEND_API_KEY and MAIL_FROM from the Pi to $app/.env, then: systemctl restart occhub
  * Logs:    journalctl -u occhub -f     ($app/error.log)
  * Update:  sudo -u $user git -C $app pull && systemctl restart occhub
EOF
