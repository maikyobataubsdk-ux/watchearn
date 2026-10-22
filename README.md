# AdEarn - High-Paying "Watch Ads & Earn" PTC Platform

A complete, modern, production-ready "Ad Watching & Earn" PTC (Pay-To-Click) platform designed for maximum ad network monetization with **Adsterra**, **HilltopAds**, **Monetag**, and custom networks.

Features a dark glassmorphism UI, dual Hindi + English language toggle, 20-second non-skippable anti-cheat countdown timer, automated 15% referral commission system, $5 / ₹400 minimum withdrawal support (UPI, Paytm, USDT, PayPal), dual SQLite + JSON persistent database, and a master admin panel.

---

## 🔑 Master Admin Access

The master admin panel is accessible via your browser:

- **Admin URL Route**: `https://yourdomain.com/raj`
- **Admin Username**: `rajpapa`
- **Admin Password**: `28@RajPapa`

### Admin Features:
- View real-time platform statistics (total users, total ads watched, global paid out).
- **Ad Network Script Manager**: Directly paste and update high-paying ad codes for **Adsterra**, **HilltopAds**, and **Monetag** (Interstitial, Popunder, Banner, Direct Link) without editing code or restarting server.
- **User Manager**: Search users, adjust coin balance, ban/unban abusive users.
- **Withdrawal Manager**: Approve payouts or reject requests with automatic coin refund.

---

## 💰 How to Paste Adsterra / HilltopAds / Monetag Ad Codes

1. Open your browser and navigate to `https://yourdomain.com/raj`.
2. Login using **Username**: `rajpapa` and **Password**: `28@RajPapa`.
3. Go to the **Ad Network Script Manager** section.
4. Paste your high-paying network scripts into the corresponding textareas:
   - **Full-Page Interstitial Script** (Adsterra / HilltopAds Interstitial code)
   - **Popunder Script** (Adsterra / HilltopAds / Monetag Popunder code)
   - **Banner Ad HTML/JS Code** (300x250 or 728x90 banner code)
5. Click **Save High-Paying Ad Codes**. Changes take effect immediately across all users' web sessions!

---

## 🚀 Complete Ubuntu VPS Deployment Guide

Follow this step-by-step guide to deploy this platform on a fresh Ubuntu 20.04 / 22.04 / 24.04 VPS.

### 1. System Update & Dependencies

Connect to your VPS via SSH and install required packages:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx git certbot python3-certbot-nginx
```

### 2. Setup Project Directory & Virtual Environment

```bash
cd /opt
sudo git clone <YOUR_GIT_REPOSITORY_URL> adearn
cd /opt/adearn

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements & test client dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install httpx
```

### 3. Verify Local Execution

Test running the FastAPI server locally:

```bash
python3 -m unittest discover -s . -p "test_*.py"
```

Expected output: `Ran 6 tests ... OK`

### 4. Configure Systemd Production Service

Create a systemd service file so the app automatically runs and restarts on VPS reboot:

```bash
sudo nano /etc/systemd/system/adearn.service
```

Paste the following configuration:

```ini
[Unit]
Description=AdEarn PTC Web Application
After=network.target

[Service]
User=root
WorkingDirectory=/opt/adearn
ExecStart=/opt/adearn/venv/bin/uvicorn web:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable adearn
sudo systemctl start adearn
sudo systemctl status adearn
```

---

### 5. Nginx Reverse Proxy Setup

Create an Nginx server block for your domain (e.g. `earn.yourdomain.com`):

```bash
sudo nano /etc/nginx/sites-available/adearn
```

Paste the following Nginx configuration:

```nginx
server {
    listen 80;
    server_name earn.yourdomain.com;

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

Enable the configuration and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/adearn /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

### 6. SSL Certificate Setup (Let's Encrypt HTTPS)

Obtain a free SSL certificate to enable HTTPS (required for ad networks):

```bash
sudo certbot --nginx -d earn.yourdomain.com
```

Select option **2** to automatically redirect HTTP traffic to HTTPS.

---

### 7. Security Hardening & Firewall (UFW)

Enable UFW firewall and allow HTTP/HTTPS/SSH traffic:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 🗄️ Database Backup & JSON Sync

All user profiles, earnings, withdrawals, and settings are stored in SQLite (`data.sqlite3`).

To ensure transparent backups, the system automatically synchronizes a structured JSON export file (`db.json`) on every write transaction located at `/opt/adearn/db.json`.

---

## 📜 Project Structure Overview

```
.
├── config.py           # Configuration & Environment loading
├── db.py               # SQLite & JSON dual persistent database engine
├── web.py              # FastAPI Web Backend, API endpoints & /raj Admin panel
├── static/
│   └── Index.html      # Glassmorphism UI (SPA with Hindi/English & Ad Engine)
├── test_app.py         # Test suite
├── requirements.txt    # Python dependencies
└── README.md           # Master documentation & deployment guide
```
