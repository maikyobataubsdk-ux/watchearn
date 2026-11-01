# Earn Program - High-Paying PTC Platform & Telegram Channel Promotion Ecosystem

**Earn Program** is a complete, modern, production-ready "Watch Ads & Earn" PTC (Pay-To-Click) platform combined with an advanced **Telegram Mini App & Channel Promotion Ecosystem**. Integrated with high-paying ad networks (**Adsterra**, **HilltopAds**, **Monetag**), automated multi-channel bot broadcasts, QR payment verification, VIP Premium ad-bypass, and channel owner revenue sharing.

Features a dark glassmorphism UI, dual Hindi + English language toggle, 20-second non-skippable anti-cheat timer, automated 15% referral commission system, $5 / ₹400 minimum withdrawal support (UPI, Paytm, USDT, PayPal), dual SQLite + JSON persistent database, Telegram bot integration (`bot.py`), and a master admin panel at `/raj`.

---

## 🌟 Key Features Overview

1. **Brand Identity**: Branded as **Blazeculture** with official logo across website, Telegram Mini App, and bot greetings (`static/logo.png`).
2. **Master Admin Panel (`/raj`)**:
   - Master login (`rajpapa` / `28@RajPapa`).
   - **VIP Premium User Manager**: One-click toggle for user **Premium VIP status**. Premium users do NOT need to watch any ads to access services or claim tasks!
   - **Payment Settings Manager**: Configure UPI ID & QR Code Image URL dynamically.
   - **Promotion Manager**: Review paid channel promotion submissions with payment UTR reference IDs and trigger instant automated multi-channel broadcasts.
   - **Ad Network Script Manager**: Paste and execute Adsterra, HilltopAds, and Monetag ad codes on live site without server restart.
   - **User & Withdrawal Manager**: Adjust coin balances, ban/unban users, approve or reject cash withdrawals.
3. **Telegram Channel Promotion System**:
   - **Channel Link Analyzer**: Users input channel link to analyze title, username, and active members.
   - **Scan & Pay QR**: Users scan admin QR code to pay promotion budget via UPI/Paytm.
   - **Multi-Channel Broadcast**: Upon admin approval, the bot broadcasts promotion posts across all connected partner channels where the bot is admin.
4. **Channel Owner Revenue Sharing**:
   - Channel owners connect their Telegram channel by adding `@EarnProgramapp` as admin.
   - Real-time `ChatMemberUpdated` tracking records joins and leaves.
   - **Revenue Split**: Every 2 users joining via ad campaigns deducts ₹1.00 INR from promoter budget and credits **₹0.50 INR** directly to the channel owner's wallet.
5. **Telegram Bot (`bot.py`)**:
   - Modern inline layout matching custom designs.
   - Redesigned welcome greeting with Mini App button, Channel Promotions, Earn Coins, VIP Premium Pass, and Master Admin Broadcast (`/broadcast`).

---

## 🔑 Master Admin Access

The master admin panel is accessible via your browser:

- **Admin URL Route**: `https://yourdomain.com/raj` (or `http://localhost:8000/raj`)
- **Admin Username**: `rajpapa`
- **Admin Password**: `28@RajPapa`

---

## 💰 How to Setup QR Payment & Ad Codes in Admin Panel

1. Open your browser and navigate to `https://yourdomain.com/raj`.
2. Login using **Username**: `rajpapa` and **Password**: `28@RajPapa`.
3. **Payment QR Settings**: Enter your UPI ID (e.g. `earnprogram@upi`) and QR Code URL (e.g. `/static/logo.png` or hosted image link) and click **Save Payment Config**.
4. **Ad Network Script Manager**: Paste your high-paying network scripts into the corresponding textareas:
   - **Full-Page Interstitial Script** (Adsterra / HilltopAds Interstitial code)
   - **Popunder Script** (Adsterra / HilltopAds / Monetag Popunder code)
   - **Banner Ad HTML/JS Code**
5. Click **Save High-Paying Ad Codes**. Changes take effect immediately!

---

