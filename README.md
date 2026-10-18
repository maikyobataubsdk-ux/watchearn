# Telegram Channel Gate Mini App (Monetag rewarded-ad starter)

This project connects a Telegram bot, a Telegram Mini App, force-subscription checks, and a target channel invite link. The target channel can be configured to deliver either a direct one-person invite link or a join-request link.

**Important monetization limitation:** this project intentionally uses only Monetag's *Rewarded Interstitial* flow for the unlock step. Do not require users to view/click ads from every network in your earlier list. Adsterra and HilltopAds explicitly prohibit incentivized traffic; using them in a forced-ad-for-access loop can lead to rejected inventory, withheld earnings, or account suspension. Add other networks only in placements and formats their current publisher terms explicitly permit. Get written approval from Monetag for the exact “watch rewarded ad to receive a Telegram channel invite” reward before running it with real traffic.

The bot gives access only when the backend receives a Monetag server-side postback with `reward_event_type=valued` for an `impression`. The browser alone cannot grant the reward. It uses SQLite for idempotent ad-event handling and saved campaign/unlock records.

## Project files

- `bot.py` — Telegram bot commands, force-subscription check, campaign links, and direct/request invite generation.
- `web.py` — FastAPI endpoints, Telegram `initData` signature validation, rewarded-ad intent/status APIs, and Monetag postback endpoint.
- `static/index.html` — responsive Telegram Mini App UI.
- `db.py` — SQLite database and one-time reward handling.
- `telegram_auth.py` — HMAC validation for Telegram Mini App `initData`.
- `.env.example` — environment configuration template.

## 1. Create the Telegram bot

1. Create a bot with `@BotFather` and copy its token.
2. Obtain your numeric Telegram user ID and set it as `ADMIN_USER_ID`.
3. Add the bot as an administrator to both the force-subscription channel (if used) and the target channel. It must be able to manage/create invite links. For membership checks, Telegram requires the bot to be an administrator in the force-subscription chat.
4. Host the Mini App on a domain with valid HTTPS. Telegram Web Apps require HTTPS for the web URL.

## 2. Configure the project

On a Debian/Ubuntu VPS with Python 3.11 or newer:

```bash
mkdir -p /opt/channel-gate
cd /opt/channel-gate
# Upload and extract this ZIP into this directory
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
WEBAPP_URL=https://miniapp.example.com
MONETAG_SDK_URL=PASTE_THE_EXACT_SDK_SCRIPT_URL_FROM_MONETAG_DASHBOARD
MONETAG_ZONE_ID=PASTE_MAIN_ZONE_ID
MONETAG_POSTBACK_SECRET=USE_A_LONG_RANDOM_ALPHANUMERIC_SECRET
DATABASE_PATH=/opt/channel-gate/data.sqlite3
HOST=127.0.0.1
PORT=8000
```

Use the exact SDK script URL and **main zone ID** supplied by your Monetag account; do not guess a script hostname or use a sub-zone ID. Leave those settings blank until the Mini App and zone are approved and configured. Never share `.env` or your bot token.

## 3. Run the web app and bot

Run these in two separate terminal sessions for the first test:

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

The public HTTPS domain should reverse-proxy to `127.0.0.1:8000`. Test `https://miniapp.example.com/health` and expect `{"ok":true}`. Keep the bot and web process running permanently with your process manager/systemd after confirming they work.

## 4. Nginx reverse-proxy example

Replace `miniapp.example.com` with your domain. First configure DNS to point to the VPS and install an HTTPS certificate. Example Nginx location inside your HTTPS server block:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

No port 8000 needs to be publicly exposed.

## 5. Configure Monetag postback

After adding the Telegram Mini App and enabling Rewarded Interstitial/postbacks in Monetag, set the postback URL for the **main SDK zone** to the following URL, replacing the domain and secret. Paste it as one line into your Monetag dashboard:

```text
https://miniapp.example.com/monetag/postback?secret=YOUR_LONG_RANDOM_SECRET&ymid={ymid}&event_type={event_type}&reward_event_type={reward_event_type}&estimated_price={estimated_price}&telegram_id={telegram_id}&zone_id={zone_id}&sub_zone_id={sub_zone_id}&request_var={request_var}
```

The `secret` value must exactly match `MONETAG_POSTBACK_SECRET` in `.env`. Use an alphanumeric secret to avoid URL-encoding issues. Keep postbacks enabled. A valid postback for an `impression` and `valued` event marks the specific ad intent rewarded. Duplicate provider retries are handled idempotently.

The frontend callback is only used for UI feedback; the server-side postback remains authoritative. A missing/non-valued event does not unlock the channel. Monetag notes that frontend callbacks and postbacks can arrive independently, so the UI briefly polls for the server confirmation.

## 6. Register the Mini App URL with Telegram

Use `@BotFather` to configure a Mini App for the bot and set the public HTTPS URL to `https://miniapp.example.com/`. The bot's Watch Ad button also uses this URL.

## 7. Bot admin commands

Send these commands to your bot as the configured `ADMIN_USER_ID`:

- `/setforce @RequiredChannel` — set the required channel users must join before seeing the ad option. The bot must be an admin there. For a private channel, use its `-100...` chat ID; the bot creates a join URL.
- `/offforce` — turn force-subscription off.
- `/settarget @TargetChannel direct` — set the target channel and make per-user direct invite links. Each generated invite is limited to one successful join.
- `/settarget @TargetChannel request` — generate links that create join requests. Telegram channel admins still need to approve those requests; the bot does not bypass that approval workflow.
- `/genlink` — generate a campaign deep link like `https://t.me/YourBot?start=TOKEN`. Share this link, not the private target channel invite.
- `/status` — show current configuration.

You can change the target channel/mode and run `/genlink` again to make campaigns for different channels. Existing campaigns retain their original destination and invite mode.

## 8. How a user's flow works

1. The user opens a campaign deep link.
2. If force-subscription is enabled, the bot asks the user to join and then rechecks membership.
3. The user launches the Mini App and taps “Watch ad to unlock”.
4. The server makes a unique `ymid` record and the app calls the Monetag SDK.
5. Access unlocks only after the server validates a `valued` impression postback.
6. The user returns to the bot and taps “Get Channel Link”.
7. The bot creates a one-use direct invite link or a join-request link according to the campaign configuration.

## Security and operational notes

- The bot must remain an administrator in configured channels. If `getChatMember` cannot verify force-sub membership, access is denied.
- Do not trust a client-side “ad completed” flag. This project does not award access from that flag.
- Never expose the bot token or postback secret in HTML/JS, public repositories, logs, or screenshots.
- Keep Python packages patched, use HTTPS, back up `data.sqlite3`, and monitor the logs.
- `request` mode creates a join-request invite; it intentionally leaves the final approval to channel admins.
- Ad availability and monetized event rate vary. A user may not receive access if the provider returns a non-valued event or no ad is available.
- The example is a functional starter, not a guarantee of publisher approval. Verify current Monetag terms and Telegram requirements before production use.
