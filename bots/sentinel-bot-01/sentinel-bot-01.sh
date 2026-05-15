#!/usr/bin/env bash
set -euo pipefail

BOT_NAME="${BOT_NAME:-sentinel-bot-01}"
SERVICE_NAME="${SERVICE_NAME:-app-worker.service}"
THRESHOLD_SECONDS="${THRESHOLD_SECONDS:-120}"
VERIFY_SLEEP_SECONDS="${VERIFY_SLEEP_SECONDS:-10}"
MAX_FIX_ATTEMPTS="${MAX_FIX_ATTEMPTS:-3}"

: "${SQLITE_DB_PATH:?missing SQLITE_DB_PATH}"

HOST="$(hostname -s 2>/dev/null || hostname)"

now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(now_utc)] $*"; }

backoff_sleep() {
  local n="$1"
  local s=$((2 ** n))
  sleep "$s"
}

svc_state() {
  if systemctl is-failed --quiet "$SERVICE_NAME"; then echo "failed"; return; fi
  if systemctl is-active --quiet "$SERVICE_NAME"; then echo "active"; return; fi
  echo "inactive"
}

sqlite_scalar() {
  python3 - "$SQLITE_DB_PATH" "$1" <<'PY'
import sqlite3,sys
db=sys.argv[1]; sql=sys.argv[2]
conn=sqlite3.connect(db)
cur=conn.execute(sql)
row=cur.fetchone()
conn.close()
print("" if row is None or row[0] is None else row[0])
PY
}

get_heartbeat_epoch() {
  local iso
  iso="$(sqlite_scalar "select last_heartbeat from system_health where service_name='${SERVICE_NAME}' limit 1;")"
  python3 - "$iso" <<'PY'
import sys,datetime
s=(sys.argv[1] or "").strip()
if not s:
  print(0); raise SystemExit
try:
  print(int(datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()))
except Exception:
  print(0)
PY
}

get_health_status_text() {
  sqlite_scalar "select coalesce(status,'') from system_health where service_name='${SERVICE_NAME}' limit 1;"
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

retry4() {
  local tries=0
  while true; do
    if "$@"; then return 0; fi
    tries=$((tries+1))
    if (( tries >= 4 )); then return 1; fi
    backoff_sleep "$tries"
  done
}

is_healthy() {
  local state hb_epoch now_epoch age
  state="$(svc_state)"
  [[ "$state" == "active" ]] || return 1
  hb_epoch="$(get_heartbeat_epoch)"
  [[ "$hb_epoch" -gt 0 ]] || return 1
  now_epoch="$(date +%s)"
  age=$((now_epoch - hb_epoch))
  (( age <= THRESHOLD_SECONDS ))
}

restart_service() { systemctl restart "$SERVICE_NAME"; }

kill_and_start() {
  local pid proc
  pid="$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo "")"
  proc=""
  if [[ -n "$pid" && "$pid" != "0" ]]; then
    proc="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
  fi
  if command -v killall >/dev/null 2>&1 && [[ -n "$proc" ]]; then
    killall -9 "$proc" >/dev/null 2>&1 || true
  elif [[ -n "$pid" && "$pid" != "0" ]]; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  systemctl start "$SERVICE_NAME" || true
}

main() {
  local state issue dedup_key details
  state="$(svc_state)"

  if [[ "$state" == "failed" ]]; then
    issue="systemd_failed"
  else
    local hb_epoch now_epoch age
    hb_epoch="$(get_heartbeat_epoch || echo 0)"
    now_epoch="$(date +%s)"
    age=$((now_epoch - hb_epoch))
    if [[ "$state" == "active" && ( "$hb_epoch" -le 0 || "$age" -gt THRESHOLD_SECONDS ) ]]; then
      issue="zombie_heartbeat_stale"
    else
      exit 0
    fi
  fi

  dedup_key="$(date -u +%Y%m%d%H%M)-${HOST}-${SERVICE_NAME}-${issue}"
  details="$(python3 - <<PY
import json
print(json.dumps({
  "systemd_state":"$state",
  "health_status":"$(get_health_status_text || true)",
  "threshold_seconds":int("$THRESHOLD_SECONDS")
}))
PY
)"

  local attempt=0
  while (( attempt < MAX_FIX_ATTEMPTS )); do
    attempt=$((attempt+1))
    local fix="systemctl restart"
    if (( attempt == MAX_FIX_ATTEMPTS )); then fix="killall -9 + systemctl start"; fi

    retry4 incident_upsert "$issue" "$fix" 0 "$attempt" "$details" "$dedup_key" || true

    if (( attempt == MAX_FIX_ATTEMPTS )); then
      kill_and_start
    else
      restart_service || true
    fi

    sleep "$VERIFY_SLEEP_SECONDS"
    if is_healthy; then
      retry4 incident_upsert "$issue" "$fix" 1 "$attempt" "$details" "$dedup_key" || true
      exit 0
    fi
  done

  retry4 incident_upsert "$issue" "circuit_breaker_stop" 0 "$MAX_FIX_ATTEMPTS" "$details" "$dedup_key" || true
  exit 2
}

main "$@"
