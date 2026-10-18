from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from config import DATABASE_PATH

DB_FILE = Path(DATABASE_PATH)
if str(DB_FILE.parent) not in ("", "."):
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                token TEXT PRIMARY KEY,
                target_chat_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                invite_mode TEXT NOT NULL CHECK(invite_mode IN ('direct','request')),
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ad_intents (
                ymid TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                campaign_token TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','rewarded','non_valued')),
                created_at TEXT NOT NULL,
                rewarded_at TEXT,
                estimated_price TEXT,
                FOREIGN KEY(campaign_token) REFERENCES campaigns(token)
            );
            CREATE INDEX IF NOT EXISTS idx_intents_user_campaign
                ON ad_intents(user_id, campaign_token, status, created_at);
            CREATE TABLE IF NOT EXISTS unlocks (
                user_id INTEGER NOT NULL,
                campaign_token TEXT NOT NULL,
                invite_link TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, campaign_token),
                FOREIGN KEY(campaign_token) REFERENCES campaigns(token)
            );
            """
        )


def set_setting(key: str, value: str | None) -> None:
    with db() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


def get_setting(key: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def create_campaign(token: str, target_chat_id: str, target_title: str, invite_mode: str, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO campaigns(token,target_chat_id,target_title,invite_mode,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (token, target_chat_id, target_title, invite_mode, admin_id, utcnow()),
        )


def get_campaign(token: str):
    with db() as conn:
        return conn.execute("SELECT * FROM campaigns WHERE token=?", (token,)).fetchone()


def create_ad_intent(ymid: str, user_id: int, campaign_token: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO ad_intents(ymid,user_id,campaign_token,status,created_at) VALUES(?,?,?,'pending',?)",
            (ymid, user_id, campaign_token, utcnow()),
        )


def get_recent_pending_intent(user_id: int, campaign_token: str, seconds: int = 30):
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM ad_intents WHERE user_id=? AND campaign_token=? AND status='pending' "
            "ORDER BY created_at DESC LIMIT 10",
            (user_id, campaign_token),
        ).fetchall()
        for row in rows:
            try:
                created = datetime.fromisoformat(row["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created >= cutoff:
                    return row
            except ValueError:
                continue
        return None


def get_intent(ymid: str):
    with db() as conn:
        return conn.execute("SELECT * FROM ad_intents WHERE ymid=?", (ymid,)).fetchone()


def handle_postback(ymid: str, event_type: str, reward_event_type: str, estimated_price: str | None) -> bool:
    """Return True if a matching pending intent was processed."""
    if event_type != "impression":
        return False
    with db() as conn:
        row = conn.execute("SELECT status FROM ad_intents WHERE ymid=?", (ymid,)).fetchone()
        if not row:
            return False
        if row["status"] != "pending":
            return True  # idempotent response for provider retries
        if reward_event_type == "valued":
            conn.execute(
                "UPDATE ad_intents SET status='rewarded', rewarded_at=?, estimated_price=? WHERE ymid=? AND status='pending'",
                (utcnow(), estimated_price, ymid),
            )
        elif reward_event_type == "non_valued":
            conn.execute(
                "UPDATE ad_intents SET status='non_valued', estimated_price=? WHERE ymid=? AND status='pending'",
                (estimated_price, ymid),
            )
        else:
            return False
        return True


def has_unlock(user_id: int, campaign_token: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM unlocks WHERE user_id=? AND campaign_token=?",
            (user_id, campaign_token),
        ).fetchone()
        return row is not None


def has_rewarded_intent(user_id: int, campaign_token: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM ad_intents WHERE user_id=? AND campaign_token=? AND status='rewarded' LIMIT 1",
            (user_id, campaign_token),
        ).fetchone()
        return row is not None


def save_unlock(user_id: int, campaign_token: str, invite_link: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO unlocks(user_id,campaign_token,invite_link,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,campaign_token) DO NOTHING",
            (user_id, campaign_token, invite_link, utcnow()),
        )


def get_unlock(user_id: int, campaign_token: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM unlocks WHERE user_id=? AND campaign_token=?",
            (user_id, campaign_token),
        ).fetchone()


def get_intent_status_for_user(ymid: str, user_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT status,campaign_token FROM ad_intents WHERE ymid=? AND user_id=?",
            (ymid, user_id),
        ).fetchone()
