from __future__ import annotations
import secrets
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import db
from config import HOST, PORT, validate_common
from db import (
    init_db, create_user, get_user_by_email, get_user_by_username,
    get_user_by_id, get_user_by_telegram_id, get_or_create_telegram_user,
    get_all_users, set_user_ban_status, set_user_premium_status, adjust_user_balance,
    get_user_daily_ad_count, get_ip_daily_ad_count, start_ad_session,
    claim_ad_reward, create_withdrawal, get_user_withdrawals, get_all_withdrawals,
    update_withdrawal_status, get_top_earners, get_global_stats,
    get_referral_stats, get_settings, update_settings, get_ad_codes, update_ad_codes,
    hash_password, create_promotion, get_user_promotions, get_all_promotions,
    update_promotion_status, connect_channel, get_user_connected_channels, get_all_connected_channels,
    get_campaign, record_rewarded_intent,
)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Blazeculture PTC & Telegram Platform", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory sessions token map: token -> user_id
USER_SESSIONS: dict[str, int] = {}
ADMIN_SESSIONS: set[str] = set()

ADMIN_USERNAME = "rajpapa"
ADMIN_PASSWORD = "28@RajPapa"


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# Pydantic Schemas
class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=100)
    referralCode: Optional[str] = None


class LoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=120)  # email or username
    password: str = Field(min_length=1, max_length=100)


class TelegramMiniAppAuthRequest(BaseModel):
    telegram_id: Optional[int] = None
    id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    firstName: Optional[str] = None
    last_name: Optional[str] = None
    lastName: Optional[str] = None
    photo_url: Optional[str] = None
    photoUrl: Optional[str] = None
    referralCode: Optional[str] = None
    initData: Optional[str] = None


class WatchAdRequest(BaseModel):
    campaignToken: Optional[str] = None
    campaign: Optional[str] = None


class ClaimRewardRequest(BaseModel):
    sessionId: str = Field(min_length=10, max_length=64)
    campaignToken: Optional[str] = None
    campaign: Optional[str] = None


class WithdrawRequest(BaseModel):
    amountUsd: float = Field(gt=0)
    method: str = Field(min_length=2, max_length=20)  # UPI, Paytm, USDT, PayPal
    accountDetails: str = Field(min_length=3, max_length=200)


class AnalyzeChannelRequest(BaseModel):
    channel_link: Optional[str] = None
    channelUrl: Optional[str] = None


class SubmitPromotionRequest(BaseModel):
    channel_link: Optional[str] = None
    channelUrl: Optional[str] = None
    channel_id: Optional[str] = None
    channelId: Optional[str] = None
    channel_title: Optional[str] = None
    channelTitle: Optional[str] = None
    ad_message: Optional[str] = ""
    budget_inr: Optional[float] = None
    priceInr: Optional[float] = None
    target_joins: Optional[int] = 100
    targetJoins: Optional[int] = 100
    payment_txn_ref: Optional[str] = None
    transactionRef: Optional[str] = None


class ConnectChannelRequest(BaseModel):
    channel_link: Optional[str] = None
    channel_id: Optional[str] = None
    channelId: Optional[str] = None
    channel_title: Optional[str] = None
    channelTitle: Optional[str] = None
    channel_username: Optional[str] = ""
    channelUsername: Optional[str] = ""


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminBanRequest(BaseModel):
    userId: int
    isBanned: bool


class AdminPremiumRequest(BaseModel):
    userId: int
    isPremium: bool


class AdminBalanceRequest(BaseModel):
    userId: int
    newBalance: float


class AdminWithdrawalStatusRequest(BaseModel):
    withdrawalId: int
    status: str  # approved, rejected


class AdminPromotionStatusRequest(BaseModel):
    promotionId: Optional[int] = None
    promotion_id: Optional[int] = None
    status: Optional[str] = "approved"


class AdminPaymentSettingsRequest(BaseModel):
    upi_id: Optional[str] = None
    paymentUpiId: Optional[str] = None
    qr_image_url: Optional[str] = None
    paymentQrUrl: Optional[str] = None


class AdminBotConfigRequest(BaseModel):
    force_chat_id: Optional[str] = None
    force_chat_title: Optional[str] = None
    force_join_url: Optional[str] = None
    target_chat_id: Optional[str] = None
    target_chat_title: Optional[str] = None
    target_invite_mode: Optional[str] = None


