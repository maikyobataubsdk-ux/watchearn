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
                rejection_reason TEXT DEFAULT '',
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
                status TEXT DEFAULT 'pending_approval',
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

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                link_url TEXT NOT NULL,
                reward_inr REAL NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                reward_inr REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, task_id),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS custom_user_rates (
                user_id INTEGER PRIMARY KEY,
                members_per_inr REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ad_codes (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_invite_links (
                invite_link TEXT PRIMARY KEY,
                promotion_id INTEGER NOT NULL,
                source_channel_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_pct REAL DEFAULT 0.0,
                discount_amount_inr REAL DEFAULT 0.0,
                min_budget_inr REAL DEFAULT 0.0,
                max_uses INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS force_sub_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT UNIQUE NOT NULL,
                channel_title TEXT NOT NULL,
                channel_username TEXT DEFAULT '',
                join_url TEXT NOT NULL,
                invite_type TEXT DEFAULT 'direct',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
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
        try:
            conn.execute("ALTER TABLE connected_channels ADD COLUMN member_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE connected_channels ADD COLUMN status TEXT DEFAULT 'pending_approval'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN rejection_reason TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN image_url TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE promotions ADD COLUMN link_type TEXT DEFAULT 'direct'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN has_accepted_agreement INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # Default settings if not set
        default_settings = {
            "coins_per_ad": "0.80",
            "daily_ad_limit": "50",
            "min_withdrawal_usd": "5.0",
            "min_withdrawal_inr": "400.0",
            "coins_per_usd": "100.0",
            "referral_commission_pct": "15.0",
            "payment_upi_id": "earnprogram@upi",
            "payment_qr_url": "/static/payment_qr.jpg",
            "cost_per_2_joins_inr": "1.0",
            "payout_per_2_joins_inr": "0.80",
            "bot_start_quote": "welcome to earn program! watch simple ads, earn coins, promote telegram channels & generate daily income easily.",
            "bot_tutorial_url": "https://t.me/earnprogramsupport",
            "bot_help_url": "https://t.me/earnprogramsupport",
            "bot_support_url": "https://t.me/earnprogramsupport",
            "bot_update_url": "https://t.me/earnprogramsupport",
            "force_sub_enabled": "0",
            "global_discount_pct": "0.0",
            "maintenance_full": "0",
            "maintenance_watch": "0",
            "maintenance_promote": "0",
            "maintenance_earnings": "0",
            "maintenance_withdraw": "0",
        }
        for k, v in default_settings.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Default ad codes placeholders
        default_ad_codes = {
            "interstitial": "<!-- Adsterra / HilltopAds / Monetag Interstitial Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Interstitial Script here\n  console.log('Adsterra / HilltopAds Interstitial Loaded');\n</script>",
            "popunder": "<!-- Adsterra / HilltopAds / Monetag Popunder Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Popunder Script here\n  console.log('Adsterra / HilltopAds Popunder Loaded');\n</script>",
            "banner": "<div style=\"background: rgba(0,255,136,0.1); border: 1px dashed #00ff88; color: #00ff88; padding: 15px; text-align: center; border-radius: 10px;\"><strong>Earn Program High-Paying Banner Ad Slot</strong><br/>Paste your Adsterra / HilltopAds Banner Code here</div>",
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
    status: str = "pending_approval",
    image_url: str = "",
    link_type: str = "direct"
) -> dict:
    tot_price = total_price_inr if total_price_inr > 0 else price_inr
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO promotions (
                user_id, channel_url, channel_id, channel_title, price_inr, target_joins, current_joins, current_views, status, transaction_ref, category, is_category_specific, total_price_inr, ad_message, image_url, link_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, channel_url, str(channel_id), channel_title, price_inr, target_joins,
                status, transaction_ref, category, 1 if is_category_specific else 0, tot_price, ad_message, image_url, link_type, utcnow()
            ),
        )
        promo_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM promotions WHERE id=?", (promo_id,)).fetchone()
        return dict(row)


# Tasks Functions
def create_task(title: str, description: str, link_url: str, reward_inr: float) -> dict:
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (title, description, link_url, reward_inr, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (title.strip(), description.strip(), link_url.strip(), float(reward_inr), utcnow()),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_task_by_id(task_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_all_tasks() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_user_tasks(user_id: int) -> list[dict]:
    with db() as conn:
        tasks = conn.execute("SELECT * FROM tasks WHERE is_active=1 ORDER BY id DESC").fetchall()
        completed = {r["task_id"] for r in conn.execute("SELECT task_id FROM task_completions WHERE user_id=?", (user_id,)).fetchall()}
        result = []
        for t in tasks:
            td = dict(t)
            td["is_completed"] = t["id"] in completed
            result.append(td)
        return result


def complete_task(user_id: int, task_id: int) -> dict:
    with db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND is_active=1", (task_id,)).fetchone()
        if not task:
            return {"ok": False, "error": "Task not found or inactive."}

        existing = conn.execute("SELECT * FROM task_completions WHERE user_id=? AND task_id=?", (user_id, task_id)).fetchone()
        if existing:
            return {"ok": False, "error": "You have already completed this task."}

        reward = float(task["reward_inr"])
        conn.execute(
            "INSERT INTO task_completions (user_id, task_id, reward_inr, created_at) VALUES (?, ?, ?, ?)",
            (user_id, task_id, reward, utcnow()),
        )

        # Credit reward as coins (where 1 coin = 1 INR in platform coin terms)
        conn.execute(
            """
            UPDATE users
            SET balance_coins = balance_coins + ?,
                total_earned_coins = total_earned_coins + ?
            WHERE id = ?
            """,
            (reward, reward, user_id),
        )

        u = conn.execute("SELECT balance_coins FROM users WHERE id=?", (user_id,)).fetchone()
        return {"ok": True, "reward_inr": reward, "new_balance": u["balance_coins"]}


def delete_task(task_id: int) -> dict:
    with db() as conn:
        conn.execute("DELETE FROM task_completions WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return {"ok": True}


def toggle_task_active(task_id: int) -> dict:
    with db() as conn:
        task = conn.execute("SELECT is_active FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return {"ok": False, "error": "Task not found."}
        new_val = 0 if task["is_active"] else 1
        conn.execute("UPDATE tasks SET is_active=? WHERE id=?", (new_val, task_id))
        return {"ok": True, "is_active": new_val}


# Custom Member Rates & Agreement Functions
def set_custom_user_rate(user_id: int, members_per_inr: float) -> None:
    with db() as conn:
        if members_per_inr <= 0:
            conn.execute("DELETE FROM custom_user_rates WHERE user_id=?", (user_id,))
        else:
            conn.execute(
                """
                INSERT INTO custom_user_rates (user_id, members_per_inr) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET members_per_inr=excluded.members_per_inr
                """,
                (user_id, float(members_per_inr)),
            )


def get_custom_user_rate(user_id: int) -> float | None:
    with db() as conn:
        row = conn.execute("SELECT members_per_inr FROM custom_user_rates WHERE user_id=?", (user_id,)).fetchone()
        return row["members_per_inr"] if row else None


def set_user_agreement_status(user_id: int, accepted: bool = True) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET has_accepted_agreement=? WHERE id=?", (1 if accepted else 0, user_id))


def get_user_promotions(user_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM promotions WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            tot_price = d.get("total_price_inr") if (d.get("total_price_inr") and d.get("total_price_inr") > 0) else (d.get("price_inr") or 0.0)
            target = d.get("target_joins") or 1
            curr_joins = d.get("current_joins") or 0
            if target > 0:
                d["spent_inr"] = round((tot_price * min(curr_joins, target)) / target, 2)
            else:
                d["spent_inr"] = 0.0
            result.append(d)
        return result


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
        result = []
        for r in rows:
            d = dict(r)
            tot_price = d.get("total_price_inr") if (d.get("total_price_inr") and d.get("total_price_inr") > 0) else (d.get("price_inr") or 0.0)
            target = d.get("target_joins") or 1
            curr_joins = d.get("current_joins") or 0
            if target > 0:
                d["spent_inr"] = round((tot_price * min(curr_joins, target)) / target, 2)
            else:
                d["spent_inr"] = 0.0
            result.append(d)
        return result


def update_promotion_status(promo_id: int, status: str, rejection_reason: str = "") -> dict:
    clean_status = str(status).strip().lower()
    if clean_status in ("stop", "pause", "paused"):
        clean_status = "stopped"
    elif clean_status in ("resume", "approve"):
        clean_status = "active"

    with db() as conn:
        p = conn.execute("SELECT * FROM promotions WHERE id=?", (promo_id,)).fetchone()
        if not p:
            return {"ok": False, "error": "Promotion campaign not found"}

        old_status = p["status"]
        conn.execute("UPDATE promotions SET status=?, rejection_reason=? WHERE id=?", (clean_status, rejection_reason, promo_id))

        # If rejected and previously not rejected, refund user's wallet
        if status == "rejected" and old_status != "rejected":
            user_id = p["user_id"]
            user = conn.execute("SELECT telegram_id, id FROM users WHERE id=?", (user_id,)).fetchone()
            if user:
                tg_id = user["telegram_id"] or user["id"]
                tot_price = p["total_price_inr"] if p["total_price_inr"] > 0 else p["price_inr"]
                refund_paise = int(round(tot_price * 100))
                if refund_paise > 0:
                    now = utcnow()
                    order_id = f"REFUND-PROMO-{promo_id}-{uuid.uuid4().hex[:6].upper()}"
                    desc = f"Refund for Rejected Promotion #{promo_id}"
                    if rejection_reason:
                        desc += f": {rejection_reason}"

                    conn.execute(
                        """
                        INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
                        VALUES (?, 'refund', ?, 'PROMO_REFUND', ?, ?, ?)
                        """,
                        (tg_id, refund_paise, order_id, desc, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO wallets (telegram_id, balance_paise, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET balance_paise = balance_paise + excluded.balance_paise, updated_at = excluded.updated_at
                        """,
                        (tg_id, refund_paise, now, now),
                    )

        return {"ok": True}


def connect_channel(owner_user_id: int, channel_id: str, channel_title: str, channel_username: str = "", category: str = "General", member_count: int = 0, status: str = "pending_approval") -> dict:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO connected_channels (owner_user_id, channel_id, channel_title, channel_username, category, member_count, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                category=excluded.category,
                member_count=excluded.member_count,
                status=excluded.status
            """,
            (owner_user_id, str(channel_id), channel_title, channel_username, category or "General", member_count, status, utcnow()),
        )
        row = conn.execute("SELECT * FROM connected_channels WHERE channel_id=?", (str(channel_id),)).fetchone()
        return dict(row)


def update_connected_channel_status(channel_id: str, status: str) -> dict:
    clean_id = str(channel_id).strip()
    username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
    at_username = f"@{username_part}" if username_part else clean_id

    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM connected_channels
            WHERE channel_id=? OR channel_id=? OR channel_username=? OR id=?
            """,
            (clean_id, at_username, username_part, clean_id),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Channel not found."}
        conn.execute("UPDATE connected_channels SET status=? WHERE id=?", (status, row["id"]))
        updated = conn.execute("SELECT * FROM connected_channels WHERE id=?", (row["id"],)).fetchone()
        return {"ok": True, "message": f"Channel '{row['channel_title']}' status set to {status}.", "channel": dict(updated)}


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


def record_promo_invite_link(invite_link: str, promotion_id: int, source_channel_id: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO promo_invite_links (invite_link, promotion_id, source_channel_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(invite_link) DO UPDATE SET promotion_id=excluded.promotion_id, source_channel_id=excluded.source_channel_id
            """,
            (invite_link.strip(), promotion_id, str(source_channel_id).strip(), utcnow()),
        )


def get_promo_invite_link(invite_link: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM promo_invite_links WHERE invite_link=?", (invite_link.strip(),)).fetchone()
        return dict(row) if row else None


def record_channel_join_event_with_source(promotion_id: int, source_channel_id: str, user_id: int) -> dict:
    settings = get_settings()
    payout_per_2_joins = float(settings.get("payout_per_2_joins_inr", "0.80"))
    payout_per_join = payout_per_2_joins / 2.0  # 0.40 INR (40 paise) per user join (80% share)

    clean_id = str(source_channel_id).strip()
    username_part = clean_id.split("t.me/")[-1].lstrip("@").strip("/")
    at_username = f"@{username_part}" if username_part else clean_id

    with db() as conn:
        conn.execute(
            "INSERT INTO join_analytics (channel_id, user_id, status, created_at) VALUES (?, ?, 'joined', ?)",
            (clean_id, user_id, utcnow()),
        )

        # Credit source connected channel
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
                WHERE id = ?
                """,
                (earned_bonus, ch["id"]),
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

        # Update active promotion target current_joins
        conn.execute(
            "UPDATE promotions SET current_joins = current_joins + 1 WHERE id=? AND status='active'",
            (promotion_id,),
        )

        return {"ok": True}


def record_channel_join_event(channel_id: str, user_id: int) -> dict:
    settings = get_settings()
    payout_per_2_joins = float(settings.get("payout_per_2_joins_inr", "0.80"))
    payout_per_join = payout_per_2_joins / 2.0  # 0.40 INR (40 paise) per user join (80% share)

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
                WHERE id = ?
                """,
                (earned_bonus, ch["id"]),
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

        total_channels_row = conn.execute("SELECT COUNT(*) as cnt FROM connected_channels").fetchone()
        total_channels = total_channels_row["cnt"] if total_channels_row else 0

        community_row = conn.execute("SELECT SUM(member_count) as total FROM connected_channels").fetchone()
        total_community = community_row["total"] if community_row and community_row["total"] else 0

        earnings_row = conn.execute("SELECT SUM(total_earned_coins) as total FROM users").fetchone()
        total_earned_coins = earnings_row["total"] if earnings_row and earnings_row["total"] else 0.0
        total_user_earnings_inr = round(float(total_earned_coins) * 0.80, 2)

        return {
            "total_users": total_users,
            "total_ads_watched": total_ads,
            "total_paid_usd": round(float(total_paid_usd), 2),
            "total_paid_inr": round(float(total_paid_usd) * 80.0, 2),
            "total_joins": total_joins,
            "total_lefts": total_lefts,
            "active_promotions": active_promos,
            "total_channels": total_channels,
            "total_community_members": total_community,
            "total_user_earnings_inr": total_user_earnings_inr,
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


def admin_deduct_user_wallet(user_id: int, amount_inr: float, description: str = "Admin Funds Deduction") -> dict:
    user = get_user_by_id(user_id)
    if not user:
        return {"ok": False, "error": f"User ID {user_id} not found."}

    tg_id = user.get("telegram_id") or user["id"]
    amount_paise = int(round(amount_inr * 100))
    if amount_paise <= 0:
        return {"ok": False, "error": "Amount must be greater than zero."}

    w = get_or_create_wallet(tg_id)
    if w["balance_paise"] < amount_paise:
        return {
            "ok": False,
            "error": f"Insufficient wallet balance. Available: ₹{w['balance_paise']/100:.2f}, Requested Deduction: ₹{amount_inr:.2f}."
        }

    now = utcnow()
    order_id = f"ADMIN-DEBIT-{tg_id}-{uuid.uuid4().hex[:8].upper()}"

    with db() as conn:
        conn.execute(
            """
            INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
            VALUES (?, 'admin_debit', ?, 'ADMIN_DEDUCTION', ?, ?, ?)
            """,
            (tg_id, amount_paise, order_id, description or "Admin Grant Deduction", now),
        )
        conn.execute(
            """
            UPDATE wallets
            SET balance_paise = balance_paise - ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (amount_paise, now, tg_id),
        )
        row_w = conn.execute("SELECT * FROM wallets WHERE telegram_id=?", (tg_id,)).fetchone()
        return {
            "ok": True,
            "message": f"Successfully deducted ₹{amount_inr:.2f} from user '{user['username']}' account.",
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
                "SELECT * FROM connected_channels WHERE status IN ('approved', 'active') AND LOWER(category)=LOWER(?) ORDER BY id ASC LIMIT ?",
                (category.strip(), limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]

        rows = conn.execute("SELECT * FROM connected_channels WHERE status IN ('approved', 'active') ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
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


def get_active_promo_rotations_for_promo(promotion_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM promo_rotations WHERE promotion_id=? AND status='active'",
            (promotion_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_promo_rotations_deleted_for_promo(promotion_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE promo_rotations SET status='deleted' WHERE promotion_id=?", (promotion_id,))


def stop_promotion(promo_id: int, user_id: int | None = None) -> dict:
    with db() as conn:
        p = conn.execute("SELECT * FROM promotions WHERE id=?", (promo_id,)).fetchone()
        if not p:
            return {"ok": False, "error": "Promotion campaign not found"}

        if user_id is not None and p["user_id"] != user_id:
            return {"ok": False, "error": "You do not have permission to stop this promotion"}

        old_status = p["status"]
        if old_status in ("stopped", "paused"):
            return {"ok": False, "error": "Promotion is already stopped"}
        if old_status in ("rejected", "completed"):
            return {"ok": False, "error": f"Cannot stop promotion with status '{old_status}'"}

        tot_price = p["total_price_inr"] if (p["total_price_inr"] and p["total_price_inr"] > 0) else (p["price_inr"] or 0.0)
        target = p["target_joins"] or 1
        curr_joins = p["current_joins"] or 0
        if target > 0:
            spent_inr = round((tot_price * min(curr_joins, target)) / target, 2)
        else:
            spent_inr = 0.0

        refund_inr = max(0.0, round(tot_price - spent_inr, 2))
        refund_paise = int(round(refund_inr * 100))

        # Update status to stopped
        conn.execute("UPDATE promotions SET status='stopped' WHERE id=?", (promo_id,))

        # Credit unspent refund paise to user's wallet if any
        owner_user = conn.execute("SELECT telegram_id, id FROM users WHERE id=?", (p["user_id"],)).fetchone()
        if owner_user and refund_paise > 0:
            tg_id = owner_user["telegram_id"] or owner_user["id"]
            now = utcnow()
            order_id = f"REFUND-STOP-{promo_id}-{uuid.uuid4().hex[:6].upper()}"
            desc = f"Refund for Stopped Promotion #{promo_id} (Spent: ₹{spent_inr:.2f}, Refunded: ₹{refund_inr:.2f})"
            conn.execute(
                """
                INSERT INTO wallet_transactions (telegram_id, type, amount_paise, utr, order_id, description, created_at)
                VALUES (?, 'refund', ?, 'PROMO_STOP_REFUND', ?, ?, ?)
                """,
                (tg_id, refund_paise, order_id, desc, now),
            )
            conn.execute(
                """
                INSERT INTO wallets (telegram_id, balance_paise, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET balance_paise = balance_paise + excluded.balance_paise, updated_at = excluded.updated_at
                """,
                (tg_id, refund_paise, now, now),
            )

        # Get active rotations to be cleaned up
        active_rotations = [
            dict(r) for r in conn.execute(
                "SELECT * FROM promo_rotations WHERE promotion_id=? AND status='active'",
                (promo_id,)
            ).fetchall()
        ]

        # Mark rotations as deleted
        conn.execute("UPDATE promo_rotations SET status='deleted' WHERE promotion_id=?", (promo_id,))

        return {
            "ok": True,
            "promotion_id": promo_id,
            "new_status": "stopped",
            "total_price_inr": tot_price,
            "spent_inr": spent_inr,
            "refund_inr": refund_inr,
            "current_joins": curr_joins,
            "target_joins": target,
            "active_rotations": active_rotations,
        }


# Discount Coupon Management Functions
def create_coupon(
    code: str,
    discount_pct: float = 0.0,
    discount_amount_inr: float = 0.0,
    min_budget_inr: float = 0.0,
    max_uses: int = 0
) -> dict:
    clean_code = str(code).strip().upper()
    if not clean_code:
        raise ValueError("Coupon code cannot be empty")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO coupons (code, discount_pct, discount_amount_inr, min_budget_inr, max_uses, used_count, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 1, ?)
            """,
            (clean_code, float(discount_pct or 0.0), float(discount_amount_inr or 0.0), float(min_budget_inr or 0.0), int(max_uses or 0), utcnow()),
        )
        row = conn.execute("SELECT * FROM coupons WHERE code=?", (clean_code,)).fetchone()
        return dict(row)


def get_all_coupons() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_coupon_by_code(code: str) -> dict | None:
    clean_code = str(code).strip().upper()
    with db() as conn:
        row = conn.execute("SELECT * FROM coupons WHERE UPPER(code)=?", (clean_code,)).fetchone()
        return dict(row) if row else None


def toggle_coupon_status(coupon_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM coupons WHERE id=?", (coupon_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Coupon not found"}
        new_status = 0 if row["is_active"] else 1
        conn.execute("UPDATE coupons SET is_active=? WHERE id=?", (new_status, coupon_id))
        return {"ok": True, "is_active": new_status}


def delete_coupon(coupon_id: int) -> dict:
    with db() as conn:
        conn.execute("DELETE FROM coupons WHERE id=?", (coupon_id,))
        return {"ok": True}


def apply_coupon_discount(code: str, budget_inr: float) -> dict:
    clean_code = str(code).strip().upper()
    coupon = get_coupon_by_code(clean_code)
    if not coupon:
        return {"valid": False, "discount_inr": 0.0, "message": "Invalid coupon code."}

    if not coupon.get("is_active"):
        return {"valid": False, "discount_inr": 0.0, "message": "This coupon code is deactivated."}

    min_budget = float(coupon.get("min_budget_inr") or 0.0)
    if budget_inr < min_budget:
        return {"valid": False, "discount_inr": 0.0, "message": f"Coupon requires minimum budget of ₹{min_budget:.2f}."}

    max_uses = int(coupon.get("max_uses") or 0)
    used_count = int(coupon.get("used_count") or 0)
    if max_uses > 0 and used_count >= max_uses:
        return {"valid": False, "discount_inr": 0.0, "message": "Coupon usage limit has been reached."}

    pct = float(coupon.get("discount_pct") or 0.0)
    fixed_amt = float(coupon.get("discount_amount_inr") or 0.0)

    discount_inr = 0.0
    if pct > 0:
        discount_inr += (budget_inr * (pct / 100.0))
    if fixed_amt > 0:
        discount_inr += fixed_amt

    discount_inr = min(discount_inr, budget_inr)
    discount_inr = round(discount_inr, 2)

    return {
        "valid": True,
        "discount_inr": discount_inr,
        "coupon_code": clean_code,
        "message": f"Coupon '{clean_code}' applied successfully!",
        "coupon": coupon
    }


def record_coupon_usage(code: str) -> None:
    clean_code = str(code).strip().upper()
    with db() as conn:
        conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE UPPER(code)=?", (clean_code,))


# Advanced Multi Force-Sub Management Functions
def add_force_sub_channel(
    channel_id: str,
    channel_title: str,
    join_url: str,
    channel_username: str = "",
    invite_type: str = "direct"
) -> dict:
    clean_id = str(channel_id).strip()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO force_sub_channels (channel_id, channel_title, channel_username, join_url, invite_type, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                join_url=excluded.join_url,
                invite_type=excluded.invite_type,
                is_active=1
            """,
            (clean_id, channel_title, channel_username, join_url, invite_type, utcnow()),
        )
        row = conn.execute("SELECT * FROM force_sub_channels WHERE channel_id=?", (clean_id,)).fetchone()
        return dict(row)


def remove_force_sub_channel(channel_id_or_username: str) -> dict:
    arg = str(channel_id_or_username).strip()
    username_part = arg.split("t.me/")[-1].lstrip("@").strip("/")
    at_username = f"@{username_part}" if username_part else arg
    with db() as conn:
        res = conn.execute(
            "DELETE FROM force_sub_channels WHERE channel_id=? OR channel_username=? OR channel_username=?",
            (arg, username_part, at_username),
        )
        return {"ok": True, "deleted": res.rowcount}


def get_all_force_sub_channels(active_only: bool = True) -> list[dict]:
    with db() as conn:
        if active_only:
            rows = conn.execute("SELECT * FROM force_sub_channels WHERE is_active=1 ORDER BY id ASC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM force_sub_channels ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def toggle_force_sub_channel(channel_id: str) -> dict:
    clean_id = str(channel_id).strip()
    with db() as conn:
        row = conn.execute("SELECT * FROM force_sub_channels WHERE channel_id=?", (clean_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Channel not found"}
        new_val = 0 if row["is_active"] else 1
        conn.execute("UPDATE force_sub_channels SET is_active=? WHERE channel_id=?", (new_val, clean_id))
        return {"ok": True, "is_active": new_val}
