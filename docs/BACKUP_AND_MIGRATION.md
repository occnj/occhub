# OccHub: backups and moving to the droplet

Everything OccHub knows lives in two places on the Pi:

| What | Where | Why it matters |
|---|---|---|
| The database | `oasis.db` | all content, prayer requests, connect cards, admin accounts, analytics |
| Photos and documents | `static/uploads/` | every image and sermon file an admin has uploaded |

`tools/occhub_backup.py` copies both to the droplet every night, and puts them back on any
machine on demand. The same tool does the one-time move from the Pi to the droplet.

```
   Pi (church)                                         Droplet
 ┌──────────────┐   nightly, over SSH, encrypted   ┌────────────────────────────┐
 │ oasis.db     │ ───────────────────────────────► │ /srv/occhub-backup/        │
 │ uploads/     │   (only new photos are sent)     │   db/       snapshots      │
 └──────────────┘                                  │   uploads/  all the photos │
                                                   └────────────────────────────┘
```

**What is not backed up on purpose:** the `.env` file (it holds `SECRET_KEY` and the email key).
Keep a copy of it in your password manager. The code itself is in GitHub.

## How it protects you

- **Consistent database copy.** The database is in WAL mode and is being written to while visitors
  use the app; copying the file can lose recent changes or produce a broken copy. The tool uses
  SQLite's online backup, then checks the copy with `PRAGMA integrity_check` before it goes anywhere.
- **Encrypted before it leaves the Pi.** The database contains prayer requests, names, emails and
  phone numbers. It is compressed and encrypted (AES-256, `gpg`) with a passphrase only you hold.
  The tool refuses to send it unencrypted unless you explicitly allow that.
- **Photos are incremental.** Upload file names are random and never reused, so each night only the
  new ones are sent. Nothing on the droplet is deleted unless you turn that on (`BACKUP_SYNC_DELETE`),
  so a mistake on the Pi can't erase the backup.
- **A backup only counts once it is whole.** A snapshot is trusted only after its manifest (checksum,
  sizes, row counts) arrives, and the manifest is sent last. A half-sent file is never restored.
- **The Pi's key can do one thing.** On the droplet it can only run `rrsync` inside
  `/srv/occhub-backup`: no shell, no commands, no port forwarding, no reading or writing anywhere else.
  If the Pi were ever compromised, the attacker still can't log in to the droplet.
- **Alerts.** Optional: a free check at healthchecks.io emails you if a backup fails *or stops
  running* (for instance, the Pi is switched off). The droplet also checks every day that the newest
  backup is complete and less than 30 hours old.

## One-time setup

### 1. On the Pi: make a key and a passphrase

```bash
ssh occnj@192.168.1.32
cd /home/occnj/occ_hub && git pull
sudo apt install -y rsync gnupg                 # usually already there

# a key that is only used for backups (no passphrase on the key, so the timer can use it)
ssh-keygen -t ed25519 -N '' -C occhub-pi-backup -f ~/.ssh/occhub_backup

# the passphrase that encrypts the database
openssl rand -base64 32 > ~/.occhub-backup-passphrase
chmod 600 ~/.occhub-backup-passphrase
cat ~/.occhub-backup-passphrase                 # SAVE THIS in your password manager now
```

> **Without this passphrase the backups cannot be opened.** Store a copy somewhere that is not the
> Pi and not the droplet.

### 2. On the droplet: create the receiving end

Copy the Pi's *public* key over (`cat ~/.ssh/occhub_backup.pub` on the Pi, paste into a file on the
droplet), get the code, and run the setup script:

```bash
ssh root@<droplet>
apt install -y git
git clone https://github.com/occnj/occhub.git /opt/occhub-tools     # private repo? use a deploy key or token
nano /root/pi_backup_key.pub                                         # paste the public key line
bash /opt/occhub-tools/deploy/droplet_setup_backup_receiver.sh /root/pi_backup_key.pub
```

`/opt/occhub-tools` is the permanent home of the backup tool on the droplet: the daily check job runs
from it, and `git -C /opt/occhub-tools pull` updates it. (The app itself goes in `/home/occhub/occ_hub`.)

