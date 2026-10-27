from __future__ import annotations
import asyncio
import secrets
import time
from html import escape
from urllib.parse import urlencode

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import BOT_TOKEN, ADMIN_USER_ID, WEBAPP_URL, validate_common
from datetime import datetime, timezone, timedelta
from db import (
    init_db, get_setting, set_setting, create_campaign, get_campaign,
    has_rewarded_intent, get_unlock, save_unlock, get_user_by_id,
    record_channel_join_event, record_channel_left_event, get_user_promotions,
    get_user_connected_channels, get_all_connected_channels,
    get_active_promotions_for_rotation, get_connected_channels_by_category,
    get_rotated_channel_ids_for_promotion, record_promo_rotation,
    get_active_promo_rotations_due_for_deletion, mark_promo_rotation_deleted,
)

router = Router()
bot: Bot | None = None
bot_username = ""


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


def get_webapp_url(token: str = "") -> str:
    params = {}
    if token:
        params["campaign"] = token
    return f"{WEBAPP_URL}/?{urlencode(params)}" if params else WEBAPP_URL


async def member_of_force_channel(user_id: int) -> bool:
    force_enabled = get_setting("force_sub_enabled")
    if force_enabled == "0":
        return True
    force_chat_id = get_setting("force_chat_id")
    if not force_chat_id:
        return True
    assert bot is not None
    try:
        force_chat = int(force_chat_id) if force_chat_id.lstrip("-").isdigit() else force_chat_id
        member = await bot.get_chat_member(chat_id=force_chat, user_id=user_id)
        status = getattr(member, "status", "")
        if status in ("creator", "administrator", "member"):
            return True
        return status == "restricted" and bool(getattr(member, "is_member", False))
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


def to_small_caps(text: str) -> str:
    """Converts standard ASCII characters to Unicode small caps font format."""
    normal = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    small_caps = "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢAʙCᴅEғGʜIᴊKʟMɴOᴘQʀSᴛUᴠWXYᴢ0123456789"
    trans = str.maketrans(normal, small_caps)
    return text.translate(trans)


def main_welcome_keyboard(token: str = "") -> InlineKeyboardMarkup:
    web_url = get_webapp_url(token)
    tut_url = get_setting("bot_tutorial_url") or "https://t.me/Tgfuckerx"
    hlp_url = get_setting("bot_help_url") or "https://t.me/Tgfuckerx"

    # Exactly 3 buttons: Earn Program (Mini App), Tutorial, Help
    buttons = [
        [
            InlineKeyboardButton(text="🚀 ᴇᴀʀɴ ᴘʀᴏɢʀᴀᴍ", web_app=WebAppInfo(url=web_url))
        ],
        [
            InlineKeyboardButton(text="📚 ᴛᴜᴛᴏʀɪᴀʟ", url=tut_url) if tut_url.startswith("http") else InlineKeyboardButton(text="📚 ᴛᴜᴛᴏʀɪᴀʟ", callback_data="btn_tutorial"),
            InlineKeyboardButton(text="💬 ʜᴇʟᴘ", url=hlp_url) if hlp_url.startswith("http") else InlineKeyboardButton(text="💬 ʜᴇʟᴘ", callback_data="btn_support")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def force_join_keyboard(token: str | None = None) -> InlineKeyboardMarkup:
    join_url = get_setting("force_join_url") or "https://t.me/"
    callback_data = f"checkforce:{token}" if token else "checkforce"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Required Channel", url=join_url)],
        [InlineKeyboardButton(text="🔄 I Joined — Check Again", callback_data=callback_data)],
    ])


def campaign_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Watch Ad to Unlock", web_app=WebAppInfo(url=get_webapp_url(token)))],
        [InlineKeyboardButton(text="🔓 Get Channel Link", callback_data=f"getlink:{token}")],
    ])