## 🚀 Complete Ubuntu VPS & Telegram Bot Deployment Guide

Follow this step-by-step guide to deploy **Blazeculture** web platform and Telegram Bot on a fresh Ubuntu 20.04 / 22.04 / 24.04 VPS.

### 1. System Update & Dependencies

Connect to your VPS via SSH and install required packages:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx git certbot python3-certbot-nginx
```

### 2. Clone Repository & Setup Virtual Environment

```bash
cd /opt
sudo git clone <YOUR_GIT_REPOSITORY_URL> blazeculture
cd /opt/blazeculture

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment Variables (`.env`)

Create a `.env` file in `/opt/blazeculture`:

```bash
cat << 'EOF' > /opt/blazeculture/.env
BOT_TOKEN=7591603597:AAG3-YourActualTelegramBotToken
ADMIN_USER_ID=7280907147
WEBAPP_URL=https://blazeculture.yourdomain.com
DATABASE_PATH=data.sqlite3
HOST=127.0.0.1
PORT=8000
EOF
```

### 4. Run Unit Tests & Verification

Verify everything is working:

```bash
TESTING=1 python3 -m unittest discover -s . -p "test_*.py"
```

Expected output: `Ran 9 tests ... OK`

---

### 5. Configure Systemd Production Services

#### A. Web Service (`blazeculture-web.service`)

Create the web service file:

```bash
sudo nano /etc/systemd/system/blazeculture-web.service
```

Paste the configuration:

```ini
[Unit]
Description=Blazeculture Web & Admin Application
After=network.target

[Service]
User=root
WorkingDirectory=/opt/blazeculture
EnvironmentFile=/opt/blazeculture/.env
ExecStart=/opt/blazeculture/venv/bin/uvicorn web:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

#### B. Telegram Bot Service (`blazeculture-bot.service`)

Create the Telegram bot service file:

```bash
sudo nano /etc/systemd/system/blazeculture-bot.service
```

Paste the configuration:

```ini
[Unit]
Description=Blazeculture Telegram Bot Service
After=network.target blazeculture-web.service

[Service]
User=root
WorkingDirectory=/opt/blazeculture
EnvironmentFile=/opt/blazeculture/.env
ExecStart=/opt/blazeculture/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start both services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable blazeculture-web blazeculture-bot
sudo systemctl start blazeculture-web blazeculture-bot
sudo systemctl status blazeculture-web blazeculture-bot
```

---

### 6. Nginx Reverse Proxy Setup

Create an Nginx server block for your domain (`blazeculture.yourdomain.com`):

```bash
sudo nano /etc/nginx/sites-available/blazeculture
```

Paste the Nginx configuration:

```nginx
server {
    listen 80;
    server_name blazeculture.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

Enable configuration and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/blazeculture /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

### 7. SSL Certificate Setup (Let's Encrypt HTTPS)

Obtain a free SSL certificate:

```bash
sudo certbot --nginx -d blazeculture.yourdomain.com
```

Select option **2** to automatically redirect HTTP traffic to HTTPS.

---

### 8. Security Hardening & Firewall (UFW)

Enable UFW firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 🗄️ Database Backup & JSON Sync

All user profiles, earnings, channel analytics, promotions, and settings are stored in SQLite (`data.sqlite3`).

To ensure transparent backups, the system automatically synchronizes a structured JSON export file (`db.json`) on every write transaction located at `/opt/blazeculture/db.json`.

---

## 📜 Project Structure Overview

```
.
├── config.py           # Environment variables & constants
├── db.py               # SQLite & JSON dual persistent database engine
├── web.py              # FastAPI Web Backend, API endpoints & /raj Master Admin
├── bot.py              # Telegram Bot Engine (aiogram 3.x, ChatMemberUpdated tracking, Mini App)
├── static/
│   ├── Index.html      # Glassmorphism UI (SPA with Blazeculture branding & Promotion Hub)
│   └── logo.png        # Official Blazeculture Logo
├── test_app.py         # Complete automated test suite
├── requirements.txt    # Python dependencies
└── README.md           # Master documentation & deployment guide
```
