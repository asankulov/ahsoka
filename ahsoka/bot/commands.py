import logging

import aiosqlite
from anthropic import AsyncAnthropic
from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, Message

from ahsoka import database as db
from ahsoka.config import Settings
from ahsoka.models import Post
from ahsoka.pipeline.keyword_index import KeywordIndex

logger = logging.getLogger(__name__)

# Batch API pricing per token (USD). Keys are model-name prefixes; longest match wins.
# Prices reflect the 50% batch-API discount vs. standard rates.
_BATCH_PRICING: dict[str, tuple[float, float, float, float]] = {
    # prefix → (input, output, cache_write, cache_read) per token
    "claude-haiku-3-5":  (0.40e-6, 2.00e-6, 0.50e-6, 0.04e-6),
    "claude-haiku-4-5":  (0.40e-6, 2.00e-6, 0.50e-6, 0.04e-6),
    "claude-sonnet-3-5": (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
    "claude-sonnet-4":   (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
    "claude-sonnet-4-6": (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
}


def _pricing_for(model: str) -> tuple[float, float, float, float] | None:
    """Return (input, output, cache_write, cache_read) per-token prices, or None if unknown."""
    match = None
    for prefix, prices in _BATCH_PRICING.items():
        if model.startswith(prefix):
            if match is None or len(prefix) > len(match[0]):
                match = (prefix, prices)
    return match[1] if match else None


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


class WaitingForInput(StatesGroup):
    setstack      = State()
    setseniority  = State()
    setremote     = State()
    setlocation   = State()
    setsalary     = State()
    setthreshold  = State()
    setkeywords   = State()
    addkeyword    = State()
    watch_channel = State()
    notify_channel = State()
    removechannel = State()


# User-visible commands (shown in Telegram "/" menu)
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start",          description="Register and get started"),
    BotCommand(command="setstack",       description="Set desired tech stack"),
    BotCommand(command="setseniority",   description="Set seniority level"),
    BotCommand(command="setremote",      description="Set work mode (remote/hybrid/onsite)"),
    BotCommand(command="setlocation",    description="Set preferred location"),
    BotCommand(command="setsalary",      description="Set salary range: /setsalary <min> <max>"),
    BotCommand(command="setthreshold",   description="Set minimum score (0-10)"),
    BotCommand(command="setkeywords",    description="Replace the entire keyword list"),
    BotCommand(command="addkeyword",     description="Append keyword(s) to the list"),
    BotCommand(command="resetkeywords",  description="Clear all keywords"),
    BotCommand(command="watch",          description="Forward a message to add a watch channel"),
    BotCommand(command="notify",         description="Set notification target (forward msg or /notify dm)"),
    BotCommand(command="pause",          description="Pause notifications"),
    BotCommand(command="resume",         description="Resume notifications"),
    BotCommand(command="status",         description="Show current filter settings"),
    BotCommand(command="channels",       description="List watched channels"),
    BotCommand(command="help",           description="Show available commands"),
]


def register_bot_commands(
    dp: Dispatcher,
    conn: aiosqlite.Connection,
    settings: Settings,
    watched_channels: set[int],
    pyro: object = None,
    keyword_index: KeywordIndex | None = None,
    anthropic: AsyncAnthropic | None = None,
) -> None:
    debug_mode: list[bool] = [False]

    user_router = Router()
    admin_router = Router()

    # --- Middleware: ensure user is registered and not banned ---

    @user_router.message.middleware()
    async def ensure_user(handler, message: Message, data: dict) -> None:
        if not message.from_user:
            return
        user = await db.get_user(conn, message.from_user.id)
        if user and user.is_banned:
            return
        await handler(message, data)

    @admin_router.message.middleware()
    async def admin_only(handler, message: Message, data: dict) -> None:
        if not message.from_user:
            return
        user = await db.get_user(conn, message.from_user.id)
        if not user or not user.is_admin:
            return
        await handler(message, data)

    def _arg(text: str | None, n: int = 1) -> str:
        parts = (text or "").split(maxsplit=n)
        return parts[n].strip() if len(parts) > n else ""

    def _uid(message: Message) -> int:
        return message.from_user.id  # type: ignore[union-attr]

    async def _rebuild_keywords() -> None:
        if keyword_index is not None:
            await keyword_index.rebuild(conn)

    async def _run_debug_score(post: Post, message: Message) -> None:
        from ahsoka.pipeline.scraper import scrape_content
        from ahsoka.pipeline.scorer import build_personalized_prompt, parse_verdict

        config = await db.get_user_config(conn, settings.owner_chat_id)
        if config is None:
            await message.reply("No config found for admin user.")
            return

        content = await scrape_content(post, timeout=settings.scrape_timeout_s)
        prompt_dict = build_personalized_prompt(post, content, config)
        params = prompt_dict["params"]
        try:
            response = await anthropic.messages.create(  # type: ignore[union-attr]
                model=settings.claude_model,
                max_tokens=params["max_tokens"],
                system=params["system"],
                messages=params["messages"],
            )
            response_dict = {
                "result": {
                    "type": "succeeded",
                    "message": {"content": response.content},
                }
            }
        except Exception as exc:
            response_dict = {
                "result": {
                    "type": "errored",
                    "error": {"message": str(exc)},
                }
            }
        verdict = parse_verdict(response_dict, config.user_id)
        match_sym = "\u2713" if verdict.matched else "\u2717"
        lines: list[str] = [
            f"DEBUG \u2014 {post.channel_name}/{post.message_id}",
            f"Content: {len(content)} chars",
            "",
            f"score={verdict.score} {match_sym}",
            verdict.reason,
        ]
        if verdict.red_flags:
            lines.append(f"flags: {', '.join(verdict.red_flags)}")
        await message.reply("\n".join(lines))

    # -------------------------------------------------------------------------
    # User commands
    # -------------------------------------------------------------------------

    @user_router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await db.get_or_create_user(conn, _uid(message))
        await message.reply(
            "Welcome to Ahsoka! I filter job postings from Telegram channels.\n\n"
            "Get started:\n"
            "  /setkeywords python backend — set keywords to match\n"
            "  /setthreshold 6 — minimum score (0-10)\n"
            "  /watch — forward a message from a job channel\n"
            "  /status — see your current settings\n"
            "  /help — all commands"
        )

    @user_router.message(Command("setstack"))
    async def cmd_setstack(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setstack)
            await message.reply("What tech stack are you looking for? (e.g. python go rust)")
            return
        await db.set_user_config(conn, _uid(message), "stack", value)
        await message.reply(f"Stack set to: {value}")

    @user_router.message(Command("setseniority"))
    async def cmd_setseniority(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setseniority)
            await message.reply("What seniority level? (e.g. senior, lead, staff)")
            return
        await db.set_user_config(conn, _uid(message), "seniority", value)
        await message.reply(f"Seniority set to: {value}")

    @user_router.message(Command("setremote"))
    async def cmd_setremote(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setremote)
            await message.reply("Work mode? (e.g. remote, hybrid, onsite)")
            return
        await db.set_user_config(conn, _uid(message), "remote", value)
        await message.reply(f"Work mode set to: {value}")

    @user_router.message(Command("setlocation"))
    async def cmd_setlocation(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setlocation)
            await message.reply("Preferred location? (e.g. Berlin, Remote EU)")
            return
        await db.set_user_config(conn, _uid(message), "location", value)
        await message.reply(f"Location set to: {value}")

    @user_router.message(Command("setsalary"))
    async def cmd_setsalary(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            await db.set_user_config(conn, _uid(message), "salary_min", parts[1])
            await db.set_user_config(conn, _uid(message), "salary_max", parts[2])
            await message.reply(f"Salary range set to: {parts[1]}-{parts[2]}")
        else:
            await state.set_state(WaitingForInput.setsalary)
            await message.reply("Enter salary range: <min> <max>  (e.g. 3000 6000)")

    @user_router.message(Command("setthreshold"))
    async def cmd_setthreshold(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 2 and parts[1].isdigit() and 0 <= int(parts[1]) <= 10:
            await db.set_user_config(conn, _uid(message), "threshold", parts[1])
            await message.reply(f"Score threshold set to: {parts[1]}/10")
        else:
            await state.set_state(WaitingForInput.setthreshold)
            await message.reply("Enter minimum score (0-10):")

    @user_router.message(Command("setkeywords"))
    async def cmd_setkeywords(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setkeywords)
            await message.reply("Enter keywords separated by spaces:")
            return
        await db.set_user_config(conn, _uid(message), "keywords", value)
        await _rebuild_keywords()
        await message.reply(f"Keywords set: {', '.join(value.split())}")

    @user_router.message(Command("addkeyword"))
    async def cmd_addkeyword(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.addkeyword)
            await message.reply("Enter keyword(s) to add:")
            return
        new_keywords = value.split()
        config = await db.get_user_config(conn, _uid(message))
        existing = config.keywords.split() if config.keywords else []
        existing_set = set(existing)
        added = [kw for kw in new_keywords if kw not in existing_set]
        for kw in added:
            existing.append(kw)
            existing_set.add(kw)
        await db.set_user_config(conn, _uid(message), "keywords", " ".join(existing))
        await _rebuild_keywords()
        if added:
            await message.reply(f"Added: {', '.join(added)}\nAll keywords: {', '.join(existing)}")
        else:
            await message.reply(f"No new keywords added (all duplicates).\nCurrent keywords: {', '.join(existing)}")

    @user_router.message(Command("resetkeywords"))
    async def cmd_resetkeywords(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_user_config(conn, _uid(message), "keywords", "")
        await _rebuild_keywords()
        await message.reply("Keywords cleared - all posts will pass the filter.")

    @user_router.message(Command("watch"))
    async def cmd_watch(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(WaitingForInput.watch_channel)
        await message.reply("Forward a message from the channel you want to watch.")

    @user_router.message(Command("notify"))
    async def cmd_notify(message: Message, state: FSMContext) -> None:
        await state.clear()
        arg = _arg(message.text).lower()
        if arg == "dm":
            await db.set_notify_target(conn, _uid(message), _uid(message))
            await message.reply("Notifications will be sent to your DM.")
            return
        await state.set_state(WaitingForInput.notify_channel)
        await message.reply(
            "Forward a message from the channel where you want notifications.\n"
            "The bot must be added to that channel first.\n"
            "Or send /notify dm to use direct messages."
        )

    @user_router.message(Command("pause"))
    async def cmd_pause(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_user_config(conn, _uid(message), "paused", "1")
        await message.reply("Paused. Posts will still be scored but not forwarded to you.")

    @user_router.message(Command("resume"))
    async def cmd_resume(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_user_config(conn, _uid(message), "paused", "0")
        await message.reply("Resumed.")

    @user_router.message(Command("status"))
    async def cmd_status(message: Message, state: FSMContext) -> None:
        await state.clear()
        config = await db.get_user_config(conn, _uid(message))
        notify_label = "DM" if config.notify_chat_id == _uid(message) else str(config.notify_chat_id)
        lines = [
            f"Stack: {config.stack or '-'}",
            f"Seniority: {config.seniority or '-'}",
            f"Remote: {config.remote or '-'}",
            f"Location: {config.location or '-'}",
            f"Salary: {config.salary_min or '0'}-{config.salary_max or 'any'}",
            f"Threshold: {config.threshold}/10",
            f"Keywords: {config.keywords or '(none - pass all)'}",
            f"Paused: {'yes' if config.paused else 'no'}",
            f"Notifications: {notify_label}",
            f"Watching {len(watched_channels)} channel(s)",
        ]
        await message.reply("\n".join(lines))

    @user_router.message(Command("channels"))
    async def cmd_channels(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not watched_channels:
            await message.reply("No channels being watched.")
            return
        lines = []
        for c in sorted(watched_channels):
            if message.bot is None:
                lines.append(str(c))
                continue
            try:
                chat = await message.bot.get_chat(c)
                title = chat.title or str(c)
                if chat.username:
                    lines.append(f"{title} (https://t.me/{chat.username})")
                else:
                    stripped = str(c).lstrip("-")[3:]
                    lines.append(f"{title} (https://t.me/c/{stripped})")
            except Exception:
                lines.append(str(c))
        await message.reply("Watched channels:\n" + "\n".join(lines))

    @user_router.message(Command("help"))
    async def cmd_help(message: Message, state: FSMContext) -> None:
        await state.clear()
        lines = [
            "Available commands:",
            "",
            "Filter settings:",
            "  /setkeywords python backend - Set keyword filter",
            "  /addkeyword fastapi - Add keywords to existing list",
            "  /resetkeywords - Clear all keywords (match everything)",
            "  /setthreshold 6 - Minimum score 0-10 (default: 7)",
            "  /setstack python go - Tech stack preference",
            "  /setseniority senior - Seniority level",
            "  /setremote remote - Work mode (remote/hybrid/onsite)",
            "  /setlocation Berlin - Location preference",
            "  /setsalary 3000 8000 - Monthly salary range",
            "",
            "Channels & notifications:",
            "  /watch - Forward a msg to add a watch channel",
            "  /notify - Forward a msg to set notification channel",
            "  /notify dm - Send notifications to DM (default)",
            "  /channels - List watched channels",
            "",
            "Controls:",
            "  /pause - Pause notifications",
            "  /resume - Resume notifications",
            "  /status - Show your current settings",
        ]
        await message.reply("\n".join(lines))

    # -------------------------------------------------------------------------
    # State-input handlers
    # -------------------------------------------------------------------------

    @user_router.message(StateFilter(WaitingForInput.setstack))
    async def input_setstack(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_user_config(conn, _uid(message), "stack", value)
        await message.reply(f"Stack set to: {value or '(cleared)'}")

    @user_router.message(StateFilter(WaitingForInput.setseniority))
    async def input_setseniority(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_user_config(conn, _uid(message), "seniority", value)
        await message.reply(f"Seniority set to: {value or '(cleared)'}")

    @user_router.message(StateFilter(WaitingForInput.setremote))
    async def input_setremote(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_user_config(conn, _uid(message), "remote", value)
        await message.reply(f"Work mode set to: {value or '(cleared)'}")

    @user_router.message(StateFilter(WaitingForInput.setlocation))
    async def input_setlocation(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_user_config(conn, _uid(message), "location", value)
        await message.reply(f"Location set to: {value or '(cleared)'}")

    @user_router.message(StateFilter(WaitingForInput.setsalary))
    async def input_setsalary(message: Message, state: FSMContext) -> None:
        parts = (message.text or "").split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            await state.clear()
            await db.set_user_config(conn, _uid(message), "salary_min", parts[0])
            await db.set_user_config(conn, _uid(message), "salary_max", parts[1])
            await message.reply(f"Salary range set to: {parts[0]}-{parts[1]}")
        else:
            await message.reply("Please enter two integers: <min> <max>  (e.g. 3000 6000)")

    @user_router.message(StateFilter(WaitingForInput.setthreshold))
    async def input_setthreshold(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if text.isdigit() and 0 <= int(text) <= 10:
            await state.clear()
            await db.set_user_config(conn, _uid(message), "threshold", text)
            await message.reply(f"Score threshold set to: {text}/10")
        else:
            await message.reply("Please enter a whole number between 0 and 10.")

    @user_router.message(StateFilter(WaitingForInput.setkeywords))
    async def input_setkeywords(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_user_config(conn, _uid(message), "keywords", value)
        await _rebuild_keywords()
        await message.reply(f"Keywords set: {', '.join(value.split())}" if value else "Keywords cleared - all posts will pass the filter.")

    @user_router.message(StateFilter(WaitingForInput.addkeyword))
    async def input_addkeyword(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        if not value:
            await message.reply("No keywords provided - nothing changed.")
            return
        new_keywords = value.split()
        config = await db.get_user_config(conn, _uid(message))
        existing = config.keywords.split() if config.keywords else []
        existing_set = set(existing)
        added = [kw for kw in new_keywords if kw not in existing_set]
        for kw in added:
            existing.append(kw)
            existing_set.add(kw)
        await db.set_user_config(conn, _uid(message), "keywords", " ".join(existing))
        await _rebuild_keywords()
        if added:
            await message.reply(f"Added: {', '.join(added)}\nAll keywords: {', '.join(existing)}")
        else:
            await message.reply(f"No new keywords added (all duplicates).\nCurrent keywords: {', '.join(existing)}")

    @user_router.message(StateFilter(WaitingForInput.watch_channel))
    async def input_watch_channel(message: Message, state: FSMContext) -> None:
        await state.clear()
        chat = message.forward_from_chat
        if not chat:
            await message.reply("Please forward a message from a channel. Try /watch again.")
            return
        channel_id = chat.id
        if channel_id in watched_channels:
            await message.reply(f"Already watching: {chat.title or channel_id}")
            return
        # Try to join via Pyrogram if available
        if pyro and getattr(chat, "username", None):
            try:
                await pyro.join_chat(chat.username)
            except Exception:
                pass  # may already be joined or private
        await db.add_channel(conn, channel_id, added_by=_uid(message))
        watched_channels.add(channel_id)
        await message.reply(f"Now watching: {chat.title or channel_id}")

    @user_router.message(StateFilter(WaitingForInput.notify_channel))
    async def input_notify_channel(message: Message, state: FSMContext) -> None:
        await state.clear()
        chat = message.forward_from_chat
        if not chat:
            await message.reply("Please forward a message from a channel. Try /notify again.")
            return
        # Verify bot can send to this channel by trying to get chat info
        from aiogram import Bot
        bot: Bot = message.bot  # type: ignore[assignment]
        try:
            member = await bot.get_chat_member(chat.id, bot.id)
            if member.status not in ("administrator", "creator"):
                await message.reply("I need to be an admin in that channel to send notifications there.")
                return
        except Exception:
            await message.reply("I can't access that channel. Add me as an admin first.")
            return
        await db.set_notify_target(conn, _uid(message), chat.id)
        await message.reply(f"Notifications will be sent to: {chat.title or chat.id}")

    # -------------------------------------------------------------------------
    # Admin commands (hidden from bot menu)
    # -------------------------------------------------------------------------

    @admin_router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext) -> None:
        await state.clear()
        lines = [
            "Admin commands:",
            "",
            "  /removechannel -100123 - Remove a channel from watchlist",
            "  /users - List all registered users",
            "  /ban 123456 - Ban a user (stops notifications)",
            "  /unban 123456 - Unban a user",
            "  /stats - Show usage statistics (users, posts, notifications)",
            "  /debug on/off - Toggle debug scoring mode (forward posts to score)",
            "  /admin - Show this help",
        ]
        await message.reply("\n".join(lines))

    @admin_router.message(Command("debug"))
    async def cmd_debug(message: Message, state: FSMContext) -> None:
        await state.clear()
        arg = _arg(message.text).lower()
        if arg == "on":
            debug_mode[0] = True
            await message.reply("Debug mode ON. Forward a job posting to score it.")
        elif arg == "off":
            debug_mode[0] = False
            await message.reply("Debug mode OFF.")
        else:
            status = "ON" if debug_mode[0] else "OFF"
            await message.reply(f"Debug mode is {status}. Use /debug on or /debug off.")

    @admin_router.message(Command("removechannel"))
    async def cmd_removechannel(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 2:
            try:
                channel_id = int(parts[1])
                await db.remove_channel(conn, channel_id)
                watched_channels.discard(channel_id)
                await message.reply(f"Stopped watching channel {channel_id}.")
                return
            except ValueError:
                await message.reply("Channel ID must be an integer.")
                return
        await state.set_state(WaitingForInput.removechannel)
        await message.reply("Enter channel ID to remove:")

    @admin_router.message(StateFilter(WaitingForInput.removechannel))
    async def input_removechannel(message: Message, state: FSMContext) -> None:
        try:
            channel_id = int((message.text or "").strip())
        except ValueError:
            await message.reply("Please enter a valid integer channel ID.")
            return
        await state.clear()
        await db.remove_channel(conn, channel_id)
        watched_channels.discard(channel_id)
        await message.reply(f"Stopped watching channel {channel_id}.")

    @admin_router.message(Command("users"))
    async def cmd_users(message: Message, state: FSMContext) -> None:
        await state.clear()
        users = await db.list_users(conn)
        if not users:
            await message.reply("No registered users.")
            return
        lines = []
        for u in users:
            flags = []
            if u.is_admin:
                flags.append("admin")
            if u.is_banned:
                flags.append("banned")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            username_str = ""
            if message.bot is not None:
                try:
                    chat = await message.bot.get_chat(u.user_id)
                    if chat.username:
                        username_str = f" @{chat.username}"
                except Exception:
                    pass
            lines.append(f"  {u.user_id}{username_str}{flag_str}")
        await message.reply(f"Registered users ({len(users)}):\n" + "\n".join(lines))

    @admin_router.message(Command("ban"))
    async def cmd_ban(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.reply("Usage: /ban <user_id>")
            return
        target_id = int(parts[1])
        await db.ban_user(conn, target_id)
        await _rebuild_keywords()
        await message.reply(f"User {target_id} banned.")

    @admin_router.message(Command("unban"))
    async def cmd_unban(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.reply("Usage: /unban <user_id>")
            return
        target_id = int(parts[1])
        await db.unban_user(conn, target_id)
        await _rebuild_keywords()
        await message.reply(f"User {target_id} unbanned.")

    @admin_router.message(Command("stats"))
    async def cmd_stats(message: Message, state: FSMContext) -> None:
        await state.clear()
        users = await db.list_users(conn)
        active = [u for u in users if not u.is_banned]
        async with conn.execute("SELECT COUNT(*) FROM seen_posts") as cur:
            (total_posts,) = await cur.fetchone()  # type: ignore[misc]
        async with conn.execute("SELECT COUNT(*) FROM seen_posts WHERE score IS NOT NULL") as cur:
            (scored_posts,) = await cur.fetchone()  # type: ignore[misc]
        async with conn.execute("SELECT COUNT(*) FROM user_notified") as cur:
            (total_notified,) = await cur.fetchone()  # type: ignore[misc]

        usage_by_model = await db.get_total_usage(conn)
        if not usage_by_model:
            api_line = "API: no data yet"
        else:
            total_in = total_out = total_cw = total_cr = total_batches = 0
            total_cost = 0.0
            has_unknown_model = False
            for model, u in usage_by_model.items():
                in_tok = u["input_tokens"] + u["cache_creation_input_tokens"]
                out_tok = u["output_tokens"]
                cr_tok = u["cache_read_input_tokens"]
                total_in     += in_tok
                total_out    += out_tok
                total_cr     += cr_tok
                total_batches += u["batches"]
                prices = _pricing_for(model)
                if prices:
                    p_in, p_out, p_cw, p_cr = prices
                    total_cost += (
                        u["input_tokens"] * p_in
                        + u["output_tokens"] * p_out
                        + u["cache_creation_input_tokens"] * p_cw
                        + u["cache_read_input_tokens"] * p_cr
                    )
                else:
                    has_unknown_model = True

            cached_part = f" ({_fmt_tokens(total_cr)} cached)" if total_cr > 0 else ""
            cost_part = f" · est. ${total_cost:.2f}" if not has_unknown_model else " · est. unknown"
            api_line = (
                f"API: {_fmt_tokens(total_in)} in{cached_part} / {_fmt_tokens(total_out)} out"
                f" · {total_batches} batches{cost_part}"
            )

        lines = [
            f"Users: {len(active)} active, {len(users)} total",
            f"Channels: {len(watched_channels)} watched",
            f"Posts: {total_posts} seen, {scored_posts} scored",
            f"Notifications: {total_notified} sent",
            api_line,
            f"Debug mode: {'ON' if debug_mode[0] else 'off'}",
        ]
        await message.reply("\n".join(lines))

    @admin_router.message(F.forward_origin.as_("origin"))
    async def debug_forwarded_post(message: Message) -> None:
        if not debug_mode[0]:
            return
        if anthropic is None:
            await message.reply("Debug scoring unavailable: Anthropic client not configured.")
            return

        origin = message.forward_origin
        if not hasattr(origin, "message_id"):
            return

        channel_id: int = origin.chat.id
        message_id: int = origin.message_id
        channel_name: str = getattr(origin.chat, "username", None) or str(channel_id)
        text: str = message.text or message.caption or ""
        entities = message.entities or message.caption_entities or []

        seen: set[str] = set()
        urls: list[str] = []
        for entity in entities:
            if len(urls) >= 3:
                break
            if entity.type == "text_link" and entity.url:
                u = entity.url
            elif entity.type == "url":
                u = text[entity.offset: entity.offset + entity.length]
            else:
                continue
            if u and u not in seen:
                seen.add(u)
                urls.append(u)

        post = Post(
            channel_id=channel_id,
            message_id=message_id,
            channel_name=channel_name,
            text=text,
            url=urls[0] if urls else None,
            urls=urls,
        )
        await message.reply(f"Scoring post from {channel_name}/{message_id}\u2026")
        await _run_debug_score(post, message)

    dp.include_router(user_router)
    dp.include_router(admin_router)
