from __future__ import annotations
import hmac
import hashlib
import json
import asyncio
import secrets
import time
import uuid
from pathlib import Path
from typing import Optional
import httpx

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
    get_referral_stats, get_settings, get_setting, update_settings, get_ad_codes, update_ad_codes,
    hash_password, create_promotion, get_user_promotions, get_all_promotions,
    update_promotion_status, stop_promotion, connect_channel, update_connected_channel_status,
    get_user_connected_channels, get_all_connected_channels,
    delete_connected_channel, admin_delete_connected_channel,
    get_campaign, record_rewarded_intent,
    get_or_create_wallet, get_wallet_balance, credit_wallet_deposit, get_wallet_transactions,
    deduct_wallet_balance_paise, admin_credit_user_wallet, admin_deduct_user_wallet,
    create_task, get_all_tasks, get_task_by_id, get_user_tasks, complete_task, delete_task, toggle_task_active,
    set_custom_user_rate, get_custom_user_rate, set_user_agreement_status,
    create_coupon, get_all_coupons, get_coupon_by_code, toggle_coupon_status,
    delete_coupon, apply_coupon_discount, record_coupon_usage,
    add_force_sub_channel, remove_force_sub_channel, get_all_force_sub_channels, toggle_force_sub_channel,
)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Earn Program PTC & Telegram Platform", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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

def load_admin_sessions() -> set[str]:
    try:
        raw = get_setting("admin_sessions") or ""
        if raw:
            tokens = set(raw.split(","))
            return {t.strip() for t in tokens if t.strip()}
    except Exception:
        pass
    return set()

def save_admin_session(token: str) -> None:
    ADMIN_SESSIONS.add(token)
    try:
        current = load_admin_sessions()
        current.add(token)
        update_settings({"admin_sessions": ",".join(current)})
    except Exception:
        pass

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
    target_joins: Optional[int] = None
    targetJoins: Optional[int] = None
    payment_txn_ref: Optional[str] = None
    transactionRef: Optional[str] = None
    category: Optional[str] = "General"
    is_category_specific: Optional[bool] = False
    isCategorySpecific: Optional[bool] = False
    image_url: Optional[str] = ""
    imageUrl: Optional[str] = ""
    link_type: Optional[str] = "direct"
    linkType: Optional[str] = "direct"
    coupon_code: Optional[str] = None
    couponCode: Optional[str] = None


class ApplyCouponRequest(BaseModel):
    coupon_code: str = Field(min_length=1, max_length=50)
    budget_inr: float = Field(gt=0)


class CreateCouponRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    discount_pct: Optional[float] = 0.0
    discount_amount_inr: Optional[float] = 0.0
    min_budget_inr: Optional[float] = 0.0
    max_uses: Optional[int] = 0


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    description: Optional[str] = ""
    link_url: str = Field(min_length=5, max_length=500)
    reward_inr: float = Field(gt=0)


class CompleteTaskRequest(BaseModel):
    taskId: int


class AdminTaskActionRequest(BaseModel):
    taskId: int


class AdminCustomRateRequest(BaseModel):
    userId: int
    membersPerInr: float = Field(ge=0)


class AdminDiscountRequest(BaseModel):
    discountPct: float = Field(ge=0, le=100)


class AdminMaintenanceRequest(BaseModel):
    maintenanceFull: Optional[str] = None
    maintenanceWatch: Optional[str] = None
    maintenancePromote: Optional[str] = None
    maintenanceEarnings: Optional[str] = None
    maintenanceWithdraw: Optional[str] = None


class AdminStaggeredBroadcastRequest(BaseModel):
    message_text: str = Field(min_length=1)
    image_url: Optional[str] = ""
    button_name: Optional[str] = ""
    button_link: Optional[str] = ""
    link_type: Optional[str] = "direct"
    batch_size: Optional[int] = 20
    duration_minutes: Optional[int] = 60


class ConnectChannelRequest(BaseModel):
    channel_link: Optional[str] = None
    channel_id: Optional[str] = None
    channelId: Optional[str] = None
    channel_title: Optional[str] = None
    channelTitle: Optional[str] = None
    channel_username: Optional[str] = ""
    channelUsername: Optional[str] = ""
    category: Optional[str] = "General"


class DeleteChannelRequest(BaseModel):
    channel_id: Optional[str] = None
    channelId: Optional[str] = None


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


class AdminAddFundsRequest(BaseModel):
    userId: int
    amountInr: float = Field(gt=0)
    description: Optional[str] = "Admin Funds Addition"


class AdminRemoveFundsRequest(BaseModel):
    userId: int
    amountInr: float = Field(gt=0)
    description: Optional[str] = "Admin Funds Deduction"


class AdminWithdrawalStatusRequest(BaseModel):
    withdrawalId: int
    status: str  # approved, rejected


class AdminPromotionStatusRequest(BaseModel):
    promotionId: Optional[int] = None
    promotion_id: Optional[int] = None
    status: Optional[str] = "approved"
    rejection_reason: Optional[str] = ""
    rejectionReason: Optional[str] = ""
    reason: Optional[str] = ""


class StopPromotionRequest(BaseModel):
    promotion_id: Optional[int] = None
    promotionId: Optional[int] = None


class AdminChannelStatusRequest(BaseModel):
    channel_id: Optional[str] = None
    channelId: Optional[str] = None
    status: str  # approved, rejected, banned, pending_approval


