#!/usr/bin/env bash
set -euo pipefail

BOT_NAME="${BOT_NAME:-sentinel-bot-02}"
SERVICE_NAME="${SERVICE_NAME:-oasis-hub.service}"
LOG_PATH="${LOG_PATH:-/home/occnj/occ_hub/error.log}"
SQLITE_DB_PATH="${SQLITE_DB_PATH:-/home/occnj/occ_hub/oasis.db}"
MAX_RESTARTS="${MAX_RESTARTS:-1}"
MIN_500S="${MIN_500S:-2}"
WINDOW_SECONDS="${WINDOW_SECONDS:-300}"
ALERT_COOLDOWN_SECONDS="${ALERT_COOLDOWN_SECONDS:-1800}"
ALERT_TO="${ALERT_TO:-media@oasisnj.net}"
SMTP_HOST="${SMTP_HOST:-smtp.office365.com}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USERNAME="${SMTP_USERNAME:-${MAIL_USERNAME:-media@oasisnj.net}}"
SMTP_PASSWORD="${SMTP_PASSWORD:-${MAIL_PASSWORD:-}}"
HOST="$(hostname -s 2>/dev/null || hostname)"

now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(now_utc)] $*"; }

svc_active() { systemctl is-active --quiet "$SERVICE_NAME"; }

sqlite_python() {
  python3 - "$SQLITE_DB_PATH" "$1" <<'PY'
import sqlite3,sys
db,sql=sys.argv[1:3]
conn=sqlite3.connect(db)
cur=conn.execute(sql)
row=cur.fetchone()
conn.close()
print("" if row is None or row[0] is None else row[0])
PY
}

db_exec() {
  python3 - "$SQLITE_DB_PATH" "$1" <<'PY'
import sqlite3,sys
db,sql=sys.argv[1:3]
conn=sqlite3.connect(db)
conn.executescript(sql)
conn.commit()
conn.close()
PY
}

read_state() {
  sqlite_python "SELECT last_offset FROM bot_log_state WHERE bot_name='${BOT_NAME}' LIMIT 1;"
}

write_state() {
  local offset="$1"
  db_exec "INSERT INTO bot_log_state (bot_name,last_offset) VALUES ('${BOT_NAME}', ${offset})
           ON CONFLICT(bot_name) DO UPDATE SET last_offset=${offset};"
}

ensure_bot_state_columns() {
  python3 - "$SQLITE_DB_PATH" <<'PY'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
cols = {row[1] for row in conn.execute("PRAGMA table_info(bot_log_state)")}
if "last_alert_at" not in cols:
    conn.execute("ALTER TABLE bot_log_state ADD COLUMN last_alert_at TEXT NOT NULL DEFAULT ''")
if "last_alert_key" not in cols:
    conn.execute("ALTER TABLE bot_log_state ADD COLUMN last_alert_key TEXT NOT NULL DEFAULT ''")
conn.commit()
conn.close()
PY
}

last_alert_state() {
  sqlite_python "SELECT COALESCE(last_alert_at, '' ) || '|' || COALESCE(last_alert_key, '') FROM bot_log_state WHERE bot_name='${BOT_NAME}' LIMIT 1;"
}

write_alert_state() {
  local alert_at="$1"
  local alert_key="$2"
  db_exec "INSERT INTO bot_log_state (bot_name,last_offset,last_alert_at,last_alert_key) VALUES ('${BOT_NAME}', 0, '${alert_at}', '${alert_key}')
           ON CONFLICT(bot_name) DO UPDATE SET last_alert_at='${alert_at}', last_alert_key='${alert_key}';"
}

incident_upsert() {
  local issue="$1" fix="$2" success="$3" attempt="$4" details_json="$5" dedup_key="$6"
  python3 - "$SQLITE_DB_PATH" "$BOT_NAME" "$HOST" "$SERVICE_NAME" "$issue" "$fix" "$success" "$attempt" "$details_json" "$dedup_key" <<'PY'
import sqlite3,sys
db,bot,host,svc,issue,fix,success,attempt,details,dkey=sys.argv[1:11]
conn=sqlite3.connect(db)
conn.execute("""
insert into incident_logs (bot_name,host,service_name,issue,fix_applied,success,attempt,details,dedup_key)
values (?,?,?,?,?,?,?,?,?)
on conflict(dedup_key) do update set
  fix_applied=excluded.fix_applied,
  success=excluded.success,
  attempt=excluded.attempt,
  details=excluded.details
""",(bot,host,svc,issue,fix,int(success),int(attempt),details,dkey))
conn.commit()
conn.close()
PY
}

