import logging

import aiosqlite
from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from ahsoka import database as db
from ahsoka.config import Settings

logger = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="setstack",       description="Set desired tech stack"),
    BotCommand(command="setseniority",   description="Set seniority level"),
    BotCommand(command="setremote",      description="Set work mode (remote/hybrid/onsite)"),
    BotCommand(command="setlocation",    description="Set preferred location"),
    BotCommand(command="setsalary",      description="Set salary range: /setsalary <min> <max>"),
    BotCommand(command="setthreshold",   description="Set minimum score (0–10)"),
    BotCommand(command="setkeywords",    description="Replace the entire keyword list"),
    BotCommand(command="addkeyword",     description="Append keyword(s) to the list"),
    BotCommand(command="resetkeywords",  description="Clear all keywords"),
    BotCommand(command="status",         description="Show current filter criteria"),
    BotCommand(command="pause",          description="Pause forwarding (still marks seen)"),
    BotCommand(command="resume",         description="Resume forwarding"),
    BotCommand(command="addchannel",     description="Add a channel to the watch list"),
    BotCommand(command="removechannel",  description="Remove a channel from the watch list"),
    BotCommand(command="channels",       description="List watched channels"),
]


def register_bot_commands(
    dp: Dispatcher,
    conn: aiosqlite.Connection,
    settings: Settings,
    watched_channels: set[int],
) -> None:
    router = Router()

    @router.message.middleware()
    async def owner_only(handler, message: Message, data: dict) -> None:
        if message.from_user and message.from_user.id != settings.owner_chat_id:
            return
        await handler(message, data)

    def _arg(text: str | None, n: int = 1) -> str:
        """Return the nth-onwards argument from a command string."""
        parts = (text or "").split(maxsplit=n)
        return parts[n].strip() if len(parts) > n else ""

    @router.message(Command("setstack"))
    async def cmd_setstack(message: Message) -> None:
        value = _arg(message.text)
        await db.set_config(conn, "stack", value)
        await message.reply(f"Stack set to: {value or '(cleared)'}")

    @router.message(Command("setseniority"))
    async def cmd_setseniority(message: Message) -> None:
        value = _arg(message.text)
        await db.set_config(conn, "seniority", value)
        await message.reply(f"Seniority set to: {value or '(cleared)'}")

    @router.message(Command("setremote"))
    async def cmd_setremote(message: Message) -> None:
        value = _arg(message.text)
        await db.set_config(conn, "remote", value)
        await message.reply(f"Work mode set to: {value or '(cleared)'}")

    @router.message(Command("setlocation"))
    async def cmd_setlocation(message: Message) -> None:
        value = _arg(message.text)
        await db.set_config(conn, "location", value)
        await message.reply(f"Location set to: {value or '(cleared)'}")

    @router.message(Command("setsalary"))
    async def cmd_setsalary(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            await db.set_config(conn, "salary_min", parts[1])
            await db.set_config(conn, "salary_max", parts[2])
            await message.reply(f"Salary range set to: {parts[1]}–{parts[2]}")
        else:
            await message.reply("Usage: /setsalary <min> <max>")

    @router.message(Command("setthreshold"))
    async def cmd_setthreshold(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) == 2 and parts[1].isdigit() and 0 <= int(parts[1]) <= 10:
            await db.set_config(conn, "threshold", parts[1])
            await message.reply(f"Score threshold set to: {parts[1]}/10")
        else:
            await message.reply("Usage: /setthreshold <0–10>")

    @router.message(Command("setkeywords"))
    async def cmd_setkeywords(message: Message) -> None:
        value = _arg(message.text)
        if not value:
            await message.reply("Usage: /setkeywords <kw1> [kw2 ...]\nTo clear all keywords use /resetkeywords.")
            return
        await db.set_config(conn, "keywords", value)
        await message.reply(f"Keywords set: {', '.join(value.split())}")

    @router.message(Command("addkeyword"))
    async def cmd_addkeyword(message: Message) -> None:
        value = _arg(message.text)
        if not value:
            await message.reply("Usage: /addkeyword <kw1> [kw2 ...]")
            return
        new_keywords = value.split()
        config = await db.get_config(conn)
        existing = config.keywords.split() if config.keywords else []
        existing_set = set(existing)
        added = [kw for kw in new_keywords if kw not in existing_set]
        for kw in added:
            existing.append(kw)
            existing_set.add(kw)
        await db.set_config(conn, "keywords", " ".join(existing))
        if added:
            await message.reply(f"Added: {', '.join(added)}\nAll keywords: {', '.join(existing)}")
        else:
            await message.reply(f"No new keywords added (all duplicates).\nCurrent keywords: {', '.join(existing)}")

    @router.message(Command("resetkeywords"))
    async def cmd_resetkeywords(message: Message) -> None:
        await db.set_config(conn, "keywords", "")
        await message.reply("Keywords cleared — all posts will pass the filter.")

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        config = await db.get_config(conn)
        lines = [
            f"Stack: {config.stack or '—'}",
            f"Seniority: {config.seniority or '—'}",
            f"Remote: {config.remote or '—'}",
            f"Location: {config.location or '—'}",
            f"Salary: {config.salary_min or '0'}–{config.salary_max or '∞'}",
            f"Threshold: {config.threshold}/10",
            f"Keywords: {config.keywords or '(none — pass all)'}",
            f"Paused: {'yes' if config.paused else 'no'}",
            f"Watching {len(watched_channels)} channel(s): "
            + (", ".join(str(c) for c in sorted(watched_channels)) or "—"),
        ]
        await message.reply("\n".join(lines))

    @router.message(Command("pause"))
    async def cmd_pause(message: Message) -> None:
        await db.set_config(conn, "paused", "true")
        await message.reply("Paused. Posts will still be marked as seen.")

    @router.message(Command("resume"))
    async def cmd_resume(message: Message) -> None:
        await db.set_config(conn, "paused", "false")
        await message.reply("Resumed.")

    @router.message(Command("addchannel"))
    async def cmd_addchannel(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.reply("Usage: /addchannel <channel_id>")
            return
        try:
            channel_id = int(parts[1])
        except ValueError:
            await message.reply("Channel ID must be an integer.")
            return
        await db.add_channel(conn, channel_id)
        watched_channels.add(channel_id)
        await message.reply(f"Now watching channel {channel_id}.")

    @router.message(Command("removechannel"))
    async def cmd_removechannel(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.reply("Usage: /removechannel <channel_id>")
            return
        try:
            channel_id = int(parts[1])
        except ValueError:
            await message.reply("Channel ID must be an integer.")
            return
        await db.remove_channel(conn, channel_id)
        watched_channels.discard(channel_id)
        await message.reply(f"Stopped watching channel {channel_id}.")

    @router.message(Command("channels"))
    async def cmd_channels(message: Message) -> None:
        if not watched_channels:
            await message.reply("No channels being watched.")
            return
        lines = [str(c) for c in sorted(watched_channels)]
        await message.reply("Watched channels:\n" + "\n".join(lines))

    dp.include_router(router)