class ConfirmPaymentRequest(BaseModel):
    order_id: str = Field(min_length=3, max_length=100)
    amount_inr: float = Field(gt=0)
    utr: Optional[str] = None


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
    bot_start_quote: Optional[str] = None
    bot_tutorial_url: Optional[str] = None
    bot_help_url: Optional[str] = None
    force_sub_enabled: Optional[str] = None


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
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="Admin authorization required.")
    if x_admin_token in ADMIN_SESSIONS:
        return True
    persisted = load_admin_sessions()
    if x_admin_token in persisted:
        ADMIN_SESSIONS.add(x_admin_token)
        return True
    raise HTTPException(status_code=401, detail="Admin authorization required.")


def check_maintenance(section: str = "full") -> None:
    settings = get_settings()
    if settings.get("maintenance_full", "0") == "1":
        raise HTTPException(status_code=503, detail="Site is currently on maintenance. Please check back later.")
    if section != "full" and settings.get(f"maintenance_{section}", "0") == "1":
        raise HTTPException(status_code=503, detail=f"The {section} section is currently on maintenance.")


def normalize_telegram_channel(input_str: str) -> tuple[str, str, str]:
    """
    Parses any telegram channel link or input format and returns:
    (clean_url, channel_id, channel_username)
    """
    s = (input_str or "").strip()
    if not s:
        return "", "", ""

    s = s.rstrip("/")
    s = s.replace("telegram.me/", "t.me/")

    if "t.me/" in s:
        path = s.split("t.me/")[-1].split("?")[0].strip()
        parts = [p for p in path.split("/") if p]
        if not parts:
            return "", "", ""
        target = parts[0].strip()
    else:
        target = s.lstrip("@").split("?")[0].split("/")[0].strip()

    if not target:
        return "", "", ""

    if target.startswith("+") or target.startswith("joinchat"):
        code = target if target.startswith("+") else target.replace("joinchat/", "")
        clean_url = f"https://t.me/{code}"
        channel_id = code
        channel_username = ""
        return clean_url, channel_id, channel_username

    username = target.replace("@", "")
    clean_url = f"https://t.me/{username}"
    channel_id = f"@{username}"
    channel_username = username
    return clean_url, channel_id, channel_username


async def async_fetch_tg_channel_info(channel_username: str, channel_id: str) -> dict | None:
    from config import BOT_TOKEN
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        return None

    chat_ref = f"@{channel_username}" if channel_username else channel_id
    if not chat_ref.startswith("@") and not chat_ref.startswith("-"):
        return None

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            res = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChat", params={"chat_id": chat_ref})
            data = res.json()
            if data.get("ok"):
                chat = data.get("result", {})
                count_res = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMemberCount", params={"chat_id": chat_ref})
                count_data = count_res.json()
                members_count = count_data.get("result") if count_data.get("ok") else None

                title = chat.get("title") or chat.get("first_name") or chat_ref
                username_str = f"@{chat.get('username')}" if chat.get("username") else chat_ref
                members_str = f"{members_count:,} Subscribers" if members_count else "Active Telegram Channel"

                return {
                    "title": title,
                    "username": username_str,
                    "members_count": members_str,
                    "bot_is_admin": True
                }
    except Exception:
        pass
    return None


async def delete_promo_rotation_messages(promo_id: int, active_rotations: list[dict]) -> None:
    """Deletes promotion messages from all rotated channels when a promotion is stopped."""
    import bot as bot_module
    from config import BOT_TOKEN

    for rot in active_rotations:
        chat_arg = int(rot["channel_id"]) if str(rot["channel_id"]).lstrip("-").isdigit() else rot["channel_id"]
        msg_id = rot["message_id"]

        # Attempt deletion via bot_module.bot if initialized
        deleted = False
        if bot_module.bot is not None:
            try:
                await bot_module.bot.delete_message(chat_id=chat_arg, message_id=msg_id)
                deleted = True
            except Exception:
                pass

        # Fallback to direct HTTP API if bot object was not initialized
        if not deleted and BOT_TOKEN and BOT_TOKEN != "PASTE_BOT_TOKEN_HERE":
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": chat_arg, "message_id": msg_id}
                    )
            except Exception:
                pass


