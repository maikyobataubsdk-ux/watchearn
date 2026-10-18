# Telegram Channel Gate Mini App (Monetag rewarded-ad starter)

This project connects a Telegram bot, a Telegram Mini App, force-subscription checks, and a target channel invite link. The target channel can be configured to deliver either a direct one-person invite link or a join-request link.

**Important monetization limitation:** this project intentionally uses only Monetag's *Rewarded Interstitial* flow for the unlock step. Do not require users to view/click ads from every network in your earlier list. Adsterra and HilltopAds explicitly prohibit incentivized traffic; using them in a forced-ad-for-access loop can lead to rejected inventory, withheld earnings, or account suspension. Add other networks only in placements and formats their current publisher terms explicitly permit. Get written approval from Monetag for the exact “watch rewarded ad to receive a Telegram channel invite” reward before running it with real traffic.

The bot gives access only when the backend receives a Monetag server-side postback with `reward_event_type=valued` for an `impression`. The browser alone cannot grant the reward. It uses SQLite for idempotent ad-event handling and saved campaign/unlock records.

## Project files

- `bot.py` — Telegram bot commands, force-subscription check, campaign links, and direct/request invite generation.
- `web.py` — FastAPI endpoints, Telegram `initData` signature validation, rewarded-ad intent/status APIs, and Monetag postback endpoint.
- `static/Index.html` — modern responsive Telegram Mini App UI with Telegram WebApp theme and haptics support.
- `db.py` — SQLite database and one-time reward handling.
- `telegram_auth.py` — HMAC validation for Telegram Mini App `initData`.
- `wrangler.toml` — Cloudflare deployment setup.
- `.env.example` — environment configuration template.

## 1. Create the Telegram bot

1. Create a bot with `@BotFather` and copy its token.
2. Obtain your numeric Telegram user ID and set it as `ADMIN_USER_ID`.
3. Add the bot as an administrator to both the force-subscription channel (if used) and the target channel. It must be able to manage/create invite links. For membership checks, Telegram requires the bot to be an administrator in the force-subscription chat.
4. Host the Mini App on a domain with valid HTTPS. Telegram Web Apps require HTTPS for the web URL.

## 2. Cloudflare Deployment Options

### Option A: Cloudflare Tunnel (Recommended)
Cloudflare Tunnel allows you to securely expose your FastAPI app & Telegram Mini App without opening public ports.

1. **Install cloudflared on your server**:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```
2. **Authenticate & Create Tunnel**:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create telegram-gate
   ```
3. **Route Traffic**:
   ```bash
   cloudflared tunnel route dns telegram-gate miniapp.yourdomain.com
   ```
4. **Configure `~/.cloudflared/config.yml`**:
   ```yaml
   tunnel: <TUNNEL_UUID>
   credentials-file: /root/.cloudflared/<TUNNEL_UUID>.json

   ingress:
     - hostname: miniapp.yourdomain.com
       service: http://localhost:8000
     - service: http_status:404
   ```
5. **Run Tunnel as Service**:
   ```bash
   sudo cloudflared service install
   sudo systemctl start cloudflared
   ```

### Option B: Cloudflare DNS Proxy (Orange Cloud) + Nginx
If running on a VPS behind Cloudflare CDN:
- Set your DNS A record for `miniapp.yourdomain.com` to **Proxied (Orange Cloud)**.
- Set SSL/TLS encryption mode in Cloudflare dashboard to **Full (strict)**.

Example Nginx block behind Cloudflare Proxy:
```nginx
server {
    listen 443 ssl http2;
    server_name miniapp.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/miniapp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/miniapp.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 3. Configure the project

On a Debian/Ubuntu VPS with Python 3.11 or newer:

```bash
mkdir -p /opt/channel-gate
cd /opt/channel-gate
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Set these values in `.env`:

```dotenv
BOT_TOKEN=YOUR_BOT_TOKEN
ADMIN_USER_ID=YOUR_NUMERIC_TELEGRAM_USER_ID
WEBAPP_URL=https://miniapp.yourdomain.com
MONETAG_SDK_URL=PASTE_THE_EXACT_SDK_SCRIPT_URL_FROM_MONETAG_DASHBOARD
MONETAG_ZONE_ID=PASTE_MAIN_ZONE_ID
MONETAG_POSTBACK_SECRET=USE_A_LONG_RANDOM_ALPHANUMERIC_SECRET
DATABASE_PATH=/opt/channel-gate/data.sqlite3
HOST=127.0.0.1
PORT=8000
```

## 4. Run the web app and bot

Run these in two separate terminal sessions or use `systemd` / `pm2`:

```bash
cd /opt/channel-gate
source .venv/bin/activate
uvicorn web:app --host 127.0.0.1 --port 8000
```

In another session:

```bash
cd /opt/channel-gate
source .venv/bin/activate
python bot.py
```

Test `https://miniapp.yourdomain.com/health` and expect `{"ok":true}`.

## 5. Configure Monetag postback

Set the postback URL for the **main SDK zone** in Monetag dashboard:

```text
https://miniapp.yourdomain.com/monetag/postback?secret=YOUR_LONG_RANDOM_SECRET&ymid={ymid}&event_type={event_type}&reward_event_type={reward_event_type}&estimated_price={estimated_price}&telegram_id={telegram_id}&zone_id={zone_id}&sub_zone_id={sub_zone_id}&request_var={request_var}
```

The `secret` value must exactly match `MONETAG_POSTBACK_SECRET` in `.env`.

## 6. Register the Mini App URL with Telegram

Use `@BotFather` to configure a Mini App for the bot and set the public HTTPS URL to `https://miniapp.yourdomain.com/`.

## 7. Bot admin commands

- `/setforce @RequiredChannel` — set required force-sub channel.
- `/offforce` — turn off force-sub check.
- `/settarget @TargetChannel direct` — set target channel with direct one-use invite link.
- `/settarget @TargetChannel request` — set target channel with join-request invite link.
- `/genlink` — generate campaign link (`https://t.me/YourBot?start=TOKEN`).
- `/status` — view active config.

## 8. How a user's flow works

1. User opens campaign link.
2. Force-sub check verified if enabled.
3. User opens Telegram Mini App with smooth animations and haptic feedback.
4. User taps "Watch ad to unlock".
5. Monetag postback verifies ad completion server-side.
6. User taps "Get Channel Link" to claim target invite link.