restart_service() {
  systemctl restart "$SERVICE_NAME"
}

send_email() {
  local subject="$1" body="$2"
  python3 - "$subject" "$body" "$ALERT_TO" "$SMTP_HOST" "$SMTP_PORT" "$SMTP_USERNAME" "$SMTP_PASSWORD" <<'PY'
import os, smtplib, ssl, sys
from email.mime.text import MIMEText

subject, body, to_addr, host, port, username, password = sys.argv[1:8]
msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = subject
msg["From"] = username
msg["To"] = to_addr

ctx = ssl.create_default_context()
with smtplib.SMTP(host, int(port), timeout=20) as s:
    s.ehlo()
    s.starttls(context=ctx)
    s.ehlo()
    if password:
        s.login(username, password)
    s.send_message(msg)
PY
}

main() {
  ensure_bot_state_columns
  [[ -f "$LOG_PATH" ]] || exit 0

  local start_offset current_size
  start_offset="$(read_state)"
  start_offset="${start_offset:-0}"
  current_size="$(stat -c '%s' "$LOG_PATH")"
  if [[ "$start_offset" -gt "$current_size" ]]; then
    start_offset=0
  fi

  local sample
  sample="$(tail -c +"$((start_offset + 1))" "$LOG_PATH" || true)"
  local new_size
  new_size="$current_size"

  local now_epoch cutoff_epoch
  now_epoch="$(date +%s)"
  cutoff_epoch=$((now_epoch - WINDOW_SECONDS))

  local error_lines count_500 count_trace count_type
  error_lines="$(printf '%s\n' "$sample" | grep -E '500 error:|Traceback \(most recent call last\)|ERROR in app: Exception on ' || true)"
  count_500="$(printf '%s\n' "$error_lines" | grep -c '500 error:' || true)"
  count_trace="$(printf '%s\n' "$error_lines" | grep -c 'Traceback (most recent call last)' || true)"
  count_type="$(printf '%s\n' "$error_lines" | grep -c 'TypeError:' || true)"

  write_state "$new_size"

  if [[ "$count_500" -lt "$MIN_500S" && "$count_trace" -lt 1 ]]; then
    exit 0
  fi

  local issue fix dedup_key details attempt
  issue="error_log_spike"
  fix="systemctl restart ${SERVICE_NAME}"
  dedup_key="$(date -u +%Y%m%d%H%M)-${HOST}-${SERVICE_NAME}-${issue}"
  last_alert="$(last_alert_state)"
  last_alert_at="${last_alert%%|*}"
  last_alert_key="${last_alert#*|}"
  details="$(python3 - <<PY
import json
print(json.dumps({
  "log_path":"$LOG_PATH",
  "count_500":int("$count_500"),
  "count_trace":int("$count_trace"),
  "count_type":int("$count_type"),
  "window_seconds":int("$WINDOW_SECONDS")
}))
PY
)"

  alert_body="$(python3 - <<PY
print("""Oasis Hub Issue

Service: ${SERVICE_NAME}
Host: ${HOST}
Issue: ${issue}
Status: log spike detected

Latest log excerpt:
${error_lines:-No matching error lines captured}

Details:
${details}
""")
PY
)"

  should_email=1
  if [[ -n "${last_alert_at}" && -n "${last_alert_key}" ]]; then
    if [[ "${last_alert_key}" == "${issue}" ]]; then
      last_epoch="$(date -d "${last_alert_at}" +%s 2>/dev/null || echo 0)"
      now_epoch="$(date +%s)"
      if (( now_epoch - last_epoch < ALERT_COOLDOWN_SECONDS )); then
        should_email=0
      fi
    fi
  fi

  if [[ "$should_email" -eq 1 ]]; then
    send_email "Oasis Hub Issue" "$alert_body" || true
    write_alert_state "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$issue" || true
  fi

  incident_upsert "$issue" "$fix" 0 1 "$details" "$dedup_key" || true

  if svc_active; then
    restart_service || true
    sleep 10
    if svc_active; then
      incident_upsert "$issue" "$fix" 1 1 "$details" "$dedup_key" || true
      exit 0
    fi
  fi

  incident_upsert "$issue" "circuit_breaker_stop" 0 1 "$details" "$dedup_key" || true
  exit 2
}

main "$@"