async def verify_channel_for_connect(channel_username: str, channel_id: str, user_telegram_id: Optional[int]) -> dict:
    import os
    from config import BOT_TOKEN

    if os.getenv("TESTING") == "1" or not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        return {"ok": True, "title": None, "username": channel_username, "id": channel_id}

    if not user_telegram_id:
        raise HTTPException(
            status_code=400,
            detail="Aapka Telegram account connected nahi hai. Kripya Telegram account se login karein."
        )

    chat_ref = f"@{channel_username}" if channel_username else channel_id
    if not chat_ref or (not chat_ref.startswith("@") and not chat_ref.startswith("-") and not chat_ref.isdigit()):
        chat_ref = f"@{chat_ref}" if chat_ref else ""

    if not chat_ref:
        raise HTTPException(status_code=400, detail="Invalid Telegram channel username or ID.")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # 1. Get Bot Info to get bot's Telegram ID
            bot_res = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
            bot_data = bot_res.json()
            if not bot_data.get("ok"):
                raise HTTPException(status_code=500, detail="Telegram Bot API configuration error.")
            bot_id = bot_data["result"]["id"]

            # 2. Get Chat Info
            chat_res = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChat", params={"chat_id": chat_ref})
            chat_data = chat_res.json()
            if not chat_data.get("ok"):
                err_desc = chat_data.get("description", "")
                raise HTTPException(
                    status_code=400,
                    detail=f"Channel nahi mila. Kripya sahi channel link ya @username enter karein. ({err_desc})"
                )
            chat = chat_data["result"]
            real_chat_id = str(chat["id"])
            real_title = chat.get("title") or chat.get("first_name") or chat_ref
            real_username = chat.get("username") or channel_username

            # 3. Verify Bot is Admin in Channel
            bot_member_res = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={"chat_id": real_chat_id, "user_id": bot_id}
            )
            bot_member_data = bot_member_res.json()
            bot_status = bot_member_data.get("result", {}).get("status") if bot_member_data.get("ok") else None
            if bot_status not in ("administrator", "creator"):
                raise HTTPException(
                    status_code=400,
                    detail="Bot is channel me admin nahi hai. Kripya pehle bot ko channel me admin banayein."
                )

            # 4. Verify User is Owner/Admin in Channel
            user_member_res = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={"chat_id": real_chat_id, "user_id": user_telegram_id}
            )
            user_member_data = user_member_res.json()
            user_status = user_member_data.get("result", {}).get("status") if user_member_data.get("ok") else None
            if user_status not in ("creator", "administrator"):
                raise HTTPException(
                    status_code=400,
                    detail="Aap sirf wahi channel add kar sakte hai jo aap own karte hai (creator/admin)."
                )

            # 5. Verify Member Count >= 100
            count_res = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMemberCount",
                params={"chat_id": real_chat_id}
            )
            count_data = count_res.json()
            members_count = count_data.get("result", 0) if count_data.get("ok") else 0
            if members_count < 100:
                raise HTTPException(
                    status_code=400,
                    detail=f"Minimum member 100 required. Aapke channel me sirf {members_count} members hain."
                )

            return {
                "ok": True,
                "title": real_title,
                "username": real_username,
                "id": real_chat_id,
                "members_count": members_count
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to verify Telegram channel: {str(exc)}")


@app.on_event("startup")
async def startup() -> None:
    init_db()


# Static & Root Page
@app.get("/", response_class=HTMLResponse)
@app.get("/raj", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
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
        "appName": "Earn Program",
        "logoUrl": "https://i.ibb.co/4nLM2yrb/uploaded-image.jpg",
        "coinsPerAd": float(settings.get("coins_per_ad", 0.80)),
        "dailyAdLimit": int(settings.get("daily_ad_limit", 50)),
        "minWithdrawalUsd": float(settings.get("min_withdrawal_usd", 5.0)),
        "minWithdrawalInr": float(settings.get("min_withdrawal_inr", 400.0)),
        "coinsPerUsd": float(settings.get("coins_per_usd", 100.0)),
        "referralCommissionPct": float(settings.get("referral_commission_pct", 15.0)),
        "paymentUpiId": settings.get("payment_upi_id", "earnprogram@upi"),
        "paymentQrUrl": settings.get("payment_qr_url", "/static/payment_qr.jpg"),
        "globalDiscountPct": float(settings.get("global_discount_pct", 0.0)),
        "maintenance": {
            "full": settings.get("maintenance_full", "0") == "1",
            "watch": settings.get("maintenance_watch", "0") == "1",
            "promote": settings.get("maintenance_promote", "0") == "1",
            "earnings": settings.get("maintenance_earnings", "0") == "1",
            "withdraw": settings.get("maintenance_withdraw", "0") == "1",
        },
        "adCodes": ad_codes,
        "supportTelegram": "EarnProgramSupport",
        "supportUrl": "https://t.me/EarnProgramSupport",
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
    custom_rate = get_custom_user_rate(user["id"])

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
            "hasAcceptedAgreement": bool(user.get("has_accepted_agreement", 0)),
            "referralCode": user["referral_code"],
            "customMemberRate": custom_rate,
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
    check_maintenance("watch")
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
    check_maintenance("watch")
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
    check_maintenance("withdraw")
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
    raw_input = (body.channel_link or body.channelUrl or "").strip()
    if not raw_input:
        raise HTTPException(status_code=400, detail="Please enter a Telegram channel link, @username, or invite code.")

    clean_url, channel_id, channel_username = normalize_telegram_channel(raw_input)
    if not clean_url:
        raise HTTPException(status_code=400, detail="Could not parse Telegram channel link. Please enter a valid @username or t.me link.")

    live_info = await async_fetch_tg_channel_info(channel_username, channel_id)

    connected = None
    with db.db() as conn:
        row = conn.execute("SELECT * FROM connected_channels WHERE channel_id=? OR channel_username=?", (channel_id, channel_username)).fetchone()
        if row:
            connected = dict(row)

    if connected:
        title = connected["channel_title"]
        members_str = f"{connected['total_joins']} Connected Joins"
    elif live_info:
        title = live_info["title"]
        members_str = live_info["members_count"]
    else:
        title = f"Telegram Channel ({channel_username or channel_id})"
        members_str = "Verified Active Telegram Channel"

    return {
        "ok": True,
        "channel_link": clean_url,
        "channel_id": channel_id,
        "title": title,
        "username": f"@{channel_username}" if channel_username else channel_id,
        "members_count": members_str,
        "bot_is_admin": True,
    }


@app.post("/api/promotions/submit")
async def submit_promotion(body: SubmitPromotionRequest, user: dict = Depends(get_current_user)) -> dict:
    check_maintenance("promote")
    raw_input = body.channel_link or body.channelUrl or "https://t.me/channel"
    clean_url, channel_id, channel_username = normalize_telegram_channel(raw_input)

    url = clean_url or raw_input
    c_id = body.channel_id or body.channelId or channel_id or url
    c_title = body.channel_title or body.channelTitle or f"Channel ({c_id})"
    base_price = body.budget_inr or body.priceInr or 100.0

    # Custom member rate for specific user if set
    custom_rate = get_custom_user_rate(user["id"])
    members_per_inr = custom_rate if (custom_rate is not None and custom_rate > 0) else 2.0

    settings = get_settings()
    global_discount_pct = float(settings.get("global_discount_pct", "0.0"))

    cat = (body.category or "General").strip()
    is_cat_specific = bool(body.is_category_specific or body.isCategorySpecific)

    total_price = base_price * 1.20 if is_cat_specific else base_price

    if global_discount_pct > 0:
        total_price = total_price * (1.0 - (global_discount_pct / 100.0))

    # Apply Coupon Discount if coupon code provided
    coupon_applied = None
    input_coupon = (body.coupon_code or body.couponCode or "").strip().upper()
    if input_coupon:
        c_res = apply_coupon_discount(input_coupon, total_price)
        if not c_res["valid"]:
            raise HTTPException(status_code=400, detail=c_res["message"])
        coupon_applied = input_coupon
        total_price = max(0.0, total_price - c_res["discount_inr"])

    total_price = round(total_price, 2)
    required_paise = int(round(total_price * 100))

    calculated_joins = int(round(base_price * members_per_inr))

    # 30% community cap check
    stats = get_global_stats()
    total_community = stats.get("total_community_members", 0) or 1000
    community_cap = int(total_community * 0.30)
    if community_cap < 50:
        community_cap = 50

    joins = body.target_joins or body.targetJoins or calculated_joins
    if joins > community_cap:
        joins = community_cap

    img_url = body.image_url or body.imageUrl or ""
    l_type = body.link_type or body.linkType or "direct"

    tg_id = user.get("telegram_id") or user["id"]
    wallet_res = deduct_wallet_balance_paise(
        telegram_id=tg_id,
        amount_paise=required_paise,
        description=f"Promotion Payment: {c_title} ({cat})"
    )

    if not wallet_res.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=wallet_res.get("error", "Insufficient wallet balance. Please add money to your wallet.")
        )

    txn_ref = body.payment_txn_ref or body.transactionRef or wallet_res.get("order_id") or "WALLET_DEBIT"

    if coupon_applied:
        record_coupon_usage(coupon_applied)

    promo = create_promotion(
        user_id=user["id"],
        channel_url=url,
        channel_id=c_id,
        channel_title=c_title,
        price_inr=base_price,
        target_joins=joins,
        transaction_ref=txn_ref,
        category=cat,
        is_category_specific=is_cat_specific,
        total_price_inr=total_price,
        ad_message=body.ad_message or "",
        status="pending_approval",
        image_url=img_url,
        link_type=l_type
    )

    return {
        "ok": True,
        "message": f"Promotion submitted! ₹{total_price:.2f} deducted from wallet. Promotion will start after Admin approval.",
        "promotion": promo,
        "wallet_balance_inr": wallet_res["balance_paise"] / 100.0,
        "coupon_applied": coupon_applied,
    }


@app.post("/api/promotions/apply-coupon")
async def check_promo_coupon(body: ApplyCouponRequest, user: dict = Depends(get_current_user)) -> dict:
    res = apply_coupon_discount(body.coupon_code, body.budget_inr)
    if not res["valid"]:
        raise HTTPException(status_code=400, detail=res["message"])
    final_amount = max(0.0, round(body.budget_inr - res["discount_inr"], 2))
    return {
        "ok": True,
        "valid": True,
        "coupon_code": res["coupon_code"],
        "discount_inr": res["discount_inr"],
        "final_amount_inr": final_amount,
        "message": res["message"],
    }


@app.get("/api/promotions/my")
@app.get("/api/user/promotions")
async def user_promotions(user: dict = Depends(get_current_user)) -> dict:
    promos = get_user_promotions(user["id"])
    return {"ok": True, "promotions": promos}


@app.post("/api/promotions/stop")
async def user_stop_promotion(body: StopPromotionRequest, user: dict = Depends(get_current_user)) -> dict:
    p_id = body.promotion_id or body.promotionId
    if not p_id:
        raise HTTPException(status_code=400, detail="promotion_id is required.")

    res = stop_promotion(p_id, user_id=user["id"])
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to stop promotion."))

    # Clean up Telegram channel posts
    active_rotations = res.get("active_rotations") or []
    if active_rotations:
        asyncio.create_task(delete_promo_rotation_messages(p_id, active_rotations))

    refund_val = res.get("refund_inr", 0.0)
    spent_val = res.get("spent_inr", 0.0)
    msg = f"Promotion #{p_id} stopped successfully. Spent: ₹{spent_val:.2f}, Refunded: ₹{refund_val:.2f} credited to your wallet."

    return {
        "ok": True,
        "message": msg,
        "promotion_id": p_id,
        "new_status": "stopped",
        "refund_inr": refund_val,
        "spent_inr": spent_val
    }


@app.post("/api/channels/connect")
async def user_connect_channel(body: ConnectChannelRequest, user: dict = Depends(get_current_user)) -> dict:
    check_maintenance("earnings")
    raw_input = body.channel_link or body.channel_id or body.channelId or "https://t.me/channel"
    clean_url, channel_id, channel_username = normalize_telegram_channel(raw_input)

    url = clean_url or raw_input
    c_id = body.channel_id or body.channelId or channel_id or url
    c_username = body.channel_username or body.channelUsername or channel_username or url.split("t.me/")[-1]
    category = body.category or "General"

    # Perform channel verification (ownership, bot admin status, min 100 members)
    user_tg_id = user.get("telegram_id")
    verified = await verify_channel_for_connect(c_username, c_id, user_tg_id)

    c_id = verified.get("id") or c_id
    c_title = verified.get("title") or body.channel_title or body.channelTitle or f"Channel ({c_id})"
    c_username = verified.get("username") or c_username
    m_count = int(verified.get("members_count") or 0)

    ch = connect_channel(
        owner_user_id=user["id"],
        channel_id=c_id,
        channel_title=c_title,
        channel_username=c_username,
        category=category,
        member_count=m_count,
        status="pending_approval"
    )
    return {
        "ok": True,
        "message": f"Channel '{c_title}' ({category}) submitted! It will be activated after Admin approval.",
        "channel": ch,
    }


@app.get("/api/channels/my")
async def my_connected_channels(user: dict = Depends(get_current_user)) -> dict:
    channels = get_user_connected_channels(user["id"])
    return {"ok": True, "channels": channels}


@app.post("/api/channels/delete")
async def user_delete_channel(body: DeleteChannelRequest, user: dict = Depends(get_current_user)) -> dict:
    c_id = body.channel_id or body.channelId
    if not c_id:
        raise HTTPException(status_code=400, detail="channel_id is required.")
    res = delete_connected_channel(user["id"], c_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to delete channel."))
    return res


@app.post("/api/admin/channels/status")
@app.post("/api/admin/channel/status")
async def admin_update_channel_status(body: AdminChannelStatusRequest, _: bool = Depends(require_admin)) -> dict:
    c_id = body.channel_id or body.channelId
    if not c_id:
        raise HTTPException(status_code=400, detail="channel_id is required.")

    status = body.status
    if status in ("unban", "approve"):
        status = "approved"
    elif status == "ban":
        status = "banned"

    res = update_connected_channel_status(c_id, status)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update channel status."))
    return res


@app.post("/api/admin/channels/delete")
async def admin_delete_channel(body: DeleteChannelRequest, _: bool = Depends(require_admin)) -> dict:
    c_id = body.channel_id or body.channelId
    if not c_id:
        raise HTTPException(status_code=400, detail="channel_id is required.")
    res = admin_delete_connected_channel(c_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to delete channel."))
    return res


# YOUPI PG & Wallet Endpoints
YOUPI_WEBHOOK_SECRET = "69447d0ed8ea359cba870ab6b792e716813dc2006522154b45268577f851c7c0"


@app.get("/api/wallet/balance")
async def wallet_balance(user: dict = Depends(get_current_user)) -> dict:
    tg_id = user.get("telegram_id") or user["id"]
    paise = get_wallet_balance(tg_id)
    return {
        "ok": True,
        "telegram_id": tg_id,
        "balance_paise": paise,
        "balance_inr": paise / 100.0,
    }


@app.get("/api/wallet/transactions")
async def wallet_transactions(user: dict = Depends(get_current_user)) -> dict:
    tg_id = user.get("telegram_id") or user["id"]
    txns = get_wallet_transactions(tg_id)
    return {
        "ok": True,
        "transactions": txns,
    }


@app.post("/api/wallet/confirm-payment")
async def confirm_wallet_payment(body: ConfirmPaymentRequest, user: dict = Depends(get_current_user)) -> dict:
    tg_id = user.get("telegram_id") or user["id"]
    amount_paise = int(round(body.amount_inr * 100))
    desc = f"YOUPI PG Wallet Topup (UTR: {body.utr or 'PAYMENT_GATEWAY'})"

    res = credit_wallet_deposit(
        telegram_id=int(tg_id),
        amount_paise=amount_paise,
        utr=body.utr,
        order_id=body.order_id,
        description=desc
    )
    return {
        "ok": True,
        "already_processed": res.get("already_processed", False),
        "balance_paise": res["balance_paise"],
        "balance_inr": res["balance_paise"] / 100.0,
    }


@app.get("/api/wallet/invoice/{txn_id}")
async def wallet_invoice(txn_id: str, user: dict = Depends(get_current_user)) -> dict:
    tg_id = user.get("telegram_id") or user["id"]
    txns = get_wallet_transactions(tg_id, limit=200)

    target_txn = None
    for t in txns:
        if str(t["id"]) == str(txn_id) or str(t["order_id"]) == str(txn_id) or str(t["utr"]) == str(txn_id):
            target_txn = t
            break

    if not target_txn:
        raise HTTPException(status_code=404, detail="Wallet transaction not found.")

    total_paise = target_txn["amount_paise"]
    total_inr = round(total_paise / 100.0, 2)
    base_inr = round(total_inr / 1.03, 2)
    gst_inr = round(total_inr - base_inr, 2)

    return {
        "ok": True,
        "invoice": {
            "invoice_number": f"INV-{target_txn['id']}-{target_txn['created_at'][:10].replace('-', '')}",
            "date": target_txn["created_at"],
            "user_id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "transaction_id": target_txn["order_id"] or target_txn["id"],
            "utr": target_txn["utr"] or "WALLET_TOPUP",
            "description": target_txn["description"] or "Wallet Money Addition",
            "base_amount_inr": base_inr,
            "gst_rate_pct": 3.0,
            "gst_amount_inr": gst_inr,
            "total_amount_inr": total_inr,
        }
    }


@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)) -> dict:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")

    uploads_dir = BASE_DIR / "static" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file.filename or "image.png").suffix.lower()
    if not file_ext or file_ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        file_ext = ".png"

    filename = f"promo_{user['id']}_{uuid.uuid4().hex[:10]}{file_ext}"
    filepath = uploads_dir / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    return {"ok": True, "image_url": f"/static/uploads/{filename}"}


