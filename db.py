from __future__ import annotations
import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from config import DATABASE_PATH

DB_FILE = Path(DATABASE_PATH)
JSON_BACKUP_FILE = DB_FILE.parent / "db.json"

if str(DB_FILE.parent) not in ("", "."):
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def hash_password(password: str) -> str:
    return hashlib.sha256(f"salt_ptc_2025_{password}".encode("utf-8")).hexdigest()


@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
        _sync_json_backup(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sync_json_backup(conn: sqlite3.Connection) -> None:
    """Sync key table data to JSON for transparent backup and verification."""
    try:
        data = {
            "updated_at": utcnow(),
            "users": [dict(r) for r in conn.execute("SELECT id, email, username, balance_coins, total_earned_coins, total_ads_watched, is_premium, is_banned, created_at FROM users").fetchall()],
            "withdrawals": [dict(r) for r in conn.execute("SELECT * FROM withdrawals ORDER BY id DESC LIMIT 100").fetchall()],
            "promotions": [dict(r) for r in conn.execute("SELECT * FROM promotions ORDER BY id DESC LIMIT 100").fetchall()],
            "connected_channels": [dict(r) for r in conn.execute("SELECT * FROM connected_channels ORDER BY id DESC LIMIT 100").fetchall()],
            "wallets": [dict(r) for r in conn.execute("SELECT * FROM wallets ORDER BY telegram_id DESC LIMIT 100").fetchall()],
            "wallet_transactions": [dict(r) for r in conn.execute("SELECT * FROM wallet_transactions ORDER BY id DESC LIMIT 100").fetchall()],
            "settings": {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()},
            "ad_codes": {r["key"]: r["value"] for r in conn.execute("SELECT * FROM ad_codes").fetchall()}
        }
        with open(JSON_BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallets (
                telegram_id INTEGER PRIMARY KEY,
                balance_paise INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                utr TEXT,
                order_id TEXT UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                first_name TEXT,
                last_name TEXT,
                photo_url TEXT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                balance_coins REAL DEFAULT 0.0,
                total_earned_coins REAL DEFAULT 0.0,
                total_ads_watched INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                referred_by INTEGER,
                referral_code TEXT UNIQUE NOT NULL,
                is_banned INTEGER DEFAULT 0,
                ip_address TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(referred_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ad_views (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                ip_address TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                reward_coins REAL DEFAULT 2.0,
                timer_seconds INTEGER DEFAULT 20,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS daily_limits (
                user_id INTEGER NOT NULL,
                date_str TEXT NOT NULL,
                ads_watched INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date_str),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ip_daily_limits (
                ip_address TEXT NOT NULL,
                date_str TEXT NOT NULL,
                ads_watched INTEGER DEFAULT 0,
                PRIMARY KEY (ip_address, date_str)
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_usd REAL NOT NULL,
                amount_inr REAL NOT NULL,
                coins_deducted REAL NOT NULL,
                method TEXT NOT NULL,
                account_details TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_url TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_title TEXT NOT NULL,
                price_inr REAL NOT NULL,
                target_joins INTEGER DEFAULT 0,
                current_joins INTEGER DEFAULT 0,
                current_views INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending_approval',
                transaction_ref TEXT NOT NULL,
                category TEXT DEFAULT 'General',
                is_category_specific INTEGER DEFAULT 0,
                total_price_inr REAL DEFAULT 0.0,
                ad_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS connected_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                channel_id TEXT UNIQUE NOT NULL,
                channel_title TEXT NOT NULL,
                channel_username TEXT,
                category TEXT DEFAULT 'General',
                total_joins INTEGER DEFAULT 0,
                total_lefts INTEGER DEFAULT 0,
                total_views INTEGER DEFAULT 0,
                total_earned_inr REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS promo_rotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promotion_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                posted_at TEXT NOT NULL,
                delete_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                FOREIGN KEY(promotion_id) REFERENCES promotions(id)
            );

            CREATE TABLE IF NOT EXISTS join_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                token TEXT PRIMARY KEY,
                target_chat_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                invite_mode TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rewarded_intents (
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, token)
            );

            CREATE TABLE IF NOT EXISTS unlocks (
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL,
                invite_link TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, token)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ad_codes (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN telegram_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN photo_url TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE connected_channels ADD COLUMN category TEXT DEFAULT 'General'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN category TEXT DEFAULT 'General'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN is_category_specific INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN total_price_inr REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN ad_message TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        # Default settings if not set
        default_settings = {
            "coins_per_ad": "2.0",
            "daily_ad_limit": "50",
            "min_withdrawal_usd": "5.0",
            "min_withdrawal_inr": "400.0",
            "coins_per_usd": "100.0",
            "referral_commission_pct": "15.0",
            "payment_upi_id": "blazeculture@upi",
            "payment_qr_url": "/static/logo.png",
            "cost_per_2_joins_inr": "1.0",
            "payout_per_2_joins_inr": "0.50",
        }
        for k, v in default_settings.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Default ad codes placeholders
        default_ad_codes = {
            "interstitial": "<!-- Adsterra / HilltopAds / Monetag Interstitial Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Interstitial Script here\n  console.log('Adsterra / HilltopAds Interstitial Loaded');\n</script>",
            "popunder": "<!-- Adsterra / HilltopAds / Monetag Popunder Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Popunder Script here\n  console.log('Adsterra / HilltopAds Popunder Loaded');\n</script>",
            "banner": "<div style=\"background: rgba(0,255,136,0.1); border: 1px dashed #00ff88; color: #00ff88; padding: 15px; text-align: center; border-radius: 10px;\"><strong>Blazeculture High-Paying Banner Ad Slot</strong><br/>Paste your Adsterra / HilltopAds Banner Code here</div>",
            "direct_link": "https://www.google.com"
        }
        for k, v in default_ad_codes.items():
            conn.execute("INSERT OR IGNORE INTO ad_codes (key, value) VALUES (?, ?)", (k, v))


# Settings & Ad Codes Functions
def get_settings() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_setting(key: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with db() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )


def update_settings(new_settings: dict) -> None:
    with db() as conn:
        for k, v in new_settings.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )


def get_ad_codes() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM ad_codes").fetchall()
        return {r["key"]: r["value"] for r in rows}


def update_ad_codes(codes: dict) -> None:
    with db() as conn:
        for k, v in codes.items():
            conn.execute(
                "INSERT INTO ad_codes(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )


# User Functions
def create_user(email: str, username: str, password: str, referral_code_used: str | None = None, ip_address: str = "") -> dict:
    email_clean = email.strip().lower()
    username_clean = username.strip()
    pass_hash = hash_password(password)
    my_ref_code = uuid.uuid4().hex[:8].upper()

    referred_by_id = None
    if referral_code_used:
        ref_user = get_user_by_referral_code(referral_code_used.strip().upper())
        if ref_user:
            referred_by_id = ref_user["id"]

    user_id = None
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (email, username, password_hash, referral_code, referred_by, ip_address, is_premium, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (email_clean, username_clean, pass_hash, my_ref_code, referred_by_id, ip_address, utcnow()),
        )
        user_id = cursor.lastrowid

    res = get_user_by_id(user_id)
    assert res is not None
    return res


def get_user_by_email(email: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE LOWER(email)=LOWER(?)", (email.strip(),)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username.strip(),)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row) if row else None


def get_or_create_telegram_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    photo_url: str | None = None,
    referral_code_used: str | None = None,
    ip_address: str = ""
) -> dict:
    existing = get_user_by_telegram_id(telegram_id)
    if existing:
        with db() as conn:
            conn.execute(
                """
                UPDATE users
                SET username = COALESCE(NULLIF(?, ''), username),
                    first_name = COALESCE(NULLIF(?, ''), first_name),
                    last_name = COALESCE(NULLIF(?, ''), last_name),
                    photo_url = COALESCE(NULLIF(?, ''), photo_url)
                WHERE id = ?
                """,
                (username or "", first_name or "", last_name or "", photo_url or "", existing["id"]),
            )
        res = get_user_by_id(existing["id"])
        assert res is not None
        return res

    uname = username.strip() if username else f"user_{telegram_id}"
    email = f"tg_{telegram_id}@telegram.org"
    pass_hash = hash_password(f"tg_secret_{telegram_id}")
    my_ref_code = uuid.uuid4().hex[:8].upper()

    referred_by_id = None
    if referral_code_used:
        ref_user = get_user_by_referral_code(referral_code_used.strip().upper())
        if ref_user and ref_user["id"] != telegram_id:
            referred_by_id = ref_user["id"]

    user_id = None
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (telegram_id, email, username, first_name, last_name, photo_url, password_hash, referral_code, referred_by, ip_address, is_premium, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (telegram_id, email, uname, first_name or "", last_name or "", photo_url or "", pass_hash, my_ref_code, referred_by_id, ip_address, utcnow()),
        )
        user_id = cursor.lastrowid

    res = get_user_by_id(user_id)
    assert res is not None
    return res


def get_user_by_referral_code(code: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE UPPER(referral_code)=UPPER(?)", (code.strip(),)).fetchone()
        return dict(row) if row else None


def get_all_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT id, email, username, balance_coins, total_earned_coins, total_ads_watched, is_premium, is_banned, ip_address, created_at FROM users ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def set_user_ban_status(user_id: int, is_banned: bool) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if is_banned else 0, user_id))


def set_user_premium_status(user_id: int, is_premium: bool) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET is_premium=? WHERE id=?", (1 if is_premium else 0, user_id))


def adjust_user_balance(user_id: int, new_balance: float) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET balance_coins=? WHERE id=?", (float(new_balance), user_id))


# Campaign & Unlock Deep Link Functions
def create_campaign(token: str, target_chat_id: str, target_title: str, invite_mode: str, created_by: int) -> dict:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO campaigns (token, target_chat_id, target_title, invite_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token, str(target_chat_id), target_title, invite_mode, created_by, utcnow()),
        )
        return get_campaign(token)  # type: ignore


def get_campaign(token: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None


def record_rewarded_intent(user_id: int, token: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rewarded_intents (user_id, token, created_at) VALUES (?, ?, ?)",
            (user_id, token, utcnow()),
        )


def has_rewarded_intent(user_id: int, token: str) -> bool:
    with db() as conn:
        # Check if user is premium
        u = conn.execute("SELECT is_premium FROM users WHERE id=?", (user_id,)).fetchone()
        if u and u["is_premium"]:
            return True
        row = conn.execute("SELECT * FROM rewarded_intents WHERE user_id=? AND token=?", (user_id, token)).fetchone()
        return bool(row)


def save_unlock(user_id: int, token: str, invite_link: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO unlocks (user_id, token, invite_link, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token, invite_link, utcnow()),
        )


def get_unlock(user_id: int, token: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM unlocks WHERE user_id=? AND token=?", (user_id, token)).fetchone()
        return dict(row) if row else None


# Promotion & Analytics Functions
def create_promotion(
    user_id: int,
    channel_url: str,
    channel_id: str,
    channel_title: str,
    price_inr: float,
    target_joins: int,
    transaction_ref: str,
    category: str = "General",
    is_category_specific: bool = False,
    total_price_inr: float = 0.0,
    ad_message: str = "",
    status: str = "active"
) -> dict:
    tot_price = total_price_inr if total_price_inr > 0 else price_inr
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO promotions (
                user_id, channel_url, channel_id, channel_title, price_inr, target_joins, current_joins, current_views, status, transaction_ref, category, is_category_specific, total_price_inr, ad_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, channel_url, str(channel_id), channel_title, price_inr, target_joins,
                status, transaction_ref, category, 1 if is_category_specific else 0, tot_price, ad_message, utcnow()
            ),
        )
        promo_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM promotions WHERE id=?", (promo_id,)).fetchone()
        return dict(row)


def get_user_promotions(user_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM promotions WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_all_promotions() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT p.*, u.username, u.email
            FROM promotions p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def update_promotion_status(promo_id: int, status: str) -> dict:
    with db() as conn:
        p = conn.execute("SELECT * FROM promotions WHERE id=?", (promo_id,)).fetchone()
        if not p:
            return {"ok": False, "error": "Promotion campaign not found"}
        conn.execute("UPDATE promotions SET status=? WHERE id=?", (status, promo_id))
        return {"ok": True}


def connect_channel(owner_user_id: int, channel_id: str, channel_title: str, channel_username: str = "", category: str = "General") -> dict:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO connected_channels (owner_user_id, channel_id, channel_title, channel_username, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                category=excluded.category
            """,
            (owner_user_id, str(channel_id), channel_title, channel_username, category or "General", utcnow()),
        )
        row = conn.execute("SELECT * FROM connected_channels WHERE channel_id=?", (str(channel_id),)).fetchone()
        return dict(row)


def get_user_connected_channels(owner_user_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM connected_channels WHERE owner_user_id=? ORDER BY id DESC", (owner_user_id,)).fetchall()
        return [dict(r) for r in rows]


def delete_connected_channel(owner_user_id: int, channel_id: str) -> dict:
    with db() as conn:
        clean_id = str(channel_id).strip()
        username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
        at_username = f"@{username_part}" if username_part else clean_id

        row = conn.execute(
            """
            SELECT * FROM connected_channels
            WHERE owner_user_id=? AND (channel_id=? OR channel_id=? OR channel_username=? OR id=?)
            """,
            (owner_user_id, clean_id, at_username, username_part, clean_id),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Channel not found or you do not have permission to delete it."}
        conn.execute("DELETE FROM connected_channels WHERE id=?", (row["id"],))
        return {"ok": True, "message": f"Channel '{row['channel_title']}' removed successfully."}


def admin_delete_connected_channel(channel_id: str) -> dict:
    with db() as conn:
        clean_id = str(channel_id).strip()
        username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
        at_username = f"@{username_part}" if username_part else clean_id

        row = conn.execute(
            """
            SELECT * FROM connected_channels
            WHERE channel_id=? OR channel_id=? OR channel_username=? OR id=?
            """,
            (clean_id, at_username, username_part, clean_id),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Channel not found."}
        conn.execute("DELETE FROM connected_channels WHERE id=?", (row["id"],))
        return {"ok": True, "message": f"Channel '{row['channel_title']}' removed by admin."}


def get_all_connected_channels() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username as owner_username, u.email as owner_email
            FROM connected_channels c
            JOIN users u ON c.owner_user_id = u.id
            ORDER BY c.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def record_channel_join_event(channel_id: str, user_id: int) -> dict:
    settings = get_settings()
    payout_per_2_joins = float(settings.get("payout_per_2_joins_inr", "0.50"))
    payout_per_join = payout_per_2_joins / 2.0  # 0.25 INR per user join

    clean_id = str(channel_id)
    username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
    at_username = f"@{username_part}" if username_part else clean_id

    with db() as conn:
        conn.execute(
            "INSERT INTO join_analytics (channel_id, user_id, status, created_at) VALUES (?, ?, 'joined', ?)",
            (clean_id, user_id, utcnow()),
        )

        # Update connected channel stats
        ch = conn.execute(
            "SELECT * FROM connected_channels WHERE channel_id=? OR channel_id=? OR channel_username=?",
            (clean_id, at_username, username_part),
        ).fetchone()
        if ch:
            earned_bonus = payout_per_join
            conn.execute(
                """
                UPDATE connected_channels
                SET total_joins = total_joins + 1,
                    total_earned_inr = total_earned_inr + ?
                WHERE channel_id = ?
                """,
                (earned_bonus, str(channel_id)),
            )

            # Credit channel owner coins balance (using 1 USD = 80 INR, coins per USD = 100)
            coins_per_usd = float(settings.get("coins_per_usd", "100.0"))
            coins_earned = (earned_bonus / 80.0) * coins_per_usd
            conn.execute(
                """
                UPDATE users
                SET balance_coins = balance_coins + ?,
                    total_earned_coins = total_earned_coins + ?
                WHERE id = ?
                """,
                (coins_earned, coins_earned, ch["owner_user_id"]),
            )

        # Update active promotions for this target channel_id
        conn.execute(
            "UPDATE promotions SET current_joins = current_joins + 1 WHERE channel_id=? AND status='active'",
            (str(channel_id),),
        )

        return {"ok": True}


def record_channel_left_event(channel_id: str, user_id: int) -> dict:
    clean_id = str(channel_id)
    username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
    at_username = f"@{username_part}" if username_part else clean_id

    with db() as conn:
        conn.execute(
            "INSERT INTO join_analytics (channel_id, user_id, status, created_at) VALUES (?, ?, 'left', ?)",
            (clean_id, user_id, utcnow()),
        )

        conn.execute(
            "UPDATE connected_channels SET total_lefts = total_lefts + 1 WHERE channel_id=? OR channel_id=? OR channel_username=?",
            (clean_id, at_username, username_part),
        )

        return {"ok": True}


def record_channel_view_event(channel_id: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE connected_channels SET total_views = total_views + 1 WHERE channel_id=?",
            (str(channel_id),),
        )
        conn.execute(
            "UPDATE promotions SET current_views = current_views + 1 WHERE channel_id=? AND status='active'",
            (str(channel_id),),
        )


# Ad View & Anti-Cheat Functions
def get_user_daily_ad_count(user_id: int, date_str: str | None = None) -> int:
    d_str = date_str or today_str()
    with db() as conn:
        row = conn.execute("SELECT ads_watched FROM daily_limits WHERE user_id=? AND date_str=?", (user_id, d_str)).fetchone()
        return row["ads_watched"] if row else 0


def get_ip_daily_ad_count(ip_address: str, date_str: str | None = None) -> int:
    d_str = date_str or today_str()
    with db() as conn:
        row = conn.execute("SELECT ads_watched FROM ip_daily_limits WHERE ip_address=? AND date_str=?", (ip_address, d_str)).fetchone()
        return row["ads_watched"] if row else 0


def start_ad_session(user_id: int, ip_address: str, timer_seconds: int = 20, reward_coins: float = 2.0) -> dict:
    session_id = uuid.uuid4().hex
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ad_views (id, user_id, ip_address, status, started_at, timer_seconds, reward_coins)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (session_id, user_id, ip_address, utcnow(), timer_seconds, reward_coins),
        )
        return {"session_id": session_id, "timer_seconds": timer_seconds, "reward_coins": reward_coins}


def claim_ad_reward(session_id: str, user_id: int) -> dict:
    settings = get_settings()
    daily_limit = int(settings.get("daily_ad_limit", "50"))
    ref_commission_pct = float(settings.get("referral_commission_pct", "15.0"))

    with db() as conn:
        session = conn.execute("SELECT * FROM ad_views WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not session:
            return {"ok": False, "error": "Invalid ad session"}

        if session["status"] != "pending":
            return {"ok": False, "error": "Ad reward already claimed or expired"}

        # Daily limit check
        d_str = today_str()
        current_daily = get_user_daily_ad_count(user_id, d_str)
        if current_daily >= daily_limit:
            return {"ok": False, "error": f"Daily ad limit of {daily_limit} ads reached for today."}

        # Mark ad session as completed
        reward = float(session["reward_coins"])
        conn.execute(
            "UPDATE ad_views SET status='completed', completed_at=? WHERE id=?",
            (utcnow(), session_id),
        )

        # Update user balance and ads watched
        conn.execute(
            """
            UPDATE users
            SET balance_coins = balance_coins + ?,
                total_earned_coins = total_earned_coins + ?,
                total_ads_watched = total_ads_watched + 1
            WHERE id = ?
            """,
            (reward, reward, user_id),
        )

        # Update daily limits for user
        conn.execute(
            """
            INSERT INTO daily_limits (user_id, date_str, ads_watched) VALUES (?, ?, 1)
            ON CONFLICT(user_id, date_str) DO UPDATE SET ads_watched = ads_watched + 1
            """,
            (user_id, d_str),
        )

        # Update daily limits for IP address
        if session["ip_address"]:
            conn.execute(
                """
                INSERT INTO ip_daily_limits (ip_address, date_str, ads_watched) VALUES (?, ?, 1)
                ON CONFLICT(ip_address, date_str) DO UPDATE SET ads_watched = ads_watched + 1
                """,
                (session["ip_address"], d_str),
            )

        # Referral bonus logic
        user = conn.execute("SELECT referred_by FROM users WHERE id=?", (user_id,)).fetchone()
        if user and user["referred_by"]:
            referrer_id = user["referred_by"]
            ref_bonus = reward * (ref_commission_pct / 100.0)
            conn.execute(
                """
                UPDATE users
                SET balance_coins = balance_coins + ?,
                    total_earned_coins = total_earned_coins + ?
                WHERE id = ?
                """,
                (ref_bonus, ref_bonus, referrer_id),
            )

        updated_user = conn.execute("SELECT balance_coins, total_earned_coins, total_ads_watched FROM users WHERE id=?", (user_id,)).fetchone()

        return {
            "ok": True,
            "reward_coins": reward,
            "new_balance": updated_user["balance_coins"],
            "total_ads_watched": updated_user["total_ads_watched"],
            "daily_ads_today": current_daily + 1,
            "daily_limit": daily_limit,
        }


# Withdrawals Functions
def create_withdrawal(user_id: int, amount_usd: float, amount_inr: float, coins: float, method: str, account_details: str) -> dict:
    with db() as conn:
        user = conn.execute("SELECT balance_coins FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["balance_coins"] < coins:
            return {"ok": False, "error": "Insufficient coin balance"}

        # Deduct user balance
        conn.execute("UPDATE users SET balance_coins = balance_coins - ? WHERE id=?", (coins, user_id))

        # Insert withdrawal record
        cursor = conn.execute(
            """
            INSERT INTO withdrawals (user_id, amount_usd, amount_inr, coins_deducted, method, account_details, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, amount_usd, amount_inr, coins, method, account_details, utcnow()),
        )
        return {"ok": True, "withdrawal_id": cursor.lastrowid}


def get_user_withdrawals(user_id: int) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]


def get_all_withdrawals() -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT w.*, u.username, u.email
            FROM withdrawals w
            JOIN users u ON w.user_id = u.id
            ORDER BY w.id DESC
            """
        ).fetchall()]


def update_withdrawal_status(withdrawal_id: int, status: str) -> dict:
    with db() as conn:
        w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
        if not w:
            return {"ok": False, "error": "Withdrawal request not found"}

        if w["status"] != "pending" and status == "rejected":
            return {"ok": False, "error": "Request already processed"}

        if status == "rejected" and w["status"] == "pending":
            # Refund coins if rejected
            conn.execute("UPDATE users SET balance_coins = balance_coins + ? WHERE id=?", (w["coins_deducted"], w["user_id"]))

        conn.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, withdrawal_id))
        return {"ok": True}


# Stats & Leaderboard
def get_top_earners(limit: int = 10) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT username, total_earned_coins, total_ads_watched
            FROM users
            WHERE is_banned = 0
            ORDER BY total_earned_coins DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_global_stats() -> dict:
    with db() as conn:
        total_users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
        total_ads = conn.execute("SELECT COUNT(*) as cnt FROM ad_views WHERE status='completed'").fetchone()["cnt"]
        total_paid_row = conn.execute("SELECT SUM(amount_usd) as total FROM withdrawals WHERE status='approved'").fetchone()
        total_paid_usd = total_paid_row["total"] if total_paid_row and total_paid_row["total"] else 0.0

        total_joins_row = conn.execute("SELECT COUNT(*) as cnt FROM join_analytics WHERE status='joined'").fetchone()
        total_joins = total_joins_row["cnt"] if total_joins_row else 0

        total_lefts_row = conn.execute("SELECT COUNT(*) as cnt FROM join_analytics WHERE status='left'").fetchone()
        total_lefts = total_lefts_row["cnt"] if total_lefts_row else 0

        active_promos_row = conn.execute("SELECT COUNT(*) as cnt FROM promotions WHERE status='active'").fetchone()
        active_promos = active_promos_row["cnt"] if active_promos_row else 0

        return {
            "total_users": total_users,
            "total_ads_watched": total_ads,
            "total_paid_usd": round(float(total_paid_usd), 2),
            "total_paid_inr": round(float(total_paid_usd) * 80.0, 2),
            "total_joins": total_joins,
            "total_lefts": total_lefts,
            "active_promotions": active_promos,
        }


def get_referral_stats(user_id: int) -> dict:
    with db() as conn:
        ref_count = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by=?", (user_id,)).fetchone()["cnt"]
        ref_users = conn.execute(
            "SELECT username, total_earned_coins, created_at FROM users WHERE referred_by=? ORDER BY id DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        return {
            "total_referrals": ref_count,
            "referrals": [dict(r) for r in ref_users],
        }


# Wallet Functions
def get_or_create_wallet(telegram_id: int) -> dict:
    now = utcnow()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO wallets (telegram_id, balance_paise, created_at, updated_at)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(telegram_id) DO NOTHING
            """,
            (telegram_id, now, now),
        )
        row = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row)


def get_wallet_balance(telegram_id: int) -> int:
    w = get_or_create_wallet(telegram_id)
    return w["balance_paise"]


def credit_wallet_deposit(telegram_id: int, amount_paise: int, utr: str | None = None, order_id: str | None = None, description: str | None = None) -> dict:
    """
    Credits user wallet and logs transaction.
    Returns dict with 'already_processed': True if order_id was already processed, or 'ok': True on success.
    """
    get_or_create_wallet(telegram_id)
    now = utcnow()
    with db() as conn:
        if order_id:
            existing = conn.execute("SELECT * FROM wallet_transactions WHERE order_id=?", (order_id,)).fetchone()
            if existing:
                row_w = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (telegram_id,)).fetchone()
                return {"ok": True, "already_processed": True, "balance_paise": row_w["balance_paise"]}

        conn.execute(
            """
            INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
            VALUES (?, 'deposit', ?, ?, ?, ?, ?)
            """,
            (telegram_id, amount_paise, utr, order_id, description or "YOUPI PG Deposit", now),
        )
        conn.execute(
            """
            UPDATE wallets
            SET balance_paise = balance_paise + ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (amount_paise, now, telegram_id),
        )
        row_w = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (telegram_id,)).fetchone()
        return {"ok": True, "already_processed": False, "balance_paise": row_w["balance_paise"]}


def get_wallet_transactions(telegram_id: int, limit: int = 50) -> list[dict]:
    get_or_create_wallet(telegram_id)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_transactions WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def admin_credit_user_wallet(user_id: int, amount_inr: float, description: str = "Admin Funds Credit") -> dict:
    user = get_user_by_id(user_id)
    if not user:
        return {"ok": False, "error": f"User ID {user_id} not found."}

    tg_id = user.get("telegram_id") or user["id"]
    amount_paise = int(round(amount_inr * 100))
    if amount_paise <= 0:
        return {"ok": False, "error": "Amount must be greater than zero."}

    get_or_create_wallet(tg_id)
    now = utcnow()
    order_id = f"ADMIN-{tg_id}-{uuid.uuid4().hex[:8].upper()}"

    with db() as conn:
        conn.execute(
            """
            INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
            VALUES (?, 'admin_credit', ?, 'ADMIN_GRANT', ?, ?, ?)
            """,
            (tg_id, amount_paise, order_id, description or "Admin Grant Credit", now),
        )
        conn.execute(
            """
            UPDATE wallets
            SET balance_paise = balance_paise + ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (amount_paise, now, tg_id),
        )
        row_w = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (tg_id,)).fetchone()
        return {
            "ok": True,
            "message": f"Successfully credited ₹{amount_inr:.2f} to user '{user['username']}' account.",
            "new_balance_paise": row_w["balance_paise"],
            "new_balance_inr": row_w["balance_paise"] / 100.0,
        }