### 3. On the Pi: point it at the droplet and test

```bash
cd /home/occnj/occ_hub
cp deploy/backup.env.example .backup.env && chmod 600 .backup.env
nano .backup.env          # set BACKUP_REMOTE=occbackup@<droplet address>:  and check the two file paths

python3 tools/occhub_backup.py push
python3 tools/occhub_backup.py status
```

The first run prints the snapshot size, how many photos it sent and `done`. Run it a second time:
it should finish in a couple of seconds, because nothing new needs sending.

### 4. On the Pi: make it automatic

```bash
sudo cp deploy/occhub-backup.service deploy/occhub-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now occhub-backup.timer
systemctl list-timers occhub-backup.timer          # shows the next run (2:30am)
sudo systemctl start occhub-backup.service         # run one now to prove it works
journalctl -u occhub-backup -n 20
```

Optional: sign up at healthchecks.io, create a check with a 1-day period, and put its URL in
`.backup.env` as `BACKUP_PING_URL=`.

### 5. Prove the backup is usable (do this once, then every few months)

On the droplet:

```bash
T=/opt/occhub-tools/tools/occhub_backup.py
# quick check: checksum, freshness, photo count. Needs no passphrase; this is what the daily job runs
python3 $T verify --quick --source /srv/occhub-backup --max-age-hours 30

# full check: decrypts and tests the database itself
install -m 600 /dev/null /root/pass && nano /root/pass            # paste the passphrase
python3 $T verify --source /srv/occhub-backup --passphrase-file /root/pass
shred -u /root/pass                                                # don't leave it lying around
```

## Moving OccHub from the Pi to the droplet

Prepare the droplet while the Pi keeps serving visitors, then switch in a short window.

### A. Prepare the droplet (no visitor impact)

```bash
# as root
apt install -y python3-venv python3-pip git caddy rsync gnupg
adduser --disabled-password --gecos "" occhub
sudo -u occhub git clone https://github.com/occnj/occhub.git /home/occhub/occ_hub
cd /home/occhub/occ_hub
sudo -u occhub python3 -m venv venv
sudo -u occhub venv/bin/pip install -r requirements.txt

# secrets: a NEW SECRET_KEY is fine (admins just sign in again). Copy the Resend values from the Pi's .env.
sudo -u occhub tee .env >/dev/null <<EOF
SECRET_KEY=$(openssl rand -hex 32)
RESEND_API_KEY=re_xxxxxxxx
MAIL_FROM=Oasis Hub <media@oasisnj.net>
EOF
chmod 600 .env
cp deploy/droplet/occhub.service /etc/systemd/system/ && systemctl daemon-reload
```

Do step 2 above (receiver) if you haven't, and let one nightly backup arrive.

### B. Rehearse with a copy (no visitor impact)

Restore runs as **root** (only root and the backup account can read `/srv/occhub-backup`, by design);
the tool hands the restored files to the app's user automatically.

```bash
install -m 600 /dev/null /root/pass && nano /root/pass          # paste the passphrase
python3 /opt/occhub-tools/tools/occhub_backup.py --app-dir /home/occhub/occ_hub restore \
     --source /srv/occhub-backup --passphrase-file /root/pass
systemctl start occhub
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5500/hub      # 200
```

Put the Caddy block from `deploy/droplet/Caddyfile.example` in `/etc/caddy/Caddyfile` (use your real
hostname, point its DNS A record at the droplet) and `systemctl reload caddy`. Open the new address,
sign in to `/admin`, click around, check photos show.

### C. Cut over (a few minutes)

1. **Pi:** `sudo systemctl stop oasis-hub` (visitors now see an error; do this at a quiet time).
2. **Pi:** one last backup: `python3 tools/occhub_backup.py push`.
3. **Droplet:**
   ```bash
   systemctl stop occhub
   python3 /opt/occhub-tools/tools/occhub_backup.py --app-dir /home/occhub/occ_hub restore \
        --source /srv/occhub-backup --passphrase-file /root/pass --force   # old database kept as oasis.db.before-restore-<time>
   systemctl start occhub
   shred -u /root/pass
   ```