@app.get("/api/tasks")
async def list_user_tasks(user: dict = Depends(get_current_user)) -> dict:
    tasks = get_user_tasks(user["id"])
    return {"ok": True, "tasks": tasks}


@app.post("/api/tasks/complete")
async def complete_user_task(body: CompleteTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    import os
    from config import BOT_TOKEN

    # Retrieve task
    task = get_task_by_id(body.taskId)
    if not task or not task.get("is_active"):
        raise HTTPException(status_code=400, detail="Task not found or inactive.")

    # Check if target is a Telegram channel / group
    target_link = (task.get("link_url") or "").strip()
    is_tg = "t.me/" in target_link or "telegram.me/" in target_link or target_link.startswith("@")

    if is_tg and os.getenv("TESTING") != "1" and BOT_TOKEN and BOT_TOKEN != "PASTE_BOT_TOKEN_HERE":
        user_tg_id = user.get("telegram_id")
        if not user_tg_id:
            raise HTTPException(
                status_code=400,
                detail="Aapka Telegram account connected nahi hai. Channel task verify karne ke liye Telegram se login karein."
            )

        # Parse chat reference
        chat_ref = target_link
        if "t.me/" in chat_ref:
            raw_path = chat_ref.split("t.me/")[-1].split("?")[0].strip()
            if raw_path.startswith("+") or "joinchat" in raw_path:
                chat_ref = raw_path
            else:
                chat_ref = f"@{raw_path}"
        elif not chat_ref.startswith("@") and not chat_ref.startswith("-"):
            chat_ref = f"@{chat_ref}"

        # If it's a public channel or known username, check getChatMember
        if not chat_ref.startswith("+") and "joinchat" not in chat_ref:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    chat_res = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember", params={
                        "chat_id": chat_ref,
                        "user_id": user_tg_id
                    })
                    chat_data = chat_res.json()
                    if chat_data.get("ok"):
                        status = chat_data.get("result", {}).get("status", "")
                        is_member = status in ("creator", "administrator", "member") or (status == "restricted" and bool(chat_data.get("result", {}).get("is_member", False)))
                        if not is_member:
                            raise HTTPException(status_code=400, detail="Aapne abhi tak yeh channel/group join nahi kiya hai. Pehle join karein fir claim karein.")
            except HTTPException:
                raise
            except Exception:
                pass

    res = complete_task(user["id"], body.taskId)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to complete task."))
    return res


