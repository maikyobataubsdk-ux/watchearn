from __future__ import annotations
import hmac
import json
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import (
    BOT_TOKEN, WEBAPP_URL, MONETAG_SDK_URL, MONETAG_ZONE_ID,
    MONETAG_POSTBACK_SECRET, validate_common,
)
from db import (
    init_db, get_campaign, get_setting, has_unlock, has_rewarded_intent,
    create_ad_intent, get_recent_pending_intent, get_intent_status_for_user,
    handle_postback,
)
from telegram_auth import validate_init_data, TelegramAuthError

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Telegram Channel Gate Mini App", docs_url=None, redoc_url=None)
tg_bot: Bot | None = None


class SessionRequest(BaseModel):
    initData: str = Field(min_length=1, max_length=16_384)
    campaign: str = Field(min_length=6, max_length=32)


class AdIntentRequest(BaseModel):
    initData: str = Field(min_length=1, max_length=16_384)
    campaign: str = Field(min_length=6, max_length=32)


async def force_sub_status(user_id: int) -> tuple[bool, str | None, str | None]:
    force_chat_id = get_setting("force_chat_id")
    if not force_chat_id:
        return True, None, None
    join_url = get_setting("force_join_url")
    if tg_bot is None:
        return False, join_url, get_setting("force_chat_title")
    try:
        force_chat = int(force_chat_id) if str(force_chat_id).lstrip("-").isdigit() else force_chat_id
        member = await tg_bot.get_chat_member(chat_id=force_chat, user_id=user_id)
        status = getattr(member, "status", "")
        is_member = status in ("creator", "administrator", "member") or (
            status == "restricted" and bool(getattr(member, "is_member", False))
        )
        return is_member, join_url, get_setting("force_chat_title")
    except (TelegramBadRequest, TelegramForbiddenError):
        return False, join_url, get_setting("force_chat_title")


def auth_user(init_data: str) -> dict:
    try:
        return validate_init_data(init_data)
    except TelegramAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_campaign(token: str):
    campaign = get_campaign(token)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@app.on_event("startup")
async def startup() -> None:
    global tg_bot
    validate_common()
    init_db()
    tg_bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@app.on_event("shutdown")
async def shutdown() -> None:
    if tg_bot is not None:
        await tg_bot.session.close()


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
async def public_config() -> dict:
    # SDK URL and zone ID are public integration settings; never expose the postback secret.
    return {
        "zoneId": MONETAG_ZONE_ID,
        "sdkUrl": MONETAG_SDK_URL,
        "adsConfigured": bool(MONETAG_ZONE_ID and MONETAG_SDK_URL),
        "webAppUrl": WEBAPP_URL,
    }


@app.post("/api/session")
async def create_session(body: SessionRequest) -> dict:
    auth = auth_user(body.initData)
    campaign = require_campaign(body.campaign)
    subscribed, force_url, force_title = await force_sub_status(auth["user_id"])
    unlocked = has_unlock(auth["user_id"], body.campaign) or has_rewarded_intent(auth["user_id"], body.campaign)
    return {
        "ok": True,
        "userId": auth["user_id"],
        "campaign": body.campaign,
        "targetTitle": campaign["target_title"],
        "forceSubscribed": subscribed,
        "forceJoinUrl": force_url,
        "forceTitle": force_title,
        "unlocked": unlocked,
        "adsConfigured": bool(MONETAG_ZONE_ID and MONETAG_SDK_URL and MONETAG_POSTBACK_SECRET),
    }


@app.post("/api/ad-intent")
async def new_ad_intent(body: AdIntentRequest) -> dict:
    auth = auth_user(body.initData)
    campaign = require_campaign(body.campaign)
    subscribed, _, force_title = await force_sub_status(auth["user_id"])
    if not subscribed:
        raise HTTPException(status_code=403, detail=f"Join the required channel first: {force_title or 'required channel'}")
    if has_unlock(auth["user_id"], body.campaign) or has_rewarded_intent(auth["user_id"], body.campaign):
        return {"ok": True, "alreadyUnlocked": True}
    if not (MONETAG_ZONE_ID and MONETAG_SDK_URL and MONETAG_POSTBACK_SECRET):
        raise HTTPException(status_code=503, detail="Rewarded ads are not configured yet. Add the approved Monetag SDK URL, zone ID, and postback secret in .env.")
    recent = get_recent_pending_intent(auth["user_id"], body.campaign)
    if recent:
        return {"ok": True, "ymid": recent["ymid"], "status": "pending"}
    ymid = uuid.uuid4().hex
    create_ad_intent(ymid, auth["user_id"], body.campaign)
    return {"ok": True, "ymid": ymid, "status": "pending", "targetTitle": campaign["target_title"]}


@app.get("/api/ad-status")
async def ad_status(
    init_data: str = Query(alias="initData", min_length=1, max_length=16_384),
    ymid: str = Query(min_length=16, max_length=64),
):
    auth = auth_user(init_data)
    row = get_intent_status_for_user(ymid, auth["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="Ad session not found")
    return {"ok": True, "status": row["status"], "campaign": row["campaign_token"]}


@app.get("/monetag/postback", response_class=PlainTextResponse)
async def monetag_postback(
    secret: str = Query(default=""),
    ymid: str = Query(default=""),
    event_type: str = Query(default=""),
    reward_event_type: str = Query(default=""),
    estimated_price: str = Query(default=""),
    telegram_id: str = Query(default=""),
    zone_id: str = Query(default=""),
    sub_zone_id: str = Query(default=""),
    request_var: str = Query(default=""),
):
    if not MONETAG_POSTBACK_SECRET or not hmac.compare_digest(secret, MONETAG_POSTBACK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid postback secret")
    if not ymid:
        raise HTTPException(status_code=400, detail="Missing ymid")
    processed = handle_postback(ymid, event_type, reward_event_type, estimated_price or None)
    if not processed:
        # Unknown ymid can result from a stale or invalid request; do not issue any reward.
        return PlainTextResponse("ignored", status_code=200)
    return PlainTextResponse("ok", status_code=200)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT
    uvicorn.run("web:app", host=HOST, port=PORT, reload=False)