async def show_campaign_flow(message: Message, token: str, user_id: int) -> None:
    campaign = get_campaign(token)
    if not campaign:
        await message.answer("⚠️ <b>Invalid Link</b>\n\nThis campaign link is invalid or no longer active.")
        return
    if not await member_of_force_channel(user_id):
        await message.answer(
            "🔒 <b>Action Required</b>\n\n"
            "To unlock access, please join our required channel first and then tap <b>I Joined — Check Again</b>.",
            reply_markup=force_join_keyboard(token),
        )
        return

    db_user = get_user_by_id(user_id)
    if db_user and db_user.get("is_premium"):
        await message.answer(
            "⭐ <b>Earn Program Premium Member</b>\n\n"
            f"🎯 Target Channel: <b>{escape(campaign['target_title'])}</b>\n\n"
            "✨ As a Premium user, you can bypass watching ads! Tap below to claim your link.",
            reply_markup=campaign_keyboard(token),
        )
        return

    await message.answer(
        "✨ <b>Earn Program Channel Access Gateway</b>\n\n"
        f"🎯 Target Channel: <b>{escape(campaign['target_title'])}</b>\n\n"
        "Tap <b>Watch Ad to Unlock</b> to complete the ad view. "
        "Once verified, return here and tap <b>Get Channel Link</b> to claim your access invite!",
        reply_markup=campaign_keyboard(token),
    )


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    first_name = escape(message.from_user.first_name) if message.from_user and message.from_user.first_name else "User"

    parts = (message.text or "").split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if token and get_campaign(token):
        await show_campaign_flow(message, token, user_id)
        return

    raw_quote = get_setting("bot_start_quote") or "welcome to earn program! watch simple ads, earn coins, promote telegram channels & generate daily income easily."
    quote_small_caps = to_small_caps(raw_quote)

    greeting = (
        f"👋 <b>Welcome, {first_name}!</b>\n\n"
        f"<blockquote>{escape(quote_small_caps)}</blockquote>"
    )

    logo_path = BASE_DIR / "static" / "logo.png"
    if logo_path.exists():
        from aiogram.types import FSInputFile
        photo = FSInputFile(str(logo_path))
        await message.answer_photo(photo=photo, caption=greeting, reply_markup=main_welcome_keyboard(token))
    else:
        await message.answer(greeting, reply_markup=main_welcome_keyboard(token))


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ <b>Access Denied</b>\n\nYou do not have master administrator privileges.")
        return

    admin_app_url = f"{WEBAPP_URL}/admin"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Launch Master Admin Panel", web_app=WebAppInfo(url=admin_app_url))],
        [InlineKeyboardButton(text="📊 View System Status", callback_data="btn_admin_status")]
    ])
    await message.answer(
        "👑 <b>Master Admin Control Center</b>\n\n"
        "Welcome Master Admin! You can manage user VIP statuses, approve channel promotions, "
        "process cash withdrawals, configure bot settings, and update ad network scripts.",
        reply_markup=kb
    )


@router.message(Command("promote"))
async def promote_cmd_handler(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Launch Promotion Portal", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "💼 <b>Earn Program Channel Promotion Hub</b>\n\n"
        "Promote your Telegram channel or group to thousands of real active users!\n\n"
        "1️⃣ Tap button below to open Promotion Portal\n"
        "2️⃣ Enter your channel link to analyze audience\n"
        "3️⃣ Select budget & pay via UPI/QR Code\n"
        "4️⃣ Automatic broadcast is triggered on approval!",
        reply_markup=kb
    )


@router.message(Command("mychannels"))
async def mychannels_cmd_handler(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Open Channel Earnings Hub", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "📺 <b>My Connected Channels & Earnings</b>\n\n"
        "Connect your Telegram channels to Earn Program network and earn ₹0.50 INR for every 2 user joins automatically tracked!\n\n"
        "Tap below to view your connected channels and analytics.",
        reply_markup=kb
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = (
        "ℹ️ <b>Earn Program Bot Guide</b>\n\n"
        "• /start — Open main menu & Mini App\n"
        "• /promote — Promote your channel paid\n"
        "• /mychannels — View your connected channels & stats\n"
        "• /help — View help guide\n\n"
    )
    if is_admin(user_id):
        text += (
            "👑 <b>Master Admin Commands</b>\n"
            "• <code>/setforce @channel</code> — Require channel subscription\n"
            "• <code>/offforce</code> — Disable force sub\n"
            "• <code>/settarget @channel direct/request</code> — Set target campaign\n"
            "• <code>/genlink</code> — Generate deep campaign link\n"
            "• <code>/broadcast &lt;message&gt;</code> — Broadcast across connected channels\n"
            "• <code>/status</code> — Display system configuration\n\n"
        )
    await message.answer(text)


async def resolve_channel(chat_ref: str):
    assert bot is not None
    chat_arg = int(chat_ref) if chat_ref.lstrip("-").isdigit() else chat_ref
    chat = await bot.get_chat(chat_arg)
    if chat.type not in ("channel", "supergroup", "group"):
        raise ValueError("Please provide a Telegram channel or group ID/username.")
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id=chat.id, user_id=me.id)
    if getattr(member, "status", "") not in ("creator", "administrator"):
        raise ValueError("The bot must be an administrator in that channel/group first with full permissions.")
    can_invite = getattr(member, "can_invite_users", None)
    if getattr(member, "status", "") != "creator" and can_invite is False:
        raise ValueError("Give the bot permission to invite users & post messages.")
    return chat