@app.post("/api/user/accept-agreement")
async def accept_user_agreement(user: dict = Depends(get_current_user)) -> dict:
    set_user_agreement_status(user["id"], True)
    return {"ok": True, "message": "Agreement accepted successfully."}


@app.post("/api/youpi-webhook")
async def youpi_webhook(request: Request) -> dict:
    raw_body = await request.body()
    headers = request.headers

    # Signature & Timestamp Headers
    timestamp_str = headers.get("X-Gateway-Timestamp") or headers.get("x-gateway-timestamp") or ""
    signature_header = (
        headers.get("X-Gateway-Signature")
        or headers.get("X-Signature")
        or headers.get("X-Youpi-Signature")
        or headers.get("x-gateway-signature")
        or headers.get("x-signature")
        or ""
    )

    if not timestamp_str or not signature_header:
        raise HTTPException(status_code=400, detail="Missing signature or timestamp header.")

    # Timestamp Drift Check (< 2 hours = 7200 seconds)
    try:
        ts = float(timestamp_str)
        if ts > 1e11:  # milliseconds
            ts = ts / 1000.0
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format.")

    current_ts = time.time()
    if abs(current_ts - ts) > 7200:
        raise HTTPException(status_code=400, detail="Timestamp drift exceeds 2 hours.")

    # HMAC-SHA256 Verification: HMAC-SHA256 over (rawBody + "." + X-Gateway-Timestamp) with secret
    payload_to_sign = raw_body.decode("utf-8", errors="replace") + "." + timestamp_str
    expected_sig = hmac.new(
        YOUPI_WEBHOOK_SECRET.encode("utf-8"),
        payload_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig.lower(), signature_header.lower()):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    # Parse body after signature verification
    try:
        data = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    payload = data.get("data") if isinstance(data.get("data"), dict) else data

    event = data.get("event") or payload.get("event") or payload.get("status")
    method = payload.get("method") or payload.get("payment_method") or data.get("method") or "UPI"

    status_str = str(event or "").upper()
    if status_str in ("SUCCESS", "PAID", "COMPLETED"):
        amount_raw = payload.get("amount") or data.get("amount") or 0
        try:
            amount_float = float(amount_raw)
        except (ValueError, TypeError):
            amount_float = 0.0

        # Amount conversion to paise (if in rupees)
        if "amount_paise" in payload:
            amount_paise = int(payload["amount_paise"])
        else:
            amount_paise = int(round(amount_float * 100))

        utr = payload.get("utr") or payload.get("rrn") or data.get("utr") or ""
        order_id = payload.get("order_id") or data.get("order_id") or ""
        metadata = payload.get("metadata") or data.get("metadata") or {}

        tg_user_id = metadata.get("telegram_user_id")
        if not tg_user_id and order_id and order_id.startswith("WALLET-"):
            parts = order_id.split("-")
            if len(parts) >= 2 and parts[1].isdigit():
                tg_user_id = int(parts[1])

        if tg_user_id and amount_paise > 0:
            cust = payload.get("customer") or {}
            cust_name = cust.get("name") or ""
            cust_phone = cust.get("phone") or ""
            desc = f"YOUPI PG Wallet Topup (UTR: {utr})"
            if cust_name or cust_phone:
                desc += f" - {cust_name} ({cust_phone})"

            credit_wallet_deposit(
                telegram_id=int(tg_user_id),
                amount_paise=amount_paise,
                utr=str(utr),
                order_id=str(order_id) if order_id else None,
                description=desc
            )

    return {"received": True}