4. Check the new address end to end. Submit a test prayer request; confirm the email arrives.
5. Update where people find you (below).

**Rollback:** the Pi is untouched. If anything looks wrong, `sudo systemctl start oasis-hub` on the Pi.
Keep the Pi's data for a few weeks before wiping it.

### What changes for visitors

- **The address.** QR codes and links point at `occnj.tail812f78.ts.net`, which is tied to the Pi. Use a
  real domain on the droplet and reprint QR codes. To keep old printed codes working for a while, leave
  the Pi's funnel running with a small redirect to the new address.
- **Home-screen apps.** A web app added to a phone's home screen is bound to the address it was added
  from. People who installed the old one need to add the new one.
- **Admins** sign in again (new `SECRET_KEY`). Passwords and accounts come across with the database.

### After the move: back up the droplet too

Once the droplet is the live copy, the Pi no longer is. Don't let the droplet be the only copy:

- Turn on DigitalOcean weekly droplet backups (a few dollars a month), **and**
- point the same tool at a second place. On the droplet, `cp deploy/backup.env.example .backup.env`, set
  `BACKUP_REMOTE` to another machine you control that has run the receiver script (for example the
  Pibase droplet, using a different `BACKUP_USER` and `BACKUP_ROOT` so it doesn't mix with Pibase's own
  data), and install the timer there too after editing the unit's `User=` and paths to `occhub` /
  `/home/occhub/occ_hub`.

## Restoring after a disaster

On any machine with the code, a Python venv, `rsync` and `gnupg` (a new droplet works):

```bash
python3 tools/occhub_backup.py restore --source /srv/occhub-backup --passphrase-file /path/to/pass
python3 tools/occhub_backup.py restore --source /srv/occhub-backup --snapshot oasis-20261010-023000 --passphrase-file ...   # a specific day
```

It refuses to overwrite an existing database unless you add `--force` (and then keeps the old one aside),
checks the checksum and database integrity before touching anything, and reports any photo the
database refers to that is missing from the backup. `--dry-run` only verifies.

## Housekeeping commands

```bash
python3 maintenance.py                    # remove finished one-time events
python3 maintenance.py --clean-uploads --dry-run    # list photos nothing refers to
python3 maintenance.py --clean-uploads              # delete them (only files older than 24h)
```

The same is available to superadmins under **Storage** in the admin menu. Take a backup before
deleting; deleted files are gone.

## Troubleshooting

| You see | Meaning / fix |
|---|---|
| `refusing to send an unencrypted database` | `BACKUP_PASSPHRASE_FILE` is not set in `.backup.env` |
| `... is readable by other users; run: chmod 600 ...` | do that |
| `Permission denied (publickey)` | the public key on the droplet isn't the one the Pi uses; re-run the receiver script with the right `.pub` |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | the droplet was rebuilt; on the Pi run `ssh-keygen -R <droplet address>` |
| `rrsync error: SSH_ORIGINAL_COMMAND does not run rsync` | expected if you try to open a shell with the backup key; that key is deliberately limited |
| `gpg decrypt failed` | wrong passphrase file |
| `checksum does not match its manifest` | that snapshot was damaged; restore the previous one with `--snapshot` |
| `another backup is already running` | a previous run is still going; wait or check `ps` |
| `rsync exit 23` on the first push | a file or folder isn't readable by the backup user; check ownership of `static/uploads` |

## Settings reference (`.backup.env`)

See `deploy/backup.env.example`; every key is explained there. Real environment variables override the file.

## How this was tested

Automated: `tests/test_backup.py` (24 tests) run the real tool with real `rsync` and `gpg`, including
data that exists only in the database's write-ahead log, damaged and half-sent backups, wrong passphrases,
overwrite protection, and an unreachable monitor.
By hand on Ubuntu 24.04: the receiver script, a real `sshd`, a push over SSH with the restricted key, the
key being refused a shell, commands, port forwarding and paths outside the backup folder, the daily check
job, a restore into a fresh folder, and the real app starting from the restored data.
**Not yet run:** on the actual Pi (Raspberry Pi OS) and a real droplet, and the two systemd units
(no systemd in the test environment). The first real push and the step-5 verification are the proof.
