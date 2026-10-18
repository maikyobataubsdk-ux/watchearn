from __future__ import annotations
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from config import BOT_TOKEN


class TelegramAuthError(ValueError):
    pass


def validate_init_data(init_data: str, max_age_seconds: int = 86_400) -> dict:
    """Validate Telegram Mini App initData using Telegram's documented HMAC algorithm."""
    if not init_data or len(init_data) > 16_384:
        raise TelegramAuthError("Missing or invalid Telegram initData")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    except Exception as exc:
        raise TelegramAuthError("Could not parse initData") from exc

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("Telegram initData has no hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramAuthError("Telegram initData signature is invalid")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise TelegramAuthError("Invalid auth_date") from exc
    now = int(time.time())
    if auth_date <= 0 or auth_date > now + 60 or now - auth_date > max_age_seconds:
        raise TelegramAuthError("Telegram session expired. Close and reopen the Mini App.")

    try:
        user = json.loads(pairs.get("user", "{}"))
        user_id = int(user["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise TelegramAuthError("Telegram user information is missing") from exc
    return {"user_id": user_id, "user": user, "auth_date": auth_date}
