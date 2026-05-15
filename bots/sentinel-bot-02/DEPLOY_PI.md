## 1) SQL
Run `bots/sentinel-bot-02/SQL_SETUP.sql` against `oasis.db`.

## 2) Install on Pi
```bash
sudo install -m 0755 bots/sentinel-bot-02/sentinel-bot-02.sh /usr/local/bin/sentinel-bot-02.sh
sudo install -m 0644 bots/sentinel-bot-02/sentinel-bot-02.service /etc/systemd/system/sentinel-bot-02.service
sudo install -m 0644 bots/sentinel-bot-02/sentinel-bot-02.timer /etc/systemd/system/sentinel-bot-02.timer
sudo cp bots/sentinel-bot-02/sentinel-bot-02.env.example /etc/sentinel-bot-02.env
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel-bot-02.timer
```

## 3) Manual Run
```bash
sudo /usr/local/bin/sentinel-bot-02.sh
```

