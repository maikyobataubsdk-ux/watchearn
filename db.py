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
            "users": [dict(r) for r in conn.execute("SELECT id, email, username, balance_coins, total_earned_coins, total_ads_watched, is_banned, created_at FROM users").fetchall()],
            "withdrawals": [dict(r) for r in conn.execute("SELECT * FROM withdrawals ORDER BY id DESC LIMIT 100").fetchall()],
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
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                balance_coins REAL DEFAULT 0.0,
                total_earned_coins REAL DEFAULT 0.0,
                total_ads_watched INTEGER DEFAULT 0,
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

        # Default settings if not set
        default_settings = {
            "coins_per_ad": "2.0",
            "daily_ad_limit": "50",
            "min_withdrawal_usd": "5.0",
            "min_withdrawal_inr": "400.0",
            "coins_per_usd": "100.0",
            "referral_commission_pct": "15.0",
        }
        for k, v in default_settings.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Default ad codes placeholders
        default_ad_codes = {
            "interstitial": "<!-- Adsterra / HilltopAds / Monetag Interstitial Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Interstitial Script here\n  console.log('Adsterra / HilltopAds Interstitial Loaded');\n</script>",
            "popunder": "<!-- Adsterra / HilltopAds / Monetag Popunder Script Placeholder -->\n<script type=\"text/javascript\">\n  // Paste your High-Paying Popunder Script here\n  console.log('Adsterra / HilltopAds Popunder Loaded');\n</script>",
            "banner": "<div style=\"background: rgba(0,255,136,0.1); border: 1px dashed #00ff88; color: #00ff88; padding: 15px; text-align: center; border-radius: 10px;\"><strong>High-Paying Banner Ad Slot</strong><br/>Paste your Adsterra / HilltopAds Banner Code here</div>",
            "direct_link": "https://www.google.com"
        }
        for k, v in default_ad_codes.items():
            conn.execute("INSERT OR IGNORE INTO ad_codes (key, value) VALUES (?, ?)", (k, v))


# Settings & Ad Codes Functions
def get_settings() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


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
            INSERT INTO users (email, username, password_hash, referral_code, referred_by, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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


def get_user_by_referral_code(code: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE UPPER(referral_code)=UPPER(?)", (code.strip(),)).fetchone()
        return dict(row) if row else None


def get_all_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT id, email, username, balance_coins, total_earned_coins, total_ads_watched, is_banned, ip_address, created_at FROM users ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def set_user_ban_status(user_id: int, is_banned: bool) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if is_banned else 0, user_id))


def adjust_user_balance(user_id: int, new_balance: float) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET balance_coins=? WHERE id=?", (float(new_balance), user_id))


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

        return {
            "total_users": total_users,
            "total_ads_watched": total_ads,
            "total_paid_usd": round(float(total_paid_usd), 2),
            "total_paid_inr": round(float(total_paid_usd) * 80.0, 2),
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