@router.message(Command("setforce"))
async def set_force_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Usage: <code>/setforce @channel_or_chat_id</code>")
        return
    assert bot is not None
    try:
        chat = await resolve_channel(args[1].strip())
        join_url = f"https://t.me/{chat.username}" if chat.username else (await bot.create_chat_invite_link(chat_id=chat.id, name="Force sub")).invite_link
        set_setting("force_chat_id", str(chat.id))
        set_setting("force_chat_title", chat.title or chat.full_name or str(chat.id))
        set_setting("force_join_url", join_url)
        await message.answer(f"✅ Force-subscription set to: <b>{escape(chat.title or str(chat.id))}</b>")
    except Exception as exc:
        await message.answer(f"❌ Error setting force sub: {escape(str(exc))}")


@router.message(Command("offforce"))
async def off_force_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    set_setting("force_chat_id", None)
    set_setting("force_chat_title", None)
    set_setting("force_join_url", None)
    await message.answer("✅ Force-subscription disabled.")


@router.message(Command("settarget"))
async def set_target_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    args = (message.text or "").split()
    if len(args) != 3 or args[2].lower() not in ("direct", "request"):
        await message.answer("Usage:\n<code>/settarget @channel direct</code> or <code>request</code>")
        return
    assert bot is not None
    try:
        chat = await resolve_channel(args[1])
        mode = args[2].lower()
        set_setting("target_chat_id", str(chat.id))
        set_setting("target_chat_title", chat.title or chat.full_name or str(chat.id))
        set_setting("target_invite_mode", mode)
        await message.answer(f"🎯 Target channel configured: <b>{escape(chat.title or str(chat.id))}</b> ({mode})")
    except Exception as exc:
        await message.answer(f"❌ Error configuring target: {escape(str(exc))}")


@router.message(Command("genlink"))
async def generate_link_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    target_chat_id = get_setting("target_chat_id")
    target_title = get_setting("target_chat_title")
    invite_mode = get_setting("target_invite_mode")
    if not target_chat_id or not target_title or not invite_mode:
        await message.answer("⚠️ Configure target first via /settarget @channel direct")
        return
    token = secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")[:12]
    create_campaign(token, target_chat_id, target_title, invite_mode, user_id)
    link = f"https://t.me/{bot_username}?start={token}"
    await message.answer(f"🚀 <b>Campaign Link Generated:</b>\n\n<code>{escape(link)}</code>")


@router.message(Command("broadcast"))
async def broadcast_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: <code>/broadcast Your promotional post HTML content</code>")
        return
    broadcast_msg = parts[1].strip()
    channels = get_all_connected_channels()
    if not channels:
        await message.answer("⚠️ No connected channels found for broadcasting.")
        return

    assert bot is not None
    sent_count = 0
    fail_count = 0
    for ch in channels:
        try:
            chat_id = int(ch["channel_id"]) if ch["channel_id"].lstrip("-").isdigit() else ch["channel_id"]
            await bot.send_message(chat_id=chat_id, text=broadcast_msg)
            sent_count += 1
        except Exception:
            fail_count += 1

    await message.answer(f"📢 <b>Broadcast Complete!</b>\n\n✅ Successfully posted to <b>{sent_count}</b> channels.\n❌ Failed on <b>{fail_count}</b> channels.")


@router.message(Command("status"))
async def status_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("⚠️ Command restricted to master administrator.")
        return
    force = get_setting("force_chat_title") or "Disabled"
    target = get_setting("target_chat_title") or "Not configured"
    mode = get_setting("target_invite_mode") or "Not configured"
    await message.answer(
        "📊 <b>Earn Program System Status</b>\n\n"
        f"📢 Force-sub Channel: <b>{escape(force)}</b>\n"
        f"🎯 Target Channel: <b>{escape(target)}</b>\n"
        f"⚙️ Invite Mode: <b>{escape(mode)}</b>\n"
        f"🌐 Mini App URL: <b>{WEBAPP_URL}</b>"
    )


