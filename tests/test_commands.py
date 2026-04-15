"""Tests for bot command handlers in ahsoka/bot/commands.py.

Handlers are registered as closures inside register_bot_commands, so we extract
their callbacks directly from the aiogram Router and call them with mock Messages.
The middleware is bypassed intentionally — its logic is verified by dedicated tests.
"""
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

from aiogram import Dispatcher

from ahsoka.bot.commands import register_bot_commands, WaitingForInput, _fmt_tokens, _pricing_for
from ahsoka.config import Settings
from ahsoka.database import (
    get_user_config, init_db, load_watched_channels, get_or_create_user, get_user,
    save_batch_usage,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

OWNER_ID = 12345
USER_ID = OWNER_ID  # default test user


@pytest.fixture
def settings() -> MagicMock:
    s = MagicMock(spec=Settings)
    s.owner_chat_id = OWNER_ID
    return s


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c, owner_chat_id=OWNER_ID)
        yield c


def make_msg(text: str, user_id: int = USER_ID) -> MagicMock:
    """Create a minimal mock aiogram Message."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.forward_from_chat = None
    return msg


def make_ctx() -> MagicMock:
    """Create a minimal mock FSMContext."""
    ctx = MagicMock()
    ctx.set_state = AsyncMock()
    ctx.clear = AsyncMock()
    ctx.get_state = AsyncMock(return_value=None)
    return ctx


def get_handler_map(dp: Dispatcher) -> dict[str, object]:
    """Map handler callback names to their functions from all sub-routers."""
    handlers = {}
    for router in dp.sub_routers:
        for h in router.message.handlers:
            handlers[h.callback.__name__] = h.callback
    return handlers


def get_handlers_list(dp: Dispatcher, router_idx: int = 0) -> list:
    """Return handler callbacks in registration order from a specific sub-router."""
    router = dp.sub_routers[router_idx]
    return [h.callback for h in router.message.handlers]


def setup_dp(conn, settings, watched=None):
    watched_channels: set[int] = watched if watched is not None else set()
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels)
    handlers = get_handler_map(dp)
    return dp, handlers, watched_channels


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_includes_routers(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    assert len(dp.sub_routers) == 2  # user_router + admin_router


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def test_start_registers_user(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/start", user_id=99999)
    await h["cmd_start"](msg, make_ctx())
    user = await get_user(conn, 99999)
    assert user is not None
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setstack
# ---------------------------------------------------------------------------

async def test_setstack_persists_value(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setstack"](make_msg("/setstack python go"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).stack == "python go"


async def test_setstack_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("/setstack")
    await h["cmd_setstack"](msg, ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setstack)
    msg.reply.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).stack == ""


async def test_setstack_clears_pending_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setstack"](make_msg("/setstack rust"), ctx)
    ctx.clear.assert_awaited_once()


async def test_setstack_replies(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/setstack rust")
    await h["cmd_setstack"](msg, make_ctx())
    msg.reply.assert_awaited_once()


async def test_input_setstack_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setstack"](make_msg("python go"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).stack == "python go"


async def test_input_setstack_empty_clears(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setstack"](make_msg(""), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).stack == ""


# ---------------------------------------------------------------------------
# /setseniority
# ---------------------------------------------------------------------------

async def test_setseniority_persists_value(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setseniority"](make_msg("/setseniority senior"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).seniority == "senior"


async def test_setseniority_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setseniority"](make_msg("/setseniority"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setseniority)


async def test_input_setseniority_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setseniority"](make_msg("lead"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).seniority == "lead"


# ---------------------------------------------------------------------------
# /setremote
# ---------------------------------------------------------------------------

async def test_setremote_persists_value(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setremote"](make_msg("/setremote remote"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).remote == "remote"


async def test_setremote_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setremote"](make_msg("/setremote"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setremote)


async def test_input_setremote_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setremote"](make_msg("hybrid"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).remote == "hybrid"


# ---------------------------------------------------------------------------
# /setlocation
# ---------------------------------------------------------------------------

async def test_setlocation_persists_value(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setlocation"](make_msg("/setlocation Berlin"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).location == "Berlin"


async def test_setlocation_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setlocation"](make_msg("/setlocation"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setlocation)


async def test_input_setlocation_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setlocation"](make_msg("Amsterdam"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).location == "Amsterdam"


# ---------------------------------------------------------------------------
# /setsalary
# ---------------------------------------------------------------------------

async def test_setsalary_valid(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/setsalary 3000 6000")
    await h["cmd_setsalary"](msg, make_ctx())
    cfg = await get_user_config(conn, USER_ID)
    assert cfg.salary_min == "3000"
    assert cfg.salary_max == "6000"
    msg.reply.assert_awaited_once()


async def test_setsalary_prompts_when_invalid(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setsalary"](make_msg("/setsalary notanumber"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setsalary)


async def test_setsalary_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setsalary"](make_msg("/setsalary"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setsalary)


async def test_input_setsalary_valid(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setsalary"](make_msg("2000 5000"), ctx)
    ctx.clear.assert_awaited_once()
    cfg = await get_user_config(conn, USER_ID)
    assert cfg.salary_min == "2000"
    assert cfg.salary_max == "5000"


async def test_input_setsalary_invalid_stays_in_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("three thousand")
    await h["input_setsalary"](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setthreshold
# ---------------------------------------------------------------------------

async def test_setthreshold_valid(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setthreshold"](make_msg("/setthreshold 5"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).threshold == 5


async def test_setthreshold_boundary_zero(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setthreshold"](make_msg("/setthreshold 0"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).threshold == 0


async def test_setthreshold_boundary_ten(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setthreshold"](make_msg("/setthreshold 10"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).threshold == 10


async def test_setthreshold_prompts_when_out_of_range(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setthreshold"](make_msg("/setthreshold 11"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setthreshold)


async def test_setthreshold_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setthreshold"](make_msg("/setthreshold"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setthreshold)


async def test_input_setthreshold_valid(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setthreshold"](make_msg("7"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).threshold == 7


async def test_input_setthreshold_invalid_stays_in_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("eleven")
    await h["input_setthreshold"](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


async def test_input_setthreshold_out_of_range_stays_in_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("99")
    await h["input_setthreshold"](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setkeywords
# ---------------------------------------------------------------------------

async def test_setkeywords_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python django fastapi"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == "python django fastapi"


async def test_setkeywords_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_setkeywords"](make_msg("/setkeywords"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setkeywords)


async def test_input_setkeywords_persists(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["input_setkeywords"](make_msg("rust go wasm"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).keywords == "rust go wasm"


# ---------------------------------------------------------------------------
# /addkeyword
# ---------------------------------------------------------------------------

async def test_addkeyword_appends_to_empty(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_addkeyword"](make_msg("/addkeyword python django"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == "python django"


async def test_addkeyword_appends_to_existing(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python"), make_ctx())
    await h["cmd_addkeyword"](make_msg("/addkeyword fastapi"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == "python fastapi"


async def test_addkeyword_deduplicates(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python django"), make_ctx())
    await h["cmd_addkeyword"](make_msg("/addkeyword django fastapi"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == "python django fastapi"


async def test_addkeyword_all_duplicates_reply(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python"), make_ctx())
    msg = make_msg("/addkeyword python")
    await h["cmd_addkeyword"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "duplicate" in reply_text.lower() or "no new" in reply_text.lower()


async def test_addkeyword_preserves_order(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords c b a"), make_ctx())
    await h["cmd_addkeyword"](make_msg("/addkeyword d"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == "c b a d"


async def test_addkeyword_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_addkeyword"](make_msg("/addkeyword"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.addkeyword)


async def test_input_addkeyword_appends(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    await h["input_addkeyword"](make_msg("fastapi"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).keywords == "python fastapi"


async def test_input_addkeyword_deduplicates(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    await h["input_addkeyword"](make_msg("python django"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).keywords == "python django"


async def test_input_addkeyword_empty_does_nothing(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    msg = make_msg("")
    await h["input_addkeyword"](msg, ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_user_config(conn, USER_ID)).keywords == "python"


# ---------------------------------------------------------------------------
# /resetkeywords
# ---------------------------------------------------------------------------

async def test_resetkeywords_clears_keywords(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_setkeywords"](make_msg("/setkeywords python django"), make_ctx())
    await h["cmd_resetkeywords"](make_msg("/resetkeywords"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == ""


async def test_resetkeywords_reply_mentions_cleared(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/resetkeywords")
    await h["cmd_resetkeywords"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "cleared" in reply_text.lower() or "pass" in reply_text.lower()


async def test_resetkeywords_idempotent(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_resetkeywords"](make_msg("/resetkeywords"), make_ctx())
    await h["cmd_resetkeywords"](make_msg("/resetkeywords"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).keywords == ""


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def test_status_reflects_current_config(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    watched.add(-100111)
    await h["cmd_setstack"](make_msg("/setstack python"), make_ctx())
    await h["cmd_setthreshold"](make_msg("/setthreshold 6"), make_ctx())

    msg = make_msg("/status")
    await h["cmd_status"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "python" in reply_text
    assert "6/10" in reply_text


async def test_status_cancels_waiting_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_status"](make_msg("/status"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# /pause and /resume
# ---------------------------------------------------------------------------

async def test_pause_sets_paused_flag(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_pause"](make_msg("/pause"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).paused is True


async def test_resume_clears_paused_flag(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_pause"](make_msg("/pause"), make_ctx())
    await h["cmd_resume"](make_msg("/resume"), make_ctx())
    assert (await get_user_config(conn, USER_ID)).paused is False


async def test_pause_cancels_waiting_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_pause"](make_msg("/pause"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# /channels
# ---------------------------------------------------------------------------

async def test_channels_lists_watched(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    watched.update([-100111, -100222])
    msg = make_msg("/channels")
    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "-100111" in reply_text
    assert "-100222" in reply_text


async def test_channels_empty_set(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/channels")
    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "No channels" in reply_text


# ---------------------------------------------------------------------------
# /watch (forwarded message channel discovery)
# ---------------------------------------------------------------------------

async def test_watch_prompts_for_forward(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("/watch")
    await h["cmd_watch"](msg, ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.watch_channel)


async def test_input_watch_adds_channel(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    msg = make_msg("")
    chat = MagicMock()
    chat.id = -100999
    chat.title = "Job Channel"
    chat.username = "jobchan"
    msg.forward_from_chat = chat
    ctx = make_ctx()
    await h["input_watch_channel"](msg, ctx)
    ctx.clear.assert_awaited_once()
    assert -100999 in watched
    assert -100999 in await load_watched_channels(conn)


async def test_input_watch_no_forward_replies_error(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("not a forward")
    msg.forward_from_chat = None
    ctx = make_ctx()
    await h["input_watch_channel"](msg, ctx)
    msg.reply.assert_awaited_once()
    assert "forward" in msg.reply.call_args[0][0].lower()


async def test_input_watch_skips_already_watched(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    watched.add(-100999)
    msg = make_msg("")
    chat = MagicMock()
    chat.id = -100999
    chat.title = "Job Channel"
    msg.forward_from_chat = chat
    ctx = make_ctx()
    await h["input_watch_channel"](msg, ctx)
    reply_text = msg.reply.call_args[0][0]
    assert "already" in reply_text.lower()


# ---------------------------------------------------------------------------
# /notify
# ---------------------------------------------------------------------------

async def test_notify_dm_resets_to_dm(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/notify dm")
    await h["cmd_notify"](msg, make_ctx())
    config = await get_user_config(conn, USER_ID)
    assert config.notify_chat_id == USER_ID


async def test_notify_prompts_for_forward(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_notify"](make_msg("/notify"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.notify_channel)


# ---------------------------------------------------------------------------
# Admin: /removechannel
# ---------------------------------------------------------------------------

async def test_removechannel_valid(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    # Add channel first via watch
    msg = make_msg("")
    chat = MagicMock()
    chat.id = -100999
    chat.title = "Job Channel"
    chat.username = "jobchan"
    msg.forward_from_chat = chat
    await h["input_watch_channel"](msg, make_ctx())
    assert -100999 in watched

    msg = make_msg("/removechannel -100999")
    await h["cmd_removechannel"](msg, make_ctx())
    assert -100999 not in watched


async def test_removechannel_invalid_id_replies_error(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/removechannel notanid")
    await h["cmd_removechannel"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "integer" in reply_text.lower()


async def test_removechannel_prompts_when_no_arg(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_removechannel"](make_msg("/removechannel"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.removechannel)


async def test_input_removechannel_valid(conn, settings):
    _, h, watched = setup_dp(conn, settings)
    # Add first
    msg = make_msg("")
    chat = MagicMock()
    chat.id = -100777
    chat.title = "Chan"
    chat.username = "chan"
    msg.forward_from_chat = chat
    await h["input_watch_channel"](msg, make_ctx())

    ctx = make_ctx()
    await h["input_removechannel"](make_msg("-100777"), ctx)
    ctx.clear.assert_awaited_once()
    assert -100777 not in watched


async def test_input_removechannel_invalid_stays_in_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("notanid")
    await h["input_removechannel"](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# State cancellation
# ---------------------------------------------------------------------------

async def test_any_command_cancels_pending_state(conn, settings):
    """Sending /status while waiting for /setsalary input should clear the state."""
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_status"](make_msg("/status"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# Middleware: ensure_user allows registered, blocks banned
# ---------------------------------------------------------------------------

async def test_ensure_user_middleware_allows_registered(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[0]  # user_router
    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = OWNER_ID

    await middleware(fake_handler, msg, {})
    assert called is True


async def test_ensure_user_middleware_blocks_banned(conn, settings):
    from ahsoka.database import ban_user
    await ban_user(conn, OWNER_ID)

    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[0]
    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = OWNER_ID

    await middleware(fake_handler, msg, {})
    assert called is False


# ---------------------------------------------------------------------------
# Admin middleware
# ---------------------------------------------------------------------------

async def test_admin_middleware_allows_admin(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[1]  # admin_router
    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = OWNER_ID  # is_admin=True

    await middleware(fake_handler, msg, {})
    assert called is True


async def test_admin_middleware_blocks_non_admin(conn, settings):
    # Create a non-admin user
    await get_or_create_user(conn, 99999)

    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[1]
    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = 99999

    await middleware(fake_handler, msg, {})
    assert called is False


# ---------------------------------------------------------------------------
# /stats (admin)
# ---------------------------------------------------------------------------


async def test_stats_no_api_data(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/stats")
    await h["cmd_stats"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "no data" in reply_text.lower()


async def test_stats_shows_api_line_when_usage_exists(conn, settings):
    await save_batch_usage(
        conn, "batch_x1", "claude-haiku-4-5-20251001",
        input_tokens=500_000, output_tokens=20_000,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        succeeded=10,
    )
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/stats")
    await h["cmd_stats"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "API:" in reply_text
    assert "batches" in reply_text
    assert "est. $" in reply_text


async def test_stats_shows_cached_tokens_when_nonzero(conn, settings):
    await save_batch_usage(
        conn, "batch_x2", "claude-haiku-4-5-20251001",
        input_tokens=100_000, output_tokens=5_000,
        cache_creation_input_tokens=10_000, cache_read_input_tokens=50_000,
        succeeded=4,
    )
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/stats")
    await h["cmd_stats"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "cached" in reply_text


async def test_stats_cancels_waiting_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_stats"](make_msg("/stats"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_fmt_tokens_millions():
    assert _fmt_tokens(1_200_000) == "1.2M"


def test_fmt_tokens_thousands():
    assert _fmt_tokens(45_000) == "45K"


def test_fmt_tokens_small():
    assert _fmt_tokens(500) == "500"


def test_pricing_for_known_model():
    prices = _pricing_for("claude-haiku-4-5-20251001")
    assert prices is not None
    assert len(prices) == 4


def test_pricing_for_unknown_model():
    assert _pricing_for("gpt-99") is None