class AdminAdCodesRequest(BaseModel):
    interstitial: Optional[str] = ""
    popunder: Optional[str] = ""
    banner: Optional[str] = ""
    direct_link: Optional[str] = ""


class AdminSettingsRequest(BaseModel):
    coins_per_ad: Optional[float] = 2.0
    daily_ad_limit: Optional[int] = 50
    min_withdrawal_usd: Optional[float] = 5.0
    min_withdrawal_inr: Optional[float] = 400.0
    referral_commission_pct: Optional[float] = 15.0
    payment_upi_id: Optional[str] = "blazeculture@upi"
    payment_qr_url: Optional[str] = "/static/payment_qr.jpg"


# Dependency Auth Helpers
def get_current_user(authorization: Optional[str] = Header(None), token: Optional[str] = Query(None)) -> dict:
    auth_token = None
    if authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ", 1)[1]
    elif token:
        auth_token = token

    if not auth_token or auth_token not in USER_SESSIONS:
        raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")

    user_id = USER_SESSIONS[auth_token]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Your account has been suspended by administrator.")

    return user


def require_admin(x_admin_token: Optional[str] = Header(None)) -> bool:
    if not x_admin_token or x_admin_token not in ADMIN_SESSIONS:
        raise HTTPException(status_code=401, detail="Admin authorization required.")
    return True


@app.on_event("startup")
async def startup() -> None:
    init_db()


# Static & Root Page
@app.get("/", response_class=HTMLResponse)
@app.get("/raj", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    html_file = BASE_DIR / "static" / "Index.html"
    if not html_file.exists():
        html_file = BASE_DIR / "static" / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Frontend template static/Index.html not found.")
    html = html_file.read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# Public Endpoints
@app.get("/api/config")
async def public_config() -> dict:
    settings = get_settings()
    ad_codes = get_ad_codes()
    return {
        "appName": "Blazeculture",
        "logoUrl": "/static/logo.png",
        "coinsPerAd": float(settings.get("coins_per_ad", 2.0)),
        "dailyAdLimit": int(settings.get("daily_ad_limit", 50)),
        "minWithdrawalUsd": float(settings.get("min_withdrawal_usd", 5.0)),
        "minWithdrawalInr": float(settings.get("min_withdrawal_inr", 400.0)),
        "coinsPerUsd": float(settings.get("coins_per_usd", 100.0)),
        "referralCommissionPct": float(settings.get("referral_commission_pct", 15.0)),
        "paymentUpiId": settings.get("payment_upi_id", "blazeculture@upi"),
        "paymentQrUrl": settings.get("payment_qr_url", "/static/payment_qr.jpg"),
        "adCodes": ad_codes,
        "supportTelegram": "@Tgfuckerx",
    }


@app.get("/api/stats")
async def public_stats() -> dict:
    stats = get_global_stats()
    leaderboard = get_top_earners(10)
    return {
        "ok": True,
        "stats": stats,
        "leaderboard": leaderboard,
    }


@app.get("/api/campaign/{token}")
async def get_campaign_details(token: str) -> dict:
    campaign = get_campaign(token)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or invalid.")
    return {
        "ok": True,
        "campaign": {
            "token": campaign["token"],
            "target_title": campaign["target_title"],
            "target_chat_id": campaign["target_chat_id"],
            "invite_mode": campaign["invite_mode"],
        },
    }


# User Authentication Endpoints
@app.post("/api/auth/register")
async def register(body: RegisterRequest, request: Request) -> dict:
    if get_user_by_email(body.email):
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    if get_user_by_username(body.username):
        raise HTTPException(status_code=400, detail="This username is already taken.")

    ip_addr = request.client.host if request.client else "127.0.0.1"
    user = create_user(
        email=body.email,
        username=body.username,
        password=body.password,
        referral_code_used=body.referralCode,
        ip_address=ip_addr,
    )

    token = secrets.token_hex(24)
    USER_SESSIONS[token] = user["id"]

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "username": user["username"],
            "balanceCoins": user["balance_coins"],
            "isPremium": bool(user.get("is_premium", 0)),
            "referralCode": user["referral_code"],
        },
    }


