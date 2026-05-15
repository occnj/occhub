#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${SQLITE_DB_PATH:-/home/occnj/occ_hub/oasis.db}"
SERVICE_NAME="app-worker.service"

while true; do
  python3 - "$DB_PATH" "$SERVICE_NAME" <<'PY'
import sqlite3, sys, datetime
db, service = sys.argv[1:3]
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
conn = sqlite3.connect(db)
conn.execute("""
create table if not exists system_health (
  service_name text primary key,
  last_heartbeat text not null default '',
  status text not null default 'starting',
  updated_at text not null default ''
)
""")
conn.execute(
    "insert into system_health (service_name,last_heartbeat,status,updated_at) values (?,?,?,?) "
    "on conflict(service_name) do update set last_heartbeat=excluded.last_heartbeat, status=excluded.status, updated_at=excluded.updated_at",
    (service, now, "ok", now),
)
conn.commit()
conn.close()
PY
  sleep 20
done