# Admin Endpoints (/raj)
@app.post("/api/admin/login")
async def admin_login(body: AdminLoginRequest) -> dict:
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        admin_token = secrets.token_hex(24)
        save_admin_session(admin_token)
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
        "bot_start_quote": settings.get("bot_start_quote", "welcome to earn program! watch simple ads, earn coins, promote telegram channels & generate daily income easily."),
        "bot_tutorial_url": settings.get("bot_tutorial_url", "https://t.me/EarnProgramSupport"),
        "bot_help_url": settings.get("bot_help_url", "https://t.me/EarnProgramSupport"),
        "force_sub_enabled": settings.get("force_sub_enabled", "0"),
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
    if body.bot_start_quote is not None: new_settings["bot_start_quote"] = body.bot_start_quote
    if body.bot_tutorial_url is not None: new_settings["bot_tutorial_url"] = body.bot_tutorial_url
    if body.bot_help_url is not None: new_settings["bot_help_url"] = body.bot_help_url
    if body.force_sub_enabled is not None: new_settings["force_sub_enabled"] = body.force_sub_enabled
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


@app.post("/api/admin/user/add-funds")
async def admin_add_funds(body: AdminAddFundsRequest, _: bool = Depends(require_admin)) -> dict:
    res = admin_credit_user_wallet(body.userId, body.amountInr, body.description or "Admin Funds Addition")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to add funds."))
    return res


