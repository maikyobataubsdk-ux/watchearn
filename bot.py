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
from db import (
    init_db, get_setting, set_setting, create_campaign, get_campaign,
    has_rewarded_intent, get_unlock, save_unlock, get_user_by_id,
    record_channel_join_event, record_channel_left_event, get_user_promotions,
    get_user_connected_channels, get_all_connected_channels,
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


def main_welcome_keyboard(token: str = "") -> InlineKeyboardMarkup:
    web_url = get_webapp_url(token)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Premium Pass", callback_data="btn_premium"),
            InlineKeyboardButton(text="📚 Tutorial", callback_data="btn_tutorial")
        ],
        [
            InlineKeyboardButton(text="🚀 Open Blazeculture App", web_app=WebAppInfo(url=web_url))
        ],
        [
            InlineKeyboardButton(text="💼 Promote Channel", callback_data="btn_promote"),
            InlineKeyboardButton(text="🔓 Channel Link", callback_data=f"getlink:{token}" if token else "btn_getlink")
        ]
    ])


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
            "⭐ <b>Blazeculture Premium Member</b>\n\n"
            f"🎯 Target Channel: <b>{escape(campaign['target_title'])}</b>\n\n"
            "✨ As a Premium user, you can bypass watching ads! Tap below to claim your link.",
            reply_markup=campaign_keyboard(token),
        )
        return

    await message.answer(
        "✨ <b>Blazeculture Channel Access Gateway</b>\n\n"
        f"🎯 Target Channel: <b>{escape(campaign['target_title'])}</b>\n\n"
        "Tap <b>Watch Ad to Unlock</b> to complete the ad view. "
        "Once verified, return here and tap <b>Get Channel Link</b> to claim your access invite!",
        reply_markup=campaign_keyboard(token),
    )


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if token and get_campaign(token):
        await show_campaign_flow(message, token, user_id)
        return

    greeting = (
        "HI THERE... 🛰️ ─ ✦ <b>M R  R A J</b> ✦ ─ 💥\n\n"
        "<b>I AM BLAZECULTURE BOT...!</b>\n"
        "I CAN PROVIDE EXCLUSIVE FILES & CHANNEL PROMOTIONS THROUGH A SPECIFIC LINK...!\n\n"
        "⚡ <i>POWERED BY BLAZECULTURE PLATFORM</i>"
    )
    await message.answer(greeting, reply_markup=main_welcome_keyboard())


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = (
        "ℹ️ <b>Blazeculture Bot Guide</b>\n\n"
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
        "📊 <b>Blazeculture System Status</b>\n\n"
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
        await callback.answer("⭐ You are a Blazeculture Premium Member! Ads are completely bypassed for you.", show_alert=True)
    else:
        await callback.answer("⭐ Blazeculture Premium Pass allows instant access to all channel links without watching ads! Contact @rajpapa or upgrade in Mini App.", show_alert=True)


@router.callback_query(F.data == "btn_tutorial")
async def tutorial_callback(callback: CallbackQuery) -> None:
    await callback.answer("📚 Open Mini App or tap Watch Ad on any campaign link to earn coins & unlock channels!", show_alert=True)


@router.callback_query(F.data == "btn_promote")
async def promote_callback(callback: CallbackQuery) -> None:
    await callback.answer("💼 Launching Promotion Portal in Mini App...", show_alert=False)
    if callback.message:
        await callback.message.answer(
            "💼 <b>Blazeculture Paid Channel Promotions</b>\n\n"
            "1. Open Blazeculture Mini App and go to <b>Channel Promotion</b>.\n"
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


# Real-time ChatMemberUpdated Handler for Join/Left & Earning Analytics
@router.chat_member()
async def chat_member_update_handler(event: ChatMemberUpdated) -> None:
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
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