# Callbacks
@router.callback_query(F.data == "btn_premium")
async def premium_info_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    db_user = get_user_by_id(user_id)
    is_prem = db_user and db_user.get("is_premium")
    if is_prem:
        await callback.answer("⭐ You are an Earn Program Premium Member! Ads are completely bypassed for you.", show_alert=True)
    else:
        await callback.answer("⭐ Earn Program Premium Pass allows instant access to all channel links without watching ads! Contact @Tgfuckerx or upgrade in Mini App.", show_alert=True)


@router.callback_query(F.data == "btn_stats")
async def stats_callback(callback: CallbackQuery) -> None:
    from db import get_global_stats
    stats = get_global_stats()
    text = (
        "📊 <b>Earn Program Live Platform Stats</b>\n\n"
        f"👥 Total Users: <b>{stats.get('total_users', 0):,}</b>\n"
        f"🎬 Ads Watched: <b>{stats.get('total_ads_watched', 0):,}</b>\n"
        f"💰 Total Cash Paid: <b>${stats.get('total_paid_usd', 0.0):.2f} / ₹{stats.get('total_paid_inr', 0.0):.2f}</b>\n"
        f"🎯 Active Promotions: <b>{stats.get('active_promotions', 0):,}</b>\n\n"
        "🚀 <i>Earn coins daily by viewing ads in our Mini App!</i>"
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Launch Web App", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]))


@router.callback_query(F.data == "btn_referral")
async def referral_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = (
        "👥 <b>Earn Program Referral Program</b>\n\n"
        "Earn <b>15% Lifetime Commission</b> on all ad coins earned by users you invite!\n\n"
        f"🔗 <b>Your Unique Referral Link:</b>\n<code>{escape(ref_link)}</code>\n\n"
        "Share this link in Telegram groups, channels, or social media!"
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text)


@router.callback_query(F.data == "btn_support")
async def support_callback(callback: CallbackQuery) -> None:
    text = (
        "💬 <b>Official Earn Program Support</b>\n\n"
        "Need help with payments, promotion campaigns, or VIP Premium Pass?\n"
        "Contact our official support handle: <b>@Tgfuckerx</b>"
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Contact Support (@Tgfuckerx)", url="https://t.me/Tgfuckerx")]
        ]))


@router.callback_query(F.data == "btn_admin_status")
async def admin_status_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    if not is_admin(user_id):
        await callback.answer("⚠️ Access restricted.", show_alert=True)
        return
    force = get_setting("force_chat_title") or "Disabled"
    target = get_setting("target_chat_title") or "Not configured"
    mode = get_setting("target_invite_mode") or "Not configured"
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📊 <b>Master Admin System Status</b>\n\n"
            f"📢 Force-sub Channel: <b>{escape(force)}</b>\n"
            f"🎯 Target Channel: <b>{escape(target)}</b>\n"
            f"⚙️ Invite Mode: <b>{escape(mode)}</b>\n"
            f"🌐 Web App URL: <b>{WEBAPP_URL}</b>"
        )


@router.callback_query(F.data == "btn_tutorial")
async def tutorial_callback(callback: CallbackQuery) -> None:
    text = (
        "📚 <b>How To Earn & Use Earn Program</b>\n\n"
        "1️⃣ <b>Watch Ads to Earn Coins:</b> Open Mini App and watch 20s ads to earn coins daily.\n"
        "2️⃣ <b>Unlock Telegram Channel Links:</b> Open campaign deep links, complete required ad view, and claim invite link.\n"
        "3️⃣ <b>Channel Promotion:</b> Submit your channel link and budget to broadcast across our network.\n"
        "4️⃣ <b>Connect & Earn Revenue:</b> Add bot as admin to your channel and earn ₹0.50 INR per 2 user joins!\n"
        "5️⃣ <b>Instant Withdrawals:</b> Cash out via UPI, Paytm, USDT, or PayPal."
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Start Earn Now", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]))


@router.callback_query(F.data == "btn_promote")
async def promote_callback(callback: CallbackQuery) -> None:
    await callback.answer("💼 Launching Promotion Portal...", show_alert=False)
    if callback.message:
        await callback.message.answer(
            "💼 <b>Earn Program Paid Channel Promotions</b>\n\n"
            "1. Open Earn Program Mini App and go to <b>Channel Promotion</b>.\n"
            "2. Enter your channel link to analyze audience.\n"
            "3. Pay via Admin UPI QR Code.\n"
            "4. Add this bot as Administrator in your channel.\n"
            "5. Once approved, your ad is posted across all partner channels!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Launch Promotion Portal", web_app=WebAppInfo(url=WEBAPP_URL))]
            ])
        )


