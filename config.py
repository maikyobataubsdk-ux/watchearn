from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USER_ID_RAW = os.getenv("ADMIN_USER_ID", "0").strip()
try:
    ADMIN_USER_ID = int(ADMIN_USER_ID_RAW)
except ValueError:
    ADMIN_USER_ID = 0

WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
MONETAG_SDK_URL = os.getenv("MONETAG_SDK_URL", "").strip()
MONETAG_ZONE_ID = os.getenv("MONETAG_ZONE_ID", "").strip()
MONETAG_POSTBACK_SECRET = os.getenv("MONETAG_POSTBACK_SECRET", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data.sqlite3")).strip()
HOST = os.getenv("HOST", "0.0.0.0").strip()
try:
    PORT = int(os.getenv("PORT", "8000"))
except ValueError:
    PORT = 8000


def validate_common() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN in .env")
    if ADMIN_USER_ID <= 0:
        raise RuntimeError("Set ADMIN_USER_ID to your numeric Telegram user ID in .env")
