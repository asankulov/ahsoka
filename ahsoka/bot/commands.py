import logging

import aiosqlite
from aiogram import Dispatcher, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, Message

from ahsoka import database as db
from ahsoka.config import Settings

logger = logging.getLogger(__name__)


class WaitingForInput(StatesGroup):
    setstack      = State()
    setseniority  = State()
    setremote     = State()
    setlocation   = State()
    setsalary     = State()
    setthreshold  = State()
    setkeywords   = State()
    addkeyword    = State()
    addchannel    = State()
    removechannel = State()


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

    # -------------------------------------------------------------------------
    # Command handlers (indices 0–14)
    # Each clears any pending wait-state first, then proceeds normally or
    # enters a new wait-state when called with no argument.
    # -------------------------------------------------------------------------

    @router.message(Command("setstack"))
    async def cmd_setstack(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setstack)
            await message.reply("What tech stack are you looking for? (e.g. python go rust)")
            return
        await db.set_config(conn, "stack", value)
        await message.reply(f"Stack set to: {value}")

    @router.message(Command("setseniority"))
    async def cmd_setseniority(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setseniority)
            await message.reply("What seniority level? (e.g. senior, lead, staff)")
            return
        await db.set_config(conn, "seniority", value)
        await message.reply(f"Seniority set to: {value}")

    @router.message(Command("setremote"))
    async def cmd_setremote(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setremote)
            await message.reply("Work mode? (e.g. remote, hybrid, onsite)")
            return
        await db.set_config(conn, "remote", value)
        await message.reply(f"Work mode set to: {value}")

    @router.message(Command("setlocation"))
    async def cmd_setlocation(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setlocation)
            await message.reply("Preferred location? (e.g. Berlin, Remote EU)")
            return
        await db.set_config(conn, "location", value)
        await message.reply(f"Location set to: {value}")

    @router.message(Command("setsalary"))
    async def cmd_setsalary(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            await db.set_config(conn, "salary_min", parts[1])
            await db.set_config(conn, "salary_max", parts[2])
            await message.reply(f"Salary range set to: {parts[1]}–{parts[2]}")
        else:
            await state.set_state(WaitingForInput.setsalary)
            await message.reply("Enter salary range: <min> <max>  (e.g. 3000 6000)")

    @router.message(Command("setthreshold"))
    async def cmd_setthreshold(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 2 and parts[1].isdigit() and 0 <= int(parts[1]) <= 10:
            await db.set_config(conn, "threshold", parts[1])
            await message.reply(f"Score threshold set to: {parts[1]}/10")
        else:
            await state.set_state(WaitingForInput.setthreshold)
            await message.reply("Enter minimum score (0–10):")

    @router.message(Command("setkeywords"))
    async def cmd_setkeywords(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.setkeywords)
            await message.reply("Enter keywords separated by spaces:")
            return
        await db.set_config(conn, "keywords", value)
        await message.reply(f"Keywords set: {', '.join(value.split())}")

    @router.message(Command("addkeyword"))
    async def cmd_addkeyword(message: Message, state: FSMContext) -> None:
        await state.clear()
        value = _arg(message.text)
        if not value:
            await state.set_state(WaitingForInput.addkeyword)
            await message.reply("Enter keyword(s) to add:")
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
    async def cmd_resetkeywords(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_config(conn, "keywords", "")
        await message.reply("Keywords cleared — all posts will pass the filter.")

    @router.message(Command("status"))
    async def cmd_status(message: Message, state: FSMContext) -> None:
        await state.clear()
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
    async def cmd_pause(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_config(conn, "paused", "true")
        await message.reply("Paused. Posts will still be marked as seen.")

    @router.message(Command("resume"))
    async def cmd_resume(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_config(conn, "paused", "false")
        await message.reply("Resumed.")

    @router.message(Command("addchannel"))
    async def cmd_addchannel(message: Message, state: FSMContext) -> None:
        await state.clear()
        parts = (message.text or "").split()
        if len(parts) == 2:
            try:
                channel_id = int(parts[1])
                await db.add_channel(conn, channel_id)
                watched_channels.add(channel_id)
                await message.reply(f"Now watching channel {channel_id}.")
                return
            except ValueError:
                await message.reply("Channel ID must be an integer.")
                return
        await state.set_state(WaitingForInput.addchannel)
        await message.reply("Enter channel ID to add (e.g. -1001234567890):")

    @router.message(Command("removechannel"))
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

    @router.message(Command("channels"))
    async def cmd_channels(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not watched_channels:
            await message.reply("No channels being watched.")
            return
        lines = [str(c) for c in sorted(watched_channels)]
        await message.reply("Watched channels:\n" + "\n".join(lines))

    # -------------------------------------------------------------------------
    # State-input handlers (indices 15–24)
    # Receive the follow-up message when the user is in a waiting state.
    # -------------------------------------------------------------------------

    @router.message(StateFilter(WaitingForInput.setstack))
    async def input_setstack(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_config(conn, "stack", value)
        await message.reply(f"Stack set to: {value or '(cleared)'}")

    @router.message(StateFilter(WaitingForInput.setseniority))
    async def input_setseniority(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_config(conn, "seniority", value)
        await message.reply(f"Seniority set to: {value or '(cleared)'}")

    @router.message(StateFilter(WaitingForInput.setremote))
    async def input_setremote(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_config(conn, "remote", value)
        await message.reply(f"Work mode set to: {value or '(cleared)'}")

    @router.message(StateFilter(WaitingForInput.setlocation))
    async def input_setlocation(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_config(conn, "location", value)
        await message.reply(f"Location set to: {value or '(cleared)'}")

    @router.message(StateFilter(WaitingForInput.setsalary))
    async def input_setsalary(message: Message, state: FSMContext) -> None:
        parts = (message.text or "").split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            await state.clear()
            await db.set_config(conn, "salary_min", parts[0])
            await db.set_config(conn, "salary_max", parts[1])
            await message.reply(f"Salary range set to: {parts[0]}–{parts[1]}")
        else:
            await message.reply("Please enter two integers: <min> <max>  (e.g. 3000 6000)")

    @router.message(StateFilter(WaitingForInput.setthreshold))
    async def input_setthreshold(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if text.isdigit() and 0 <= int(text) <= 10:
            await state.clear()
            await db.set_config(conn, "threshold", text)
            await message.reply(f"Score threshold set to: {text}/10")
        else:
            await message.reply("Please enter a whole number between 0 and 10.")

    @router.message(StateFilter(WaitingForInput.setkeywords))
    async def input_setkeywords(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        await db.set_config(conn, "keywords", value)
        await message.reply(f"Keywords set: {', '.join(value.split())}" if value else "Keywords cleared — all posts will pass the filter.")

    @router.message(StateFilter(WaitingForInput.addkeyword))
    async def input_addkeyword(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        await state.clear()
        if not value:
            await message.reply("No keywords provided — nothing changed.")
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

    @router.message(StateFilter(WaitingForInput.addchannel))
    async def input_addchannel(message: Message, state: FSMContext) -> None:
        try:
            channel_id = int((message.text or "").strip())
        except ValueError:
            await message.reply("Please enter a valid integer channel ID (e.g. -1001234567890).")
            return
        await state.clear()
        await db.add_channel(conn, channel_id)
        watched_channels.add(channel_id)
        await message.reply(f"Now watching channel {channel_id}.")

    @router.message(StateFilter(WaitingForInput.removechannel))
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

    dp.include_router(router)
