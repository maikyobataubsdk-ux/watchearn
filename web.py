from __future__ import annotations
import secrets
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from config import HOST, PORT, validate_common
from db import (
    init_db, create_user, get_user_by_email, get_user_by_username,
    get_user_by_id, get_all_users, set_user_ban_status, adjust_user_balance,
    get_user_daily_ad_count, get_ip_daily_ad_count, start_ad_session,
    claim_ad_reward, create_withdrawal, get_user_withdrawals, get_all_withdrawals,
    update_withdrawal_status, get_top_earners, get_global_stats,
    get_referral_stats, get_settings, update_settings, get_ad_codes, update_ad_codes,
    hash_password,
)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Ad Earn PTC Platform", docs_url=None, redoc_url=None)

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


class WatchAdRequest(BaseModel):
    pass


class ClaimRewardRequest(BaseModel):
    sessionId: str = Field(min_length=10, max_length=64)


class WithdrawRequest(BaseModel):
    amountUsd: float = Field(gt=0)
    method: str = Field(min_length=2, max_length=20)  # UPI, Paytm, USDT, PayPal
    accountDetails: str = Field(min_length=3, max_length=200)


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminBanRequest(BaseModel):
    userId: int
    isBanned: bool


class AdminBalanceRequest(BaseModel):
    userId: int
    newBalance: float


class AdminWithdrawalStatusRequest(BaseModel):
    withdrawalId: int
    status: str  # approved, rejected


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
        "coinsPerAd": float(settings.get("coins_per_ad", 2.0)),
        "dailyAdLimit": int(settings.get("daily_ad_limit", 50)),
        "minWithdrawalUsd": float(settings.get("min_withdrawal_usd", 5.0)),
        "minWithdrawalInr": float(settings.get("min_withdrawal_inr", 400.0)),
        "coinsPerUsd": float(settings.get("coins_per_usd", 100.0)),
        "referralCommissionPct": float(settings.get("referral_commission_pct", 15.0)),
        "adCodes": ad_codes,
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
            "email": user["email"],
            "username": user["username"],
            "balanceCoins": user["balance_coins"],
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
            "email": user["email"],
            "username": user["username"],
            "balanceCoins": user["balance_coins"],
            "totalEarnedCoins": user["total_earned_coins"],
            "totalAdsWatched": user["total_ads_watched"],
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
async def watch_ad(request: Request, user: dict = Depends(get_current_user)) -> dict:
    settings = get_settings()
    daily_limit = int(settings.get("daily_ad_limit", 50))
    coins_per_ad = float(settings.get("coins_per_ad", 2.0))

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
        "sessionId": session_info["session_id"],
        "timerSeconds": session_info["timer_seconds"],
        "rewardCoins": session_info["reward_coins"],
        "adCodes": ad_codes,
    }


@app.post("/api/user/claim-reward")
async def claim_reward(body: ClaimRewardRequest, user: dict = Depends(get_current_user)) -> dict:
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
    pending_w = sum(1 for w in withdrawals if w["status"] == "pending")

    return {
        "ok": True,
        "stats": stats,
        "totalUsersCount": len(users),
        "pendingWithdrawalsCount": pending_w,
    }


@app.get("/api/admin/users")
async def admin_users(_: bool = Depends(require_admin)) -> dict:
    users = get_all_users()
    return {"ok": True, "users": users}


@app.post("/api/admin/user/ban")
async def admin_ban_user(body: AdminBanRequest, _: bool = Depends(require_admin)) -> dict:
    set_user_ban_status(body.userId, body.isBanned)
    return {"ok": True, "message": f"User ID {body.userId} ban status set to {body.isBanned}"}


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
    })
    return {"ok": True, "message": "System settings updated successfully!"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web:app", host=HOST, port=PORT, reload=False)