@app.post("/api/auth/login")
async def login(body: LoginRequest) -> dict:
    user = get_user_by_email(body.login) or get_user_by_username(body.login)
    if not user or user["password_hash"] != hash_password(body.password):
        raise HTTPException(status_code=400, detail="Invalid email/username or password.")

    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Account is banned. Contact support.")

    token = secrets.token_hex(24)
    USER_SESSIONS[token] = user["id"]

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "telegram_id": user.get("telegram_id"),
            "email": user["email"],
            "username": user["username"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "photo_url": user.get("photo_url", ""),
            "balanceCoins": user["balance_coins"],
            "isPremium": bool(user.get("is_premium", 0)),
            "referralCode": user["referral_code"],
        },
    }


@app.post("/api/auth/telegram-mini-app")
@app.post("/api/auth/telegram")
async def telegram_mini_app_auth(body: TelegramMiniAppAuthRequest, request: Request) -> dict:
    tg_id = body.telegram_id or body.id
    if not tg_id:
        raise HTTPException(status_code=400, detail="Telegram User ID is required.")

    ip_addr = request.client.host if request.client else "127.0.0.1"
    uname = body.username or f"user_{tg_id}"
    fname = body.first_name or body.firstName or ""
    lname = body.last_name or body.lastName or ""
    purl = body.photo_url or body.photoUrl or ""

    user = get_or_create_telegram_user(
        telegram_id=tg_id,
        username=uname,
        first_name=fname,
        last_name=lname,
        photo_url=purl,
        referral_code_used=body.referralCode,
        ip_address=ip_addr,
    )

    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Your account has been suspended by administrator.")

    token = secrets.token_hex(24)
    USER_SESSIONS[token] = user["id"]

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "telegram_id": user.get("telegram_id"),
            "email": user["email"],
            "username": user["username"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "photo_url": user.get("photo_url", ""),
            "balanceCoins": user["balance_coins"],
            "isPremium": bool(user.get("is_premium", 0)),
            "referralCode": user["referral_code"],
        },
    }


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    settings = get_settings()
    daily_ads = get_user_daily_ad_count(user["id"])
    ref_stats = get_referral_stats(user["id"])

    return {
        "ok": True,
        "user": {
            "id": user["id"],
            "telegram_id": user.get("telegram_id"),
            "email": user["email"],
            "username": user["username"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "photo_url": user.get("photo_url", ""),
            "balanceCoins": user["balance_coins"],
            "totalEarnedCoins": user["total_earned_coins"],
            "totalAdsWatched": user["total_ads_watched"],
            "isPremium": bool(user.get("is_premium", 0)),
            "is_premium": bool(user.get("is_premium", 0)),
            "referralCode": user["referral_code"],
            "dailyAdsToday": daily_ads,
            "dailyAdLimit": int(settings.get("daily_ad_limit", 50)),
            "referralStats": ref_stats,
        },
    }


@app.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(None)) -> dict:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        USER_SESSIONS.pop(token, None)
    return {"ok": True}


# Ad Watching & Reward Endpoints
@app.post("/api/user/watch-ad")
async def watch_ad(request: Request, body: Optional[WatchAdRequest] = None, user: dict = Depends(get_current_user)) -> dict:
    settings = get_settings()
    coins_per_ad = float(settings.get("coins_per_ad", 2.0))
    camp_token = (body.campaignToken or body.campaign) if body else None

    if camp_token:
        record_rewarded_intent(user["id"], camp_token)

    if user.get("is_premium"):
        with db.db() as conn:
            conn.execute(
                """
                UPDATE users
                SET balance_coins = balance_coins + ?,
                    total_earned_coins = total_earned_coins + ?,
                    total_ads_watched = total_ads_watched + 1
                WHERE id = ?
                """,
                (coins_per_ad, coins_per_ad, user["id"]),
            )
        return {
            "ok": True,
            "is_premium": True,
            "isPremium": True,
            "reward_coins": coins_per_ad,
            "message": "⭐ Premium VIP User: Ad watching bypassed! Reward added instantly.",
        }

    daily_limit = int(settings.get("daily_ad_limit", 50))
    daily_count = get_user_daily_ad_count(user["id"])
    if daily_count >= daily_limit:
        raise HTTPException(status_code=400, detail=f"You have reached the daily limit of {daily_limit} ads.")

    ip_addr = request.client.host if request.client else "127.0.0.1"
    ip_daily = get_ip_daily_ad_count(ip_addr)
    if ip_daily >= daily_limit * 2:
        raise HTTPException(status_code=400, detail="Daily ad limit reached for this IP address.")

    session_info = start_ad_session(
        user_id=user["id"],
        ip_address=ip_addr,
        timer_seconds=20,
        reward_coins=coins_per_ad,
    )

    ad_codes = get_ad_codes()

    return {
        "ok": True,
        "is_premium": False,
        "sessionId": session_info["session_id"],
        "timerSeconds": session_info["timer_seconds"],
        "rewardCoins": session_info["reward_coins"],
        "adCodes": ad_codes,
    }


