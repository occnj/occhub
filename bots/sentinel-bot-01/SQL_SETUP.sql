-- Sentinel Bot 01 (Heartbeat Monitor) schema (SQLite)
-- Run against oasis.db

CREATE TABLE IF NOT EXISTS system_health (
  service_name TEXT PRIMARY KEY,
  last_heartbeat TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  status TEXT NOT NULL DEFAULT 'starting',
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TRIGGER IF NOT EXISTS system_health_set_updated_at
AFTER UPDATE ON system_health
FOR EACH ROW
BEGIN
  UPDATE system_health
  SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  WHERE service_name = NEW.service_name;
END;

CREATE TABLE IF NOT EXISTS incident_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  bot_name TEXT NOT NULL,
  host TEXT NOT NULL,
  service_name TEXT NOT NULL,
  issue TEXT NOT NULL,
  fix_applied TEXT NOT NULL DEFAULT '',
  success INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0,
  details TEXT NOT NULL DEFAULT '{}',
  dedup_key TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS incident_logs_created_at_idx ON incident_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS incident_logs_service_idx ON incident_logs (service_name, created_at DESC);