@router.callback_query(F.data == "btn_getlink")
async def getlink_help_callback(callback: CallbackQuery) -> None:
    await callback.answer("📌 Please open a specific campaign link to unlock access.", show_alert=True)


@router.callback_query(F.data.startswith("checkforce"))
async def check_force_callback(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    if not await member_of_force_channel(callback.from_user.id):
        await callback.answer("❌ Membership not verified. Please join required channel first.", show_alert=True)
        return
    await callback.answer("✅ Membership verified!")
    token = callback.data.split(":", 1)[1] if ":" in (callback.data or "") else ""
    if token and get_campaign(token):
        await show_campaign_flow(callback.message, token, callback.from_user.id)
    else:
        await callback.message.answer("✅ Membership verified. Reopen campaign link to proceed.")


@router.callback_query(F.data.startswith("getlink:"))
async def get_link_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    token = callback.data.split(":", 1)[1]
    campaign = get_campaign(token)
    if not campaign:
        await callback.answer("⚠️ Campaign link expired or invalid.", show_alert=True)
        return
    if not await member_of_force_channel(callback.from_user.id):
        await callback.answer("⚠️ Please join the required channel first.", show_alert=True)
        await callback.message.answer("Please join the required channel to continue.", reply_markup=force_join_keyboard(token))
        return

    db_user = get_user_by_id(callback.from_user.id)
    is_premium_user = db_user and bool(db_user.get("is_premium"))

    existing = get_unlock(callback.from_user.id, token)
    if existing:
        await callback.answer("✨ Your link is ready")
        await callback.message.answer(
            f"🎉 <b>Here is your link for {escape(campaign['target_title'])}:</b>\n\n{escape(existing['invite_link'])}"
        )
        return

    if not is_premium_user and not has_rewarded_intent(callback.from_user.id, token):
        await callback.answer("⚠️ Please watch and complete the rewarded ad first in the Mini App.", show_alert=True)
        return

    assert bot is not None
    try:
        target_chat_id = campaign["target_chat_id"]
        target_chat_arg = int(target_chat_id) if str(target_chat_id).lstrip("-").isdigit() else target_chat_id
        create_kwargs = {
            "chat_id": target_chat_arg,
            "name": f"blaze-{callback.from_user.id}-{token}",
            "expire_date": int(time.time()) + 86_400,
        }
        if campaign["invite_mode"] == "request":
            create_kwargs["creates_join_request"] = True
        else:
            create_kwargs["creates_join_request"] = False
            create_kwargs["member_limit"] = 1
        invite = await bot.create_chat_invite_link(**create_kwargs)
        save_unlock(callback.from_user.id, token, invite.invite_link)
        stored = get_unlock(callback.from_user.id, token)
        link = stored["invite_link"] if stored else invite.invite_link
        request_note = "Join-request link; channel admins will approve your request." if campaign["invite_mode"] == "request" else "Single-use direct invite link."
        premium_badge = "⭐ [Premium Ad-Bypass Active]\n" if is_premium_user else ""
        await callback.answer("🎉 Link unlocked!")
        await callback.message.answer(
            f"🔓 <b>Unlocked: {escape(campaign['target_title'])}</b>\n\n"
            f"{premium_badge}ℹ️ {escape(request_note)}\n\n{escape(link)}"
        )
    except Exception as exc:
        await callback.answer("❌ Failed to create invite link.", show_alert=True)
        await callback.message.answer(
            "⚠️ Telegram error generating invite link. Ensure bot has admin invite privileges in target channel.\n"
            f"Details: <code>{escape(str(exc))}</code>"
        )


# Background 3-Hour 5-Channel Rotation & Deletion Task
async def process_promotion_rotations() -> None:
    """
    Background worker loop that manages 5-channel batch rotations:
    1. Deletes expired posts (3 hours old) from connected channels if subscriber joins target not met.
    2. Broadcasts active promotion to the next batch of 5 connected channels (matching category if category promotion).
    """
    if bot is None:
        return

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds")

    # Part A: Handle expired posts deletion after 3 hours
    due_deletions = get_active_promo_rotations_due_for_deletion(now_iso)
    for rot in due_deletions:
        # If campaign target joins already reached, mark rotation deleted without deleting message (or clean up)
        if rot["current_joins"] >= rot["target_joins"] or rot["promo_status"] != "active":
            mark_promo_rotation_deleted(rot["id"])
            continue

        try:
            chat_arg = int(rot["channel_id"]) if str(rot["channel_id"]).lstrip("-").isdigit() else rot["channel_id"]
            await bot.delete_message(chat_id=chat_arg, message_id=rot["message_id"])
        except Exception:
            pass
        finally:
            mark_promo_rotation_deleted(rot["id"])

    # Part B: Post active promotions to next batch of 5 channels
    active_promos = get_active_promotions_for_rotation()
    for promo in active_promos:
        already_rotated = get_rotated_channel_ids_for_promotion(promo["id"])
        category = promo.get("category") if promo.get("is_category_specific") else "General"
        candidate_channels = get_connected_channels_by_category(category, limit=50)

        # Filter channels not yet posted for this promotion
        fresh_channels = [c for c in candidate_channels if c["channel_id"] not in already_rotated]
        if not fresh_channels:
            # If all candidate channels posted, fall back to any available approved connected channel
            all_channels = [c for c in get_all_connected_channels() if c.get("status") in ("approved", "active")]
            fresh_channels = [c for c in all_channels if c["channel_id"] not in already_rotated]

        if not fresh_channels:
            continue

        # Batch up to 5 channels at a time
        batch = fresh_channels[:5]
        delete_at_dt = now_dt + timedelta(hours=3)
        delete_at_iso = delete_at_dt.isoformat(timespec="seconds")

        # Build inline button pointing to user's promotional channel
        inline_btn = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👉 Join Channel Now", url=promo["channel_url"])]
        ])

        msg_content = (
            f"📢 <b>SPONSORED PROMOTION</b>\n\n"
            f"{escape(promo.get('ad_message') or promo['channel_title'])}\n\n"
            f"👇 <i>Tap below to join directly!</i>"
        )

        for ch in batch:
            try:
                # Per-channel special tracking invite link generation
                tracking_url = promo["channel_url"]
                try:
                    target_chat_arg = int(promo["channel_id"]) if str(promo["channel_id"]).lstrip("-").isdigit() else promo["channel_id"]
                    invite = await bot.create_chat_invite_link(
                        chat_id=target_chat_arg,
                        name=f"promo_{promo['id']}_ch_{ch['id']}",
                        creates_join_request=False
                    )
                    if invite and invite.invite_link:
                        tracking_url = invite.invite_link
                except Exception:
                    pass

                ch_inline_btn = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👉 Join Channel Now", url=tracking_url)]
                ])

                chat_id_arg = int(ch["channel_id"]) if str(ch["channel_id"]).lstrip("-").isdigit() else ch["channel_id"]
                sent_msg = await bot.send_message(chat_id=chat_id_arg, text=msg_content, reply_markup=ch_inline_btn)
                record_promo_rotation(
                    promotion_id=promo["id"],
                    channel_id=ch["channel_id"],
                    message_id=sent_msg.message_id,
                    delete_at=delete_at_iso,
                )
            except Exception:
                pass