@app.post("/api/user/claim-reward")
async def claim_reward(body: ClaimRewardRequest, user: dict = Depends(get_current_user)) -> dict:
    camp_token = body.campaignToken or body.campaign
    if camp_token:
        record_rewarded_intent(user["id"], camp_token)

    res = claim_ad_reward(body.sessionId, user["id"])
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to claim reward."))

    return res


# Withdrawal Endpoints
@app.post("/api/user/withdraw")
async def request_withdraw(body: WithdrawRequest, user: dict = Depends(get_current_user)) -> dict:
    settings = get_settings()
    min_usd = float(settings.get("min_withdrawal_usd", 5.0))
    coins_per_usd = float(settings.get("coins_per_usd", 100.0))

    if body.amountUsd < min_usd:
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal amount is ${min_usd:.2f} (₹{min_usd*80:.0f}).")

    coins_needed = body.amountUsd * coins_per_usd
    if user["balance_coins"] < coins_needed:
        raise HTTPException(status_code=400, detail=f"Insufficient balance. You need {coins_needed:.0f} coins for ${body.amountUsd:.2f}.")

    amount_inr = body.amountUsd * 80.0

    res = create_withdrawal(
        user_id=user["id"],
        amount_usd=body.amountUsd,
        amount_inr=amount_inr,
        coins=coins_needed,
        method=body.method,
        account_details=body.accountDetails,
    )

    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Withdrawal failed."))

    return {"ok": True, "message": "Withdrawal request submitted successfully!"}


@app.get("/api/user/withdrawals")
async def user_withdrawals(user: dict = Depends(get_current_user)) -> dict:
    withdrawals = get_user_withdrawals(user["id"])
    return {"ok": True, "withdrawals": withdrawals}


# Promotion & Connected Channel User Endpoints
@app.get("/api/promotions/payment-qr")
async def get_payment_qr_public() -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "upi_id": settings.get("payment_upi_id", "blazeculture@upi"),
        "qr_image_url": settings.get("payment_qr_url", "/static/payment_qr.jpg"),
    }


@app.post("/api/promotions/analyze")
async def analyze_channel(body: AnalyzeChannelRequest, user: dict = Depends(get_current_user)) -> dict:
    clean_url = (body.channel_link or body.channelUrl or "").strip()
    if not clean_url or ("t.me/" not in clean_url and not clean_url.startswith("@")):
        raise HTTPException(status_code=400, detail="Please enter a valid Telegram channel link or @username")

    channel_username = clean_url.split("t.me/")[-1].replace("@", "").strip("/")
    channel_id = f"@{channel_username}" if channel_username else clean_url

    # Check if channel exists in connected channels
    connected = None
    with db.db() as conn:
        row = conn.execute("SELECT * FROM connected_channels WHERE channel_id=? OR channel_username=?", (channel_id, channel_username)).fetchone()
        if row:
            connected = dict(row)

    members_str = f"{connected['total_joins']} Connected Joins" if connected else "Verified Active Telegram Channel"

    return {
        "ok": True,
        "channel_link": clean_url,
        "title": connected["channel_title"] if connected else f"Telegram Channel ({channel_id})",
        "username": f"@{channel_username}" if channel_username else clean_url,
        "members_count": members_str,
        "bot_is_admin": True,
    }


@app.post("/api/promotions/submit")
async def submit_promotion(body: SubmitPromotionRequest, user: dict = Depends(get_current_user)) -> dict:
    url = body.channel_link or body.channelUrl or "https://t.me/channel"
    c_id = body.channel_id or body.channelId or url
    c_title = body.channel_title or body.channelTitle or f"Channel ({c_id})"
    price = body.budget_inr or body.priceInr or 100.0
    joins = body.target_joins or body.targetJoins or int(price * 2)
    txn_ref = body.payment_txn_ref or body.transactionRef or "TXN_PENDING"

    promo = create_promotion(
        user_id=user["id"],
        channel_url=url,
        channel_id=c_id,
        channel_title=c_title,
        price_inr=price,
        target_joins=joins,
        transaction_ref=txn_ref,
    )
    return {
        "ok": True,
        "message": "Promotion request submitted successfully! Pending admin QR payment verification & approval.",
        "promotion": promo,
    }


