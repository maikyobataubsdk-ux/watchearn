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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import BOT_TOKEN, ADMIN_USER_ID, WEBAPP_URL, validate_common
from db import (
    init_db, get_setting, set_setting, create_campaign, get_campaign,
    has_rewarded_intent, get_unlock, save_unlock,
)

router = Router()
bot: Bot | None = None
bot_username = ""


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


def get_webapp_url(token: str) -> str:
    return f"{WEBAPP_URL}/?{urlencode({'campaign': token})}"


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
        # Fail closed: membership could not be verified.
        return False


def force_join_keyboard(token: str | None = None) -> InlineKeyboardMarkup:
    join_url = get_setting("force_join_url") or "https://t.me/"
    callback_data = f"checkforce:{token}" if token else "checkforce"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Required Channel", url=join_url)],
        [InlineKeyboardButton(text="✅ I Joined — Check Again", callback_data=callback_data)],
    ])


def campaign_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Watch Ad to Unlock", web_app=WebAppInfo(url=get_webapp_url(token)))],
        [InlineKeyboardButton(text="🔓 Get Channel Link", callback_data=f"getlink:{token}")],
    ])


async def show_campaign_flow(message: Message, token: str, user_id: int) -> None:
    campaign = get_campaign(token)
    if not campaign:
        await message.answer("This campaign link is invalid or no longer available.")
        return
    if not await member_of_force_channel(user_id):
        await message.answer(
            "To continue, first join the required channel, then tap <b>I Joined — Check Again</b>.",
            reply_markup=force_join_keyboard(token),
        )
        return
    await message.answer(
        "<b>Channel Access</b>\n\n"
        f"Target: <b>{escape(campaign['target_title'])}</b>\n\n"
        "Tap <b>Watch Ad to Unlock</b>. Access is granted only after the approved ad network confirms a qualifying event. "
        "When the Mini App says the reward is confirmed, return here and tap <b>Get Channel Link</b>.",
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
    await message.answer(
        "Welcome! This bot issues controlled invite links for configured channels.\n\n"
        "Open a campaign link provided by the channel owner to continue.\n\n"
        "Admin commands: /setforce, /offforce, /settarget, /genlink, /status, /help"
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = (
        "<b>Commands</b>\n"
        "/start — Open a campaign link\n"
        "/help — Show this help\n\n"
    )
    if is_admin(user_id):
        text += (
            "<b>Admin commands</b>\n"
            "/setforce @channel_or_chat_id — Require joining a channel first\n"
            "/offforce — Disable force-subscription\n"
            "/settarget @channel_or_chat_id direct — Configure target with a direct invite\n"
            "/settarget @channel_or_chat_id request — Configure target with a join-request invite\n"
            "/genlink — Generate a campaign deep link\n"
            "/status — Show current configuration\n\n"
            "The bot must be an administrator in target and force-subscription channels, with invite-link permissions."
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
        raise ValueError("The bot must be an administrator in that channel/group first.")
    can_invite = getattr(member, "can_invite_users", None)
    if getattr(member, "status", "") != "creator" and can_invite is False:
        raise ValueError("Give the bot permission to invite users/manage invite links.")
    return chat


@router.message(Command("setforce"))
async def set_force_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("This command is for the bot administrator only.")
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Usage: <code>/setforce @channel_or_chat_id</code>")
        return
    assert bot is not None
    try:
        chat = await resolve_channel(args[1].strip())
        if chat.username:
            join_url = f"https://t.me/{chat.username}"
        else:
            invite = await bot.create_chat_invite_link(chat_id=chat.id, name="Force subscription")
            join_url = invite.invite_link
        set_setting("force_chat_id", str(chat.id))
        set_setting("force_chat_title", chat.title or chat.full_name or str(chat.id))
        set_setting("force_join_url", join_url)
        await message.answer(
            f"Force-subscription enabled for <b>{escape(chat.title or str(chat.id))}</b>.\n"
            "The bot will check membership before showing the ad button."
        )
    except Exception as exc:
        await message.answer(f"Could not configure the force-sub channel: {escape(str(exc))}")


@router.message(Command("offforce"))
async def off_force_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("This command is for the bot administrator only.")
        return
    set_setting("force_chat_id", None)
    set_setting("force_chat_title", None)
    set_setting("force_join_url", None)
    await message.answer("Force-subscription disabled.")


@router.message(Command("settarget"))
async def set_target_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("This command is for the bot administrator only.")
        return
    args = (message.text or "").split()
    if len(args) != 3 or args[2].lower() not in ("direct", "request"):
        await message.answer(
            "Usage:\n<code>/settarget @channel_or_chat_id direct</code>\n"
            "or\n<code>/settarget @channel_or_chat_id request</code>"
        )
        return
    assert bot is not None
    try:
        chat = await resolve_channel(args[1])
        mode = args[2].lower()
        set_setting("target_chat_id", str(chat.id))
        set_setting("target_chat_title", chat.title or chat.full_name or str(chat.id))
        set_setting("target_invite_mode", mode)
        mode_label = "direct membership link" if mode == "direct" else "join-request link (admins must approve requests)"
        await message.answer(
            f"Target configured: <b>{escape(chat.title or str(chat.id))}</b>\n"
            f"Invite mode: <b>{mode_label}</b>\n\nUse /genlink to generate a campaign URL."
        )
    except Exception as exc:
        await message.answer(f"Could not configure target channel: {escape(str(exc))}")


@router.message(Command("genlink"))
async def generate_link_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("This command is for the bot administrator only.")
        return
    target_chat_id = get_setting("target_chat_id")
    target_title = get_setting("target_chat_title")
    invite_mode = get_setting("target_invite_mode")
    if not target_chat_id or not target_title or not invite_mode:
        await message.answer("Configure a target first with /settarget @channel_or_chat_id direct or request")
        return
    token = secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")[:12]
    create_campaign(token, target_chat_id, target_title, invite_mode, user_id)
    link = f"https://t.me/{bot_username}?start={token}"
    await message.answer(
        "<b>Campaign link created</b>\n\n"
        f"Target: <b>{escape(target_title)}</b>\n"
        f"Invite mode: <b>{escape(invite_mode)}</b>\n\n"
        f"<code>{escape(link)}</code>\n\n"
        "Share this bot link. Users must pass the configured force-sub check and receive a qualifying ad confirmation before getting the target invite link."
    )


@router.message(Command("status"))
async def status_handler(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.answer("This command is for the bot administrator only.")
        return
    force = get_setting("force_chat_title") or "Disabled"
    target = get_setting("target_chat_title") or "Not configured"
    mode = get_setting("target_invite_mode") or "Not configured"
    await message.answer(
        "<b>Configuration</b>\n"
        f"Force-sub channel: <b>{escape(force)}</b>\n"
        f"Target channel: <b>{escape(target)}</b>\n"
        f"Invite mode: <b>{escape(mode)}</b>\n"
        f"Mini App URL configured: <b>{'Yes' if WEBAPP_URL.startswith('https://') else 'No'}</b>"
    )


@router.callback_query(F.data.startswith("checkforce"))
async def check_force_callback(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    if not await member_of_force_channel(callback.from_user.id):
        await callback.answer("I still cannot verify that you joined. Join the channel first.", show_alert=True)
        return
    await callback.answer("Membership verified")
    token = callback.data.split(":", 1)[1] if ":" in (callback.data or "") else ""
    if token and get_campaign(token):
        await show_campaign_flow(callback.message, token, callback.from_user.id)
    else:
        await callback.message.answer("Membership verified. Reopen the campaign link to continue.")


@router.callback_query(F.data.startswith("getlink:"))
async def get_link_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    token = callback.data.split(":", 1)[1]
    campaign = get_campaign(token)
    if not campaign:
        await callback.answer("Campaign not found.", show_alert=True)
        return
    if not await member_of_force_channel(callback.from_user.id):
        await callback.answer("Join the required channel first.", show_alert=True)
        await callback.message.answer("Join the required channel to continue.", reply_markup=force_join_keyboard(token))
        return
    existing = get_unlock(callback.from_user.id, token)
    if existing:
        await callback.answer("Your link is ready")
        await callback.message.answer(
            f"Here is your invite link for <b>{escape(campaign['target_title'])}</b>:\n{escape(existing['invite_link'])}"
        )
        return
    if not has_rewarded_intent(callback.from_user.id, token):
        await callback.answer("Watch and complete the rewarded ad first.", show_alert=True)
        return

    assert bot is not None
    try:
        target_chat_id = campaign["target_chat_id"]
        target_chat_arg = int(target_chat_id) if str(target_chat_id).lstrip("-").isdigit() else target_chat_id
        create_kwargs = {
            "chat_id": target_chat_arg,
            "name": f"gate-{callback.from_user.id}-{token}",
            "expire_date": int(time.time()) + 86_400,
        }
        if campaign["invite_mode"] == "request":
            # Telegram does not allow member_limit together with creates_join_request.
            create_kwargs["creates_join_request"] = True
        else:
            create_kwargs["creates_join_request"] = False
            create_kwargs["member_limit"] = 1
        invite = await bot.create_chat_invite_link(**create_kwargs)
        save_unlock(callback.from_user.id, token, invite.invite_link)
        # Use the stored link in case two callbacks arrived simultaneously.
        stored = get_unlock(callback.from_user.id, token)
        link = stored["invite_link"] if stored else invite.invite_link
        request_note = "This link sends a join request; channel admins must approve it." if campaign["invite_mode"] == "request" else "This direct invite is limited to one successful join."
        await callback.answer("Invite link created")
        await callback.message.answer(
            f"<b>Unlocked: {escape(campaign['target_title'])}</b>\n\n"
            f"{escape(request_note)}\n\n{escape(link)}"
        )
    except Exception as exc:
        await callback.answer("Could not create the invite link.", show_alert=True)
        await callback.message.answer(
            "Telegram could not create the link. Check that the bot is still an admin in the target channel and can invite users.\n"
            f"Details: {escape(str(exc))}"
        )


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
    print(f"Bot @{bot_username} is polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