async def rotation_background_loop() -> None:
    while True:
        try:
            await process_promotion_rotations()
        except Exception:
            pass
        await asyncio.sleep(60)  # Check every 60 seconds


# Real-time ChatMemberUpdated Handler for Join/Left & Earning Analytics
@router.chat_member()
async def chat_member_update_handler(event: ChatMemberUpdated) -> None:
    # Anti-Bot Protection: Ignore all bot accounts completely!
    if event.new_chat_member and event.new_chat_member.user and event.new_chat_member.user.is_bot:
        return

    chat_id = str(event.chat.id)
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    user_id = event.new_chat_member.user.id

    # Join Event
    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        record_channel_join_event(chat_id, user_id)

    # Left Event
    if old_status in ("member", "administrator", "restricted") and new_status in ("left", "kicked"):
        record_channel_left_event(chat_id, user_id)


async def main() -> None:
    global bot, bot_username
    validate_common()
    if not WEBAPP_URL.startswith("https://"):
        raise RuntimeError("WEBAPP_URL must be the public HTTPS URL of your Mini App")
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    bot_username = me.username or ""
    if not bot_username:
        raise RuntimeError("Could not read bot username. Check BOT_TOKEN.")
    dp = Dispatcher()
    dp.include_router(router)
    print(f"Blazeculture Bot @{bot_username} is polling...")
    # Start 3-hour 5-channel rotation background loop task
    asyncio.create_task(rotation_background_loop())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
