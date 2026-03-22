# Oasis Hub — App Documentation
**Version 2.0 · Oasis Christian Centre, Rahway NJ**

---

## What Is Oasis Hub?

Oasis Hub is a **mobile-first web app** running on a Raspberry Pi inside the church. Members and guests navigate to it by scanning a QR code that opens:

```
https://occnj.tail812f78.ts.net/hub
```

The app is entirely self-hosted — no monthly software fees, no third-party app store, no internet dependency for the church's own content. It runs 24/7 on the Pi at local IP `192.168.1.32` and is reachable over Tailscale at `100.90.22.46`.

---

## Who Is It For?

- **First-time guests** who scan a QR code at the welcome table or on a seat card
- **Regular members** who want quick access to give, pray, serve, or connect
- **Church staff** who manage content via the admin panel

---

## What Can Users Do?

| Feature | What It Does |
|---|---|
| **Hub (Dashboard)** | Home screen with quick-access tiles and bottom navigation |
| **You Said Yes** | Downloads the "You Said Yes" PDF for new believers |
| **Ministries** | Tap any ministry to see its description and website link |
| **Upcoming Events** | Calendar view of upcoming and past church events |
| **Prayer Request** | Submit a prayer with type, optional privacy flag, sent to pastoral team |
| **Our Beliefs** | Swipeable cards with each doctrinal statement and scripture |
| **Leadership** | Photo grid of leaders; tap to read full bio |
| **Serve** | Browse volunteer opportunities by ministry category |
| **Connect Card** | Full membership/visitor connection form |
| **Contact Us** | Church info (address, hours, phone, email) + contact form |
| **About Oasis** | Customizable page with hero image and church description |
| **Social Media** | Links to Instagram, Facebook, YouTube, and Twitter/X |
| **Give** | Opens the church's giving portal |

---

## Admin Panel

Access at: `https://occnj.tail812f78.ts.net/admin`
Default login: `admin` / `oasis2025` ← **change this immediately**

### What You Can Manage

**Overview**
- **Dashboard** — Live stats: views today, total views, connect cards, prayer requests
- **Analytics** — Top visited pages, daily view history, session counts

**Responses**
- **Connect Cards** — View all connection form submissions, click for full detail
- **Prayer Requests** — View all submitted prayer requests; private ones are flagged

**Content**
- **Events** — Add/edit/delete events with date, time, location, description
- **Leadership** — Add/edit/delete leaders, upload photos, set order
- **Ministries** — Add/edit/delete ministry listings with icon, description, link
- **Beliefs** — Add/edit/delete belief statements with scripture references
- **Serve** — Add/edit/delete volunteer categories; add/remove roles within each

**Config**
- **Settings** — Upload logo, set all URLs, customize About page, set all email destinations, manage social media links
- **Users** *(Superadmin only)* — Add or remove admin accounts, set roles
- **Password** — Change your own password

### User Roles
- **Editor** — Can manage all content (events, leaders, beliefs, ministries, serve, submissions)
- **Superadmin** — All editor permissions + can manage admin users

---

## How Analytics Work

Every page the user visits is quietly logged with:
- Timestamp
- Page name
- IP address (anonymized via X-Forwarded-For)
- Browser/device (User-Agent string, 200 chars max)
- Session ID (random, stored in browser session cookie)

No personal data is collected without the user explicitly submitting a form. The analytics dashboard shows which pages are most visited and how traffic trends over time — useful for understanding what content resonates with guests.

---

## Tech Stack

| Component | Technology |
|---|---|
| Web Framework | Python / Flask |
| Database | SQLite (file: `oasis.db`) |
| Production Server | Gunicorn (2 workers) |
| Remote Access | Tailscale (Tailnet funnel) |
| Hardware | Raspberry Pi (hostname: occnj) |
| Email | Office365 SMTP (media@oasisnj.net) |
| Fonts | DM Serif Display + DM Sans (Google Fonts) |
| Icons | Bootstrap Icons |

---

## File Structure

```
occ_hub/
├── member.py           ← Flask app — all routes and logic
├── oasis.db            ← SQLite database (auto-created)
├── oasis-hub.service   ← Systemd service file
├── setup_pi.sh         ← One-command Pi setup script
├── static/
│   ├── logo.png
│   ├── sermon.png
│   ├── gate.jpg
│   ├── splash.jpg
│   └── uploads/        ← Leader photos, logo uploads, hero images
└── templates/
    ├── splash.html, gate.html, hub.html
    ├── beliefs.html, ministries.html, leadership.html
    ├── serve.html, calendar.html, prayer.html
    ├── connect.html, contact.html, about.html, social.html
    └── admin/
        ├── base.html, login.html, dashboard.html
        ├── analytics.html, submissions.html, submission_detail.html
        ├── prayers.html, events.html, event_form.html
        ├── leaders.html, leader_form.html
        ├── ministries.html, ministry_form.html
        ├── beliefs.html, belief_form.html
        ├── serve.html, serve_cat_form.html
        ├── settings.html, users.html, user_form.html, password.html
```

---

## First-Time Install on Pi

```bash
# SSH into the Pi
ssh occnj@192.168.1.32

# Clone the repo
git clone https://github.com/occnj/occhub.git /home/occnj/occ_hub

# Run setup (creates venv, installs packages, starts service)
cd /home/occnj/occ_hub
sudo bash setup_pi.sh
```

## Deploying Updates

```bash
# On your Mac — push changes
git add .
git commit -m "describe change"
git push

# On the Pi — pull and restart (takes ~2 seconds)
ssh occnj@192.168.1.32
cd /home/occnj/occ_hub && git pull && sudo systemctl restart oasis-hub
```

**Check logs:**
```bash
sudo journalctl -u oasis-hub -f
cat /home/occnj/occ_hub/error.log
```

---

## Promo Video Script Ideas

**Opening shot:** QR code on a welcome table. Someone scans it.

> "Whether you're visiting Oasis for the first time, or you've been here for years — everything you need is right here."

**Show:** Hub screen loading on a phone.

> "Find sermons. Connect with a ministry. Submit a prayer request. Download your You Said Yes guide. Give online. It all starts with one scan."

**Show:** Upcoming events calendar, leadership bios, serve opportunities.

> "Oasis Hub keeps you connected — not just on Sundays, but every day of the week."

**Show:** QR code again.

> "Scan the code. We're glad you're here."

---

*Built for Oasis Christian Centre · Rahway, NJ · occnj.tail812f78.ts.net*