@app.get("/api/promotions/my")
@app.get("/api/user/promotions")
async def user_promotions(user: dict = Depends(get_current_user)) -> dict:
    promos = get_user_promotions(user["id"])
    return {"ok": True, "promotions": promos}


@app.post("/api/channels/connect")
async def user_connect_channel(body: ConnectChannelRequest, user: dict = Depends(get_current_user)) -> dict:
    url = body.channel_link or body.channel_id or body.channelId or "https://t.me/channel"
    c_id = body.channel_id or body.channelId or url
    c_title = body.channel_title or body.channelTitle or f"Channel ({c_id})"
    c_username = body.channel_username or body.channelUsername or url.split("t.me/")[-1]

    ch = connect_channel(
        owner_user_id=user["id"],
        channel_id=c_id,
        channel_title=c_title,
        channel_username=c_username,
    )
    return {
        "ok": True,
        "message": "Channel connected successfully! Ad earnings and join analytics enabled.",
        "channel": ch,
    }


@app.get("/api/channels/my")
async def my_connected_channels(user: dict = Depends(get_current_user)) -> dict:
    channels = get_user_connected_channels(user["id"])
    return {"ok": True, "channels": channels}


# Admin Endpoints (/raj)
@app.post("/api/admin/login")
async def admin_login(body: AdminLoginRequest) -> dict:
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        admin_token = secrets.token_hex(24)
        ADMIN_SESSIONS.add(admin_token)
        return {"ok": True, "adminToken": admin_token}
    raise HTTPException(status_code=401, detail="Invalid admin credentials.")


@app.get("/api/admin/stats")
async def admin_stats(_: bool = Depends(require_admin)) -> dict:
    stats = get_global_stats()
    users = get_all_users()
    withdrawals = get_all_withdrawals()
    promotions = get_all_promotions()
    channels = get_all_connected_channels()
    pending_w = sum(1 for w in withdrawals if w["status"] == "pending")
    pending_p = sum(1 for p in promotions if p["status"] == "pending_approval" or p["status"] == "pending")

    return {
        "ok": True,
        "stats": stats,
        "totalUsersCount": len(users),
        "pendingWithdrawalsCount": pending_w,
        "pendingPromotionsCount": pending_p,
        "connectedChannelsCount": len(channels),
    }


@app.get("/api/admin/bot-config")
async def admin_get_bot_config(_: bool = Depends(require_admin)) -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "force_chat_id": settings.get("force_chat_id", ""),
        "force_chat_title": settings.get("force_chat_title", ""),
        "force_join_url": settings.get("force_join_url", ""),
        "target_chat_id": settings.get("target_chat_id", ""),
        "target_chat_title": settings.get("target_chat_title", ""),
        "target_invite_mode": settings.get("target_invite_mode", "direct"),
    }


@app.post("/api/admin/bot-config")
async def admin_save_bot_config(body: AdminBotConfigRequest, _: bool = Depends(require_admin)) -> dict:
    new_settings = {}
    if body.force_chat_id is not None: new_settings["force_chat_id"] = body.force_chat_id
    if body.force_chat_title is not None: new_settings["force_chat_title"] = body.force_chat_title
    if body.force_join_url is not None: new_settings["force_join_url"] = body.force_join_url
    if body.target_chat_id is not None: new_settings["target_chat_id"] = body.target_chat_id
    if body.target_chat_title is not None: new_settings["target_chat_title"] = body.target_chat_title
    if body.target_invite_mode is not None: new_settings["target_invite_mode"] = body.target_invite_mode
    update_settings(new_settings)
    return {"ok": True, "message": "Bot configuration updated successfully!"}


@app.get("/api/admin/payment-qr")
async def admin_get_payment_qr(_: bool = Depends(require_admin)) -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "upi_id": settings.get("payment_upi_id", "blazeculture@upi"),
        "qr_image_url": settings.get("payment_qr_url", "/static/payment_qr.jpg"),
    }


@app.post("/api/admin/payment-qr")
async def admin_save_payment_qr(body: AdminPaymentSettingsRequest, _: bool = Depends(require_admin)) -> dict:
    upi = body.upi_id or body.paymentUpiId or "blazeculture@upi"
    qr = body.qr_image_url or body.paymentQrUrl or "/static/payment_qr.jpg"
    update_settings({"payment_upi_id": upi, "payment_qr_url": qr})
    return {"ok": True, "message": "Payment QR code & UPI ID updated successfully!"}


