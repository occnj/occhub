-- Sentinel Bot 02 (Error Log Monitor) schema (SQLite)
-- Uses the existing incident_logs table from sentinel-bot-01.

CREATE TABLE IF NOT EXISTS bot_log_state (
  bot_name TEXT PRIMARY KEY,
  last_offset INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TRIGGER IF NOT EXISTS bot_log_state_updated_at
AFTER UPDATE ON bot_log_state
FOR EACH ROW
BEGIN
  UPDATE bot_log_state
  SET updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  WHERE bot_name = NEW.bot_name;
END;