@app.post("/api/admin/user/remove-funds")
@app.post("/api/admin/user/deduct-funds")
async def admin_remove_funds(body: AdminRemoveFundsRequest, _: bool = Depends(require_admin)) -> dict:
    res = admin_deduct_user_wallet(body.userId, body.amountInr, body.description or "Admin Funds Deduction")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to deduct funds."))
    return res


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
@app.post("/api/admin/promotions/stop")
@app.post("/api/admin/promotions/resume")
@app.post("/api/admin/promotion/status")
async def admin_update_promotion_status(body: AdminPromotionStatusRequest, request: Request, _: bool = Depends(require_admin)) -> dict:
    p_id = body.promotionId or body.promotion_id
    if not p_id:
        raise HTTPException(status_code=400, detail="promotionId is required")

    status = body.status
    if "approve" in request.url.path or "resume" in request.url.path:
        status = "active"
    elif "stop" in request.url.path:
        status = "stopped"
    elif "reject" in request.url.path:
        status = "rejected"

    rej_reason = body.rejection_reason or body.rejectionReason or body.reason or ""

    if status == "stopped":
        res = stop_promotion(p_id)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("error", "Failed to stop promotion."))
        active_rotations = res.get("active_rotations") or []
        if active_rotations:
            asyncio.create_task(delete_promo_rotation_messages(p_id, active_rotations))
        refund_val = res.get("refund_inr", 0.0)
        spent_val = res.get("spent_inr", 0.0)
        msg = f"Promotion #{p_id} stopped. Spent: ₹{spent_val:.2f}, Refunded: ₹{refund_val:.2f} to user wallet."
        return {"ok": True, "message": msg, "new_status": "stopped", "refund_inr": refund_val, "spent_inr": spent_val}

    res = update_promotion_status(p_id, status, rejection_reason=rej_reason)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update promotion status."))

    msg = f"Promotion #{p_id} status updated to {status}"
    if status == "rejected":
        msg += f". Promotion rejected and funds refunded to user's wallet."

    return {"ok": True, "message": msg, "new_status": res.get("new_status", status)}


@app.get("/api/admin/tasks")
async def admin_get_tasks(_: bool = Depends(require_admin)) -> dict:
    tasks = get_all_tasks()
    return {"ok": True, "tasks": tasks}


@app.post("/api/admin/tasks/create")
async def admin_create_task(body: CreateTaskRequest, _: bool = Depends(require_admin)) -> dict:
    task = create_task(body.title, body.description or "", body.link_url, body.reward_inr)
    return {"ok": True, "task": task}


@app.post("/api/admin/tasks/toggle")
async def admin_toggle_task(body: AdminTaskActionRequest, _: bool = Depends(require_admin)) -> dict:
    res = toggle_task_active(body.taskId)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Task not found."))
    return res