@app.get("/api/admin/users")
async def admin_users(_: bool = Depends(require_admin)) -> dict:
    users = get_all_users()
    return {"ok": True, "users": users}


@app.post("/api/admin/user/ban")
async def admin_ban_user(body: AdminBanRequest, _: bool = Depends(require_admin)) -> dict:
    set_user_ban_status(body.userId, body.isBanned)
    return {"ok": True, "message": f"User ID {body.userId} ban status set to {body.isBanned}"}


@app.post("/api/admin/users/premium")
@app.post("/api/admin/user/premium")
async def admin_premium_user(body: AdminPremiumRequest, _: bool = Depends(require_admin)) -> dict:
    set_user_premium_status(body.userId, body.isPremium)
    return {"ok": True, "message": f"User ID {body.userId} premium status set to {body.isPremium}"}


@app.post("/api/admin/user/balance")
async def admin_adjust_balance(body: AdminBalanceRequest, _: bool = Depends(require_admin)) -> dict:
    adjust_user_balance(body.userId, body.newBalance)
    return {"ok": True, "message": f"User ID {body.userId} balance updated to {body.newBalance}"}


@app.get("/api/admin/withdrawals")
async def admin_withdrawals(_: bool = Depends(require_admin)) -> dict:
    withdrawals = get_all_withdrawals()
    return {"ok": True, "withdrawals": withdrawals}


@app.post("/api/admin/withdrawal/status")
async def admin_update_withdrawal(body: AdminWithdrawalStatusRequest, _: bool = Depends(require_admin)) -> dict:
    res = update_withdrawal_status(body.withdrawalId, body.status)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update withdrawal status."))
    return {"ok": True}


@app.get("/api/admin/promotions")
async def admin_get_promotions(_: bool = Depends(require_admin)) -> dict:
    promotions = get_all_promotions()
    channels = get_all_connected_channels()
    return {"ok": True, "promotions": promotions, "connectedChannels": channels}


@app.post("/api/admin/promotions/approve")
@app.post("/api/admin/promotions/reject")
@app.post("/api/admin/promotion/status")
async def admin_update_promotion_status(body: AdminPromotionStatusRequest, request: Request, _: bool = Depends(require_admin)) -> dict:
    p_id = body.promotionId or body.promotion_id
    if not p_id:
        raise HTTPException(status_code=400, detail="promotionId is required")

    status = body.status
    if "approve" in request.url.path:
        status = "approved"
    elif "reject" in request.url.path:
        status = "rejected"

    res = update_promotion_status(p_id, status)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update promotion status."))

    return {"ok": True, "message": f"Promotion #{p_id} status updated to {status}"}


@app.get("/api/admin/ad-codes")
async def admin_get_ad_codes(_: bool = Depends(require_admin)) -> dict:
    codes = get_ad_codes()
    settings = get_settings()
    return {"ok": True, "adCodes": codes, "settings": settings}


@app.post("/api/admin/ad-codes")
async def admin_save_ad_codes(body: AdminAdCodesRequest, _: bool = Depends(require_admin)) -> dict:
    update_ad_codes({
        "interstitial": body.interstitial or "",
        "popunder": body.popunder or "",
        "banner": body.banner or "",
        "direct_link": body.direct_link or "",
    })
    return {"ok": True, "message": "High-paying ad codes updated successfully!"}


@app.post("/api/admin/settings")
async def admin_save_settings(body: AdminSettingsRequest, _: bool = Depends(require_admin)) -> dict:
    update_settings({
        "coins_per_ad": body.coins_per_ad or 2.0,
        "daily_ad_limit": body.daily_ad_limit or 50,
        "min_withdrawal_usd": body.min_withdrawal_usd or 5.0,
        "min_withdrawal_inr": body.min_withdrawal_inr or 400.0,
        "referral_commission_pct": body.referral_commission_pct or 15.0,
        "payment_upi_id": body.payment_upi_id or "blazeculture@upi",
        "payment_qr_url": body.payment_qr_url or "/static/payment_qr.jpg",
    })
    return {"ok": True, "message": "Blazeculture system settings updated successfully!"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web:app", host=HOST, port=PORT, reload=False)
