#!/usr/bin/env bash
set -euo pipefail

BOT_NAME="${BOT_NAME:-sentinel-bot-02}"
SERVICE_NAME="${SERVICE_NAME:-oasis-hub.service}"
LOG_PATH="${LOG_PATH:-/home/occnj/occ_hub/error.log}"
SQLITE_DB_PATH="${SQLITE_DB_PATH:-/home/occnj/occ_hub/oasis.db}"
MAX_RESTARTS="${MAX_RESTARTS:-1}"
MIN_500S="${MIN_500S:-2}"
WINDOW_SECONDS="${WINDOW_SECONDS:-300}"
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

main() {
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