@app.post("/api/admin/tasks/delete")
async def admin_delete_task(body: AdminTaskActionRequest, _: bool = Depends(require_admin)) -> dict:
    res = delete_task(body.taskId)
    return res


# Admin Coupon Endpoints
class AdminCouponActionRequest(BaseModel):
    couponId: int


@app.get("/api/admin/coupons")
async def admin_get_coupons(_: bool = Depends(require_admin)) -> dict:
    coupons = get_all_coupons()
    return {"ok": True, "coupons": coupons}


@app.post("/api/admin/coupons/create")
async def admin_create_coupon(body: CreateCouponRequest, _: bool = Depends(require_admin)) -> dict:
    try:
        coupon = create_coupon(
            code=body.code,
            discount_pct=body.discount_pct or 0.0,
            discount_amount_inr=body.discount_amount_inr or 0.0,
            min_budget_inr=body.min_budget_inr or 0.0,
            max_uses=body.max_uses or 0,
        )
        return {"ok": True, "message": f"Coupon '{coupon['code']}' created successfully!", "coupon": coupon}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/coupons/toggle")
async def admin_toggle_coupon(body: AdminCouponActionRequest, _: bool = Depends(require_admin)) -> dict:
    res = toggle_coupon_status(body.couponId)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Coupon not found."))
    return res


@app.post("/api/admin/coupons/delete")
async def admin_delete_coupon(body: AdminCouponActionRequest, _: bool = Depends(require_admin)) -> dict:
    res = delete_coupon(body.couponId)
    return res


@app.post("/api/admin/user/custom-rate")
async def admin_set_custom_rate(body: AdminCustomRateRequest, _: bool = Depends(require_admin)) -> dict:
    set_custom_user_rate(body.userId, body.membersPerInr)
    return {"ok": True, "message": f"Custom member rate updated to {body.membersPerInr} members/INR for User #{body.userId}."}


@app.post("/api/admin/settings/discount")
async def admin_set_discount(body: AdminDiscountRequest, _: bool = Depends(require_admin)) -> dict:
    update_settings({"global_discount_pct": str(body.discountPct)})
    return {"ok": True, "message": f"Global discount updated to {body.discountPct}% for all users."}


@app.post("/api/admin/maintenance")
async def admin_set_maintenance(body: AdminMaintenanceRequest, _: bool = Depends(require_admin)) -> dict:
    new_settings = {}
    if body.maintenanceFull is not None: new_settings["maintenance_full"] = body.maintenanceFull
    if body.maintenanceWatch is not None: new_settings["maintenance_watch"] = body.maintenanceWatch
    if body.maintenancePromote is not None: new_settings["maintenance_promote"] = body.maintenancePromote
    if body.maintenanceEarnings is not None: new_settings["maintenance_earnings"] = body.maintenanceEarnings
    if body.maintenanceWithdraw is not None: new_settings["maintenance_withdraw"] = body.maintenanceWithdraw
    update_settings(new_settings)
    return {"ok": True, "message": "Maintenance settings updated successfully!"}


@app.post("/api/admin/broadcast-staggered")
async def admin_staggered_broadcast(body: AdminStaggeredBroadcastRequest, _: bool = Depends(require_admin)) -> dict:
    import asyncio
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import bot as bot_module

    channels = get_all_connected_channels()
    approved_channels = [c for c in channels if c.get("status") in ("approved", "active")]

    if not approved_channels:
        return {"ok": False, "error": "No approved connected channels found for broadcast."}

    batch_size = max(1, body.batch_size or 20)
    duration_min = max(1, body.duration_minutes or 60)

    # Build inline keyboard if button details provided
    reply_markup = None
    btn_text = (body.button_name or "").strip()
    btn_link = (body.button_link or "").strip()
    if btn_text and btn_link:
        if not (btn_link.startswith("http://") or btn_link.startswith("https://") or btn_link.startswith("tg://")):
            btn_link = f"https://{btn_link}"
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=btn_link)]]
        )

    async def staggered_worker():
        if bot_module.bot is None:
            return
        for i in range(0, len(approved_channels), batch_size):
            batch = approved_channels[i:i + batch_size]
            posted_messages = []

            for ch in batch:
                try:
                    chat_arg = int(ch["channel_id"]) if str(ch["channel_id"]).lstrip("-").isdigit() else ch["channel_id"]
                    if body.image_url:
                        msg = await bot_module.bot.send_photo(
                            chat_id=chat_arg,
                            photo=body.image_url,
                            caption=body.message_text,
                            reply_markup=reply_markup
                        )
                    else:
                        msg = await bot_module.bot.send_message(
                            chat_id=chat_arg,
                            text=body.message_text,
                            reply_markup=reply_markup
                        )
                    posted_messages.append((chat_arg, msg.message_id))
                except Exception:
                    pass

            # Wait set duration
            await asyncio.sleep(duration_min * 60)

            # Auto-delete from batch
            for chat_arg, msg_id in posted_messages:
                try:
                    await bot_module.bot.delete_message(chat_id=chat_arg, message_id=msg_id)
                except Exception:
                    pass

    asyncio.create_task(staggered_worker())

    return {
        "ok": True,
        "message": f"Staggered broadcast started for {len(approved_channels)} channels! (Batch size: {batch_size}, Duration: {duration_min} min per batch)",
        "total_channels": len(approved_channels)
    }


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
    return {"ok": True, "message": "Earn Program system settings updated successfully!"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web:app", host=HOST, port=PORT, reload=False)
