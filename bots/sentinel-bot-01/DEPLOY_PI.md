## 1) SQL
- Run `SQL_SETUP.sql` against `oasis.db` (creates `system_health` + `incident_logs`).
```bash
cd ~/occ_hub
python3 - <<'PY'
import sqlite3, pathlib
db_path = pathlib.Path('oasis.db')
sql_path = pathlib.Path('bots/sentinel-bot-01/SQL_SETUP.sql')
conn = sqlite3.connect(db_path)
conn.executescript(sql_path.read_text())
conn.commit()
conn.close()
print("ok")
PY
```

## 2) Install on Pi
```bash
sudo install -m 0755 bots/sentinel-bot-01/app-worker.sh /usr/local/bin/app-worker.sh
sudo install -m 0644 bots/sentinel-bot-01/app-worker.service /etc/systemd/system/app-worker.service
sudo install -m 0755 bots/sentinel-bot-01/sentinel-bot-01.sh /usr/local/bin/sentinel-bot-01.sh
sudo install -m 0644 bots/sentinel-bot-01/sentinel-bot-01.service /etc/systemd/system/sentinel-bot-01.service
sudo install -m 0644 bots/sentinel-bot-01/sentinel-bot-01.timer /etc/systemd/system/sentinel-bot-01.timer
sudo cp bots/sentinel-bot-01/sentinel-bot-01.env.example /etc/sentinel-bot-01.env
sudo nano /etc/sentinel-bot-01.env
sudo systemctl daemon-reload
sudo systemctl enable --now app-worker.service
sudo systemctl enable --now sentinel-bot-01.timer
```

## 3) Status / Logs
```bash
systemctl status app-worker.service --no-pager
systemctl status sentinel-bot-01.timer --no-pager
journalctl -u sentinel-bot-01.service -n 200 --no-pager
```

## 4) Manual Run
```bash
sudo /usr/local/bin/sentinel-bot-01.sh
```

## 5) Force a test
```bash
sudo systemctl stop app-worker.service
sudo /usr/local/bin/sentinel-bot-01.sh
```