def deduct_wallet_balance_paise(telegram_id: int, amount_paise: int, description: str = "Channel Promotion Payment") -> dict:
    """
    Deducts user wallet balance in paise if sufficient balance is available.
    Returns dict with 'ok': True and new balance, or 'ok': False if insufficient balance.
    """
    w = get_or_create_wallet(telegram_id)
    current_bal = w["balance_paise"]
    if current_bal < amount_paise:
        return {
            "ok": False,
            "error": f"Insufficient wallet balance. Required: ₹{amount_paise/100:.2f}, Available: ₹{current_bal/100:.2f}. Please add money to your wallet.",
            "current_balance_paise": current_bal,
            "required_paise": amount_paise,
        }

    now = utcnow()
    order_id = f"PROMO-{telegram_id}-{uuid.uuid4().hex[:8].upper()}"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
            VALUES (?, 'debit', ?, NULL, ?, ?, ?)
            """,
            (telegram_id, amount_paise, order_id, description, now),
        )
        conn.execute(
            """
            UPDATE wallets
            SET balance_paise = balance_paise - ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (amount_paise, now, telegram_id),
        )
        row_w = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (telegram_id,)).fetchone()
        return {"ok": True, "balance_paise": row_w["balance_paise"], "order_id": order_id}


# Promotion Batch Rotation Helpers
def get_connected_channels_by_category(category: str | None = None, limit: int = 50) -> list[dict]:
    with db() as conn:
        if category and category.strip().lower() != "general":
            rows = conn.execute(
                "SELECT * FROM connected_channels WHERE LOWER(category)=LOWER(?) ORDER BY id ASC LIMIT ?",
                (category.strip(), limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]

        rows = conn.execute("SELECT * FROM connected_channels ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_active_promotions_for_rotation() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM promotions WHERE status='active' AND current_joins < target_joins ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_rotated_channel_ids_for_promotion(promotion_id: int) -> set[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT channel_id FROM promo_rotations WHERE promotion_id=?", (promotion_id,)
        ).fetchall()
        return {r["channel_id"] for r in rows}


def record_promo_rotation(promotion_id: int, channel_id: str, message_id: int, delete_at: str) -> dict:
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO promo_rotations (promotion_id, channel_id, message_id, posted_at, delete_at, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (promotion_id, str(channel_id), message_id, utcnow(), delete_at),
        )
        row = conn.execute("SELECT * FROM promo_rotations WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_active_promo_rotations_due_for_deletion(now_iso: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT r.*, p.current_joins, p.target_joins, p.status as promo_status
            FROM promo_rotations r
            JOIN promotions p ON r.promotion_id = p.id
            WHERE r.status='active' AND r.delete_at <= ?
            """,
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_promo_rotation_deleted(rotation_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE promo_rotations SET status='deleted' WHERE id=?", (rotation_id,))
