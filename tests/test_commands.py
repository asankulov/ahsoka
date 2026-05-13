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


def setup_dp(conn, settings, watched=None, anthropic=None, pyro=None, keyword_index=None):
    watched_channels: set[int] = watched if watched is not None else set()
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels, pyro=pyro,
                          keyword_index=keyword_index, anthropic=anthropic)
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
# /debug toggle
# ---------------------------------------------------------------------------

async def test_debug_on_enables_mode(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/debug on")
    await h["cmd_debug"](msg, make_ctx())
    msg.reply.assert_awaited_once()
    assert "ON" in msg.reply.call_args[0][0]


async def test_debug_off_disables_mode(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())
    msg = make_msg("/debug off")
    await h["cmd_debug"](msg, make_ctx())
    assert "OFF" in msg.reply.call_args[0][0]


async def test_debug_no_arg_shows_status(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/debug")
    await h["cmd_debug"](msg, make_ctx())
    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args[0][0]
    assert "off" in reply_text.lower() or "ON" in reply_text


async def test_debug_clears_state(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await h["cmd_debug"](make_msg("/debug on"), ctx)
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


# ---------------------------------------------------------------------------
# /admin and /stats mention debug
# ---------------------------------------------------------------------------

async def test_admin_help_mentions_debug(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/admin")
    await h["cmd_admin"](msg, make_ctx())
    assert "/debug" in msg.reply.call_args[0][0]


async def test_stats_shows_debug_mode_off_by_default(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/stats")
    await h["cmd_stats"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "debug" in reply_text.lower()
    assert "off" in reply_text.lower()


async def test_stats_shows_debug_mode_on_after_toggle(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())
    msg = make_msg("/stats")
    await h["cmd_stats"](msg, make_ctx())
    assert "ON" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# debug_forwarded_post handler
# ---------------------------------------------------------------------------

def make_forwarded_msg(channel_id: int = -100111, message_id: int = 42,
                       username: str = "testchan", text: str = "Job posting") -> MagicMock:
    msg = make_msg(text)
    origin = MagicMock()
    origin.type = "channel"
    origin.chat.id = channel_id
    origin.chat.username = username
    origin.message_id = message_id
    msg.forward_origin = origin
    msg.caption = None
    msg.entities = []
    msg.caption_entities = None
    return msg


async def test_forwarded_post_ignored_when_debug_off(conn, settings):
    _, h, _ = setup_dp(conn, settings)
    msg = make_forwarded_msg()
    await h["debug_forwarded_post"](msg)
    msg.reply.assert_not_awaited()


async def test_forwarded_post_scores_when_debug_on(conn, settings):
    from unittest.mock import patch

    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = (
        '"score": 7, "reason": "good match", "matched": true, '
        '"apply": "", "red_flags": [], "stack": ["Python"], '
        '"seniority": "senior", "remote": "remote"}'
    )
    fake_response = MagicMock()
    fake_response.content = [fake_block]

    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=fake_response)

    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg()
    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="job text")):
        await h["debug_forwarded_post"](msg)

    mock_anthropic.messages.create.assert_awaited_once()
    assert msg.reply.await_count >= 2  # "Scoring…" + results


async def test_forwarded_post_no_anthropic_replies_unavailable(conn, settings):
    _, h, _ = setup_dp(conn, settings, anthropic=None)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg()
    await h["debug_forwarded_post"](msg)

    msg.reply.assert_awaited_once()
    assert "unavailable" in msg.reply.call_args[0][0].lower()


async def test_forwarded_post_non_channel_origin_ignored(conn, settings):
    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock()

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg()
    # Remove message_id to simulate non-channel origin
    del msg.forward_origin.message_id
    await h["debug_forwarded_post"](msg)

    mock_anthropic.messages.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# _run_debug_score — TG link resolution (checklist from the-armorer)
# ---------------------------------------------------------------------------

def _make_mock_anthropic() -> MagicMock:
    """Return a minimal AsyncAnthropic-like mock that returns a parseable response."""
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = (
        '"score": 5, "reason": "ok", "matched": true, '
        '"apply": "", "red_flags": [], "stack": [], '
        '"seniority": "", "remote": ""}'
    )
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    mock = MagicMock()
    mock.messages.create = AsyncMock(return_value=fake_response)
    return mock


def make_forwarded_msg_with_urls(urls: list[str], channel_id: int = -100111,
                                  message_id: int = 42, username: str = "testchan",
                                  text: str = "Job posting") -> MagicMock:
    """Forwarded message carrying the given URLs as text_link entities."""
    msg = make_forwarded_msg(channel_id=channel_id, message_id=message_id,
                              username=username, text=text)
    entities = []
    for url in urls:
        ent = MagicMock()
        ent.type = "text_link"
        ent.url = url
        entities.append(ent)
    msg.entities = entities
    return msg


async def test_debug_score_tg_link_resolved_and_appended(conn, settings):
    """t.me link in post.urls + pyro is not None → resolve_tg_link called, result appended."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    tg_url = "https://t.me/somechan/123"
    msg = make_forwarded_msg_with_urls([tg_url])

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link", return_value=True), \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock(return_value="resolved text")) as mock_resolve:
        await h["debug_forwarded_post"](msg)

    mock_resolve.assert_awaited_once_with(tg_url, mock_pyro)
    # The reply with debug output must mention the extra char count from appended content
    reply_text = msg.reply.call_args_list[-1][0][0]
    assert "Content:" in reply_text
    # appended string is "\n\n--- linked from {url} ---\nresolved text"
    appended = f"\n\n--- linked from {tg_url} ---\nresolved text"
    expected_len = len("base") + len(appended)
    assert str(expected_len) in reply_text


async def test_debug_score_http_url_skips_resolve_tg_link(conn, settings):
    """HTTP-only URLs → is_tg_link returns False → resolve_tg_link never called."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg_with_urls(["https://example.com/job"])

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link", return_value=False), \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock()) as mock_resolve:
        await h["debug_forwarded_post"](msg)

    mock_resolve.assert_not_awaited()


async def test_debug_score_resolve_returns_none_content_unchanged(conn, settings):
    """resolve_tg_link returns None → if resolved: guard fires, content unchanged."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg_with_urls(["https://t.me/chan/99"])

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link", return_value=True), \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock(return_value=None)):
        await h["debug_forwarded_post"](msg)

    reply_text = msg.reply.call_args_list[-1][0][0]
    # Content length must equal len("base") — nothing was appended
    assert f"Content: {len('base')} chars" in reply_text


async def test_debug_score_resolve_returns_empty_string_content_unchanged(conn, settings):
    """resolve_tg_link returns '' → if resolved: guard fires, content unchanged."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg_with_urls(["https://t.me/chan/99"])

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link", return_value=True), \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock(return_value="")):
        await h["debug_forwarded_post"](msg)

    reply_text = msg.reply.call_args_list[-1][0][0]
    assert f"Content: {len('base')} chars" in reply_text


async def test_debug_score_pyro_none_skips_resolution_block(conn, settings):
    """pyro is None → resolution block is skipped entirely, resolve_tg_link never called."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    # pyro=None (the default)
    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    msg = make_forwarded_msg_with_urls(["https://t.me/somechan/1"])

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link") as mock_is_tg, \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock()) as mock_resolve:
        await h["debug_forwarded_post"](msg)

    mock_is_tg.assert_not_called()
    mock_resolve.assert_not_awaited()


async def test_debug_score_empty_urls_no_error(conn, settings):
    """post.urls is empty → no error raised, no resolve_tg_link call."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    # No entities → urls=[]
    msg = make_forwarded_msg()

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link") as mock_is_tg, \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link",
               new=AsyncMock()) as mock_resolve:
        await h["debug_forwarded_post"](msg)

    mock_is_tg.assert_not_called()
    mock_resolve.assert_not_awaited()
    # Handler completed without exception → at least one reply was made
    assert msg.reply.await_count >= 1


async def test_debug_score_multiple_tg_links_all_resolved(conn, settings):
    """Multiple t.me links in post.urls → each resolved in turn, all non-empty results appended."""
    from unittest.mock import patch

    mock_anthropic = _make_mock_anthropic()
    mock_pyro = MagicMock()
    settings.scrape_timeout_s = 5.0
    settings.claude_model = "claude-haiku-4-5-20251001"

    _, h, _ = setup_dp(conn, settings, anthropic=mock_anthropic, pyro=mock_pyro)
    await h["cmd_debug"](make_msg("/debug on"), make_ctx())

    url1 = "https://t.me/chan/1"
    url2 = "https://t.me/chan/2"
    msg = make_forwarded_msg_with_urls([url1, url2])

    resolve_results = ["first resolved", "second resolved"]
    resolve_mock = AsyncMock(side_effect=resolve_results)

    with patch("ahsoka.pipeline.scraper.scrape_content", new=AsyncMock(return_value="base")), \
         patch("ahsoka.pipeline.tg_resolver.is_tg_link", return_value=True), \
         patch("ahsoka.pipeline.tg_resolver.resolve_tg_link", new=resolve_mock):
        await h["debug_forwarded_post"](msg)

    assert resolve_mock.await_count == 2
    expected_content = (
        "base"
        f"\n\n--- linked from {url1} ---\nfirst resolved"
        f"\n\n--- linked from {url2} ---\nsecond resolved"
    )
    reply_text = msg.reply.call_args_list[-1][0][0]
    assert f"Content: {len(expected_content)} chars" in reply_text


# ---------------------------------------------------------------------------
# Helper: make a message with a real AsyncMock bot
# ---------------------------------------------------------------------------

def make_msg_with_bot(text: str, user_id: int = USER_ID) -> MagicMock:
    """Create a mock Message where message.bot is an AsyncMock-capable object."""
    msg = make_msg(text, user_id=user_id)
    bot = MagicMock()
    bot.get_chat = AsyncMock()
    msg.bot = bot
    return msg


# ---------------------------------------------------------------------------
# /channels — get_chat resolution
# ---------------------------------------------------------------------------

async def test_channels_public_channel_shows_username_link(conn, settings):
    """Public channel (has username) → line shows 'title (https://t.me/username)'."""
    channel_id = -1001234567
    _, h, watched = setup_dp(conn, settings)
    watched.add(channel_id)

    chat = MagicMock()
    chat.title = "Python Jobs"
    chat.username = "pythonjobs"

    msg = make_msg_with_bot("/channels")
    msg.bot.get_chat = AsyncMock(return_value=chat)

    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Python Jobs (https://t.me/pythonjobs)" in reply_text


async def test_channels_private_channel_shows_c_link(conn, settings):
    """Private channel (no username) → line shows 'title (https://t.me/c/stripped_id)'."""
    channel_id = -1001234567
    _, h, watched = setup_dp(conn, settings)
    watched.add(channel_id)

    chat = MagicMock()
    chat.title = "Private Jobs"
    chat.username = None

    msg = make_msg_with_bot("/channels")
    msg.bot.get_chat = AsyncMock(return_value=chat)

    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    # stripped_id: str(-1001234567).lstrip("-")[3:] == "1234567"
    assert "Private Jobs (https://t.me/c/1234567)" in reply_text


async def test_channels_get_chat_raises_falls_back_to_raw_id(conn, settings):
    """get_chat raising → falls back to raw channel_id string, no exception propagated."""
    channel_id = -1001234567
    _, h, watched = setup_dp(conn, settings)
    watched.add(channel_id)

    msg = make_msg_with_bot("/channels")
    msg.bot.get_chat = AsyncMock(side_effect=Exception("Forbidden"))

    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert str(channel_id) in reply_text


async def test_channels_bot_none_falls_back_to_raw_id(conn, settings):
    """message.bot is None → falls back to raw channel_id for every channel."""
    channel_id = -1001234567
    _, h, watched = setup_dp(conn, settings)
    watched.add(channel_id)

    msg = make_msg("/channels")
    msg.bot = None

    await h["cmd_channels"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert str(channel_id) in reply_text


# Empty watchlist already covered by test_channels_empty_set above.


# ---------------------------------------------------------------------------
# /users — get_chat username resolution
# ---------------------------------------------------------------------------

async def test_users_empty_list_replies_no_users(conn, settings):
    """Empty user list → replies 'No registered users.'"""
    # Use owner_chat_id=0 so init_db skips owner insertion, giving us a clean user table.
    async with aiosqlite.connect(":memory:") as fresh_conn:
        from ahsoka.database import init_db as _init_db
        await _init_db(fresh_conn, owner_chat_id=0)
        dp2 = Dispatcher()
        s2 = MagicMock(spec=Settings)
        s2.owner_chat_id = 0
        register_bot_commands(dp2, fresh_conn, s2, set())
        h2 = get_handler_map(dp2)

        msg = make_msg("/users")
        await h2["cmd_users"](msg, make_ctx())
        reply_text = msg.reply.call_args[0][0]
        assert "No registered users" in reply_text


async def test_users_with_username_shows_at_handle(conn, settings):
    """User with username → line shows '  {user_id} @{username}'."""
    _, h, _ = setup_dp(conn, settings)

    chat = MagicMock()
    chat.username = "kylych"

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = AsyncMock(return_value=chat)

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert f"  {OWNER_ID} @kylych" in reply_text


async def test_users_without_username_shows_id_only(conn, settings):
    """User with no username → line shows '  {user_id}' without @ handle."""
    _, h, _ = setup_dp(conn, settings)

    chat = MagicMock()
    chat.username = None

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = AsyncMock(return_value=chat)

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert f"  {OWNER_ID}" in reply_text
    assert "@" not in reply_text


async def test_users_get_chat_raises_shows_id_no_exception(conn, settings):
    """get_chat raising → line shows '  {user_id}' unchanged, no exception propagated."""
    _, h, _ = setup_dp(conn, settings)

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = AsyncMock(side_effect=Exception("Forbidden"))

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert f"  {OWNER_ID}" in reply_text


async def test_users_bot_none_shows_id_no_username(conn, settings):
    """message.bot is None → username_str stays empty, output is '  {user_id}{flag_str}'."""
    _, h, _ = setup_dp(conn, settings)

    msg = make_msg("/users")
    msg.bot = None

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert f"  {OWNER_ID}" in reply_text
    assert "@" not in reply_text


async def test_users_admin_flag_shown_with_username(conn, settings):
    """Admin flag appears correctly when username resolves successfully."""
    _, h, _ = setup_dp(conn, settings)

    chat = MagicMock()
    chat.username = "theadmin"

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = AsyncMock(return_value=chat)

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    # OWNER_ID is admin by default (init_db sets is_admin=True for owner)
    assert "admin" in reply_text
    assert "@theadmin" in reply_text


async def test_users_admin_flag_shown_when_get_chat_fails(conn, settings):
    """Admin flag still appears even when get_chat raises."""
    _, h, _ = setup_dp(conn, settings)

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = AsyncMock(side_effect=RuntimeError("network error"))

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "admin" in reply_text


async def test_users_banned_flag_shown_with_username(conn, settings):
    """Banned flag appears alongside username when user is banned."""
    from ahsoka.database import ban_user, get_or_create_user as _get_or_create_user
    # Create a second non-admin user and ban them
    await _get_or_create_user(conn, 77777)
    await ban_user(conn, 77777)

    _, h, _ = setup_dp(conn, settings)

    async def fake_get_chat(user_id):
        chat = MagicMock()
        chat.username = "banned_person" if user_id == 77777 else None
        return chat

    msg = make_msg_with_bot("/users")
    msg.bot.get_chat = fake_get_chat

    await h["cmd_users"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "banned" in reply_text
    assert "@banned_person" in reply_text


# ---------------------------------------------------------------------------
# /ban and /unban admin commands
# ---------------------------------------------------------------------------

TARGET_ID = 55555  # a non-owner user used in ban/unban tests


async def _setup_target_user(conn, target_id: int = TARGET_ID) -> None:
    """Insert target_id into the DB so get_user returns a real User row."""
    from ahsoka.database import get_or_create_user as _goc
    await _goc(conn, target_id)


async def test_ban_happy_path_calls_ban_user_and_replies_banned(conn, settings):
    """User exists → ban_user called, reply contains 'banned'."""
    from ahsoka.database import ban_user as _ban_user, get_user
    await _setup_target_user(conn)
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg(f"/ban {TARGET_ID}")
    await h["cmd_ban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "banned" in reply_text.lower()
    user = await get_user(conn, TARGET_ID)
    assert user is not None and user.is_banned


async def test_ban_user_not_found_replies_not_found_no_ban(conn, settings):
    """get_user returns None → reply contains 'not found', ban_user NOT called."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/ban 99999")
    with patch("ahsoka.bot.commands.db.ban_user", new_callable=AsyncMock) as mock_ban, \
         patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock, return_value=None):
        await h["cmd_ban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "not found" in reply_text.lower()
    mock_ban.assert_not_called()


async def test_ban_user_not_found_does_not_rebuild_keywords(conn, settings):
    """get_user returns None → _rebuild_keywords NOT called."""
    from unittest.mock import patch
    mock_index = MagicMock()
    mock_index.rebuild = AsyncMock()
    _, h, _ = setup_dp(conn, settings, keyword_index=mock_index)
    msg = make_msg("/ban 99999")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock, return_value=None):
        await h["cmd_ban"](msg, make_ctx())
    mock_index.rebuild.assert_not_called()


async def test_ban_bad_syntax_no_arg_replies_usage(conn, settings):
    """No argument → reply is 'Usage: /ban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/ban")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.ban_user", new_callable=AsyncMock) as mock_ban:
        await h["cmd_ban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /ban" in reply_text
    mock_get.assert_not_called()
    mock_ban.assert_not_called()


async def test_ban_bad_syntax_non_digit_replies_usage(conn, settings):
    """Non-digit argument → reply is 'Usage: /ban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/ban someuser")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.ban_user", new_callable=AsyncMock) as mock_ban:
        await h["cmd_ban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /ban" in reply_text
    mock_get.assert_not_called()
    mock_ban.assert_not_called()


async def test_ban_bad_syntax_too_many_args_replies_usage(conn, settings):
    """Two numeric arguments → reply is 'Usage: /ban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/ban 123 456")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.ban_user", new_callable=AsyncMock) as mock_ban:
        await h["cmd_ban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /ban" in reply_text
    mock_get.assert_not_called()
    mock_ban.assert_not_called()


async def test_unban_happy_path_calls_unban_user_and_replies_unbanned(conn, settings):
    """User exists and is banned → unban_user called, reply contains 'unbanned'."""
    from ahsoka.database import ban_user as _ban_user, get_user, unban_user as _unban_user
    await _setup_target_user(conn)
    await _ban_user(conn, TARGET_ID)
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg(f"/unban {TARGET_ID}")
    await h["cmd_unban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "unbanned" in reply_text.lower()
    user = await get_user(conn, TARGET_ID)
    assert user is not None and not user.is_banned


async def test_unban_user_not_found_replies_not_found_no_unban(conn, settings):
    """get_user returns None → reply contains 'not found', unban_user NOT called."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/unban 99999")
    with patch("ahsoka.bot.commands.db.unban_user", new_callable=AsyncMock) as mock_unban, \
         patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock, return_value=None):
        await h["cmd_unban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "not found" in reply_text.lower()
    mock_unban.assert_not_called()


async def test_unban_user_not_found_does_not_rebuild_keywords(conn, settings):
    """get_user returns None → _rebuild_keywords NOT called."""
    from unittest.mock import patch
    mock_index = MagicMock()
    mock_index.rebuild = AsyncMock()
    _, h, _ = setup_dp(conn, settings, keyword_index=mock_index)
    msg = make_msg("/unban 99999")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock, return_value=None):
        await h["cmd_unban"](msg, make_ctx())
    mock_index.rebuild.assert_not_called()


async def test_unban_bad_syntax_no_arg_replies_usage(conn, settings):
    """No argument → reply is 'Usage: /unban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/unban")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.unban_user", new_callable=AsyncMock) as mock_unban:
        await h["cmd_unban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /unban" in reply_text
    mock_get.assert_not_called()
    mock_unban.assert_not_called()


async def test_unban_bad_syntax_non_digit_replies_usage(conn, settings):
    """Non-digit argument → reply is 'Usage: /unban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/unban someuser")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.unban_user", new_callable=AsyncMock) as mock_unban:
        await h["cmd_unban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /unban" in reply_text
    mock_get.assert_not_called()
    mock_unban.assert_not_called()


async def test_unban_bad_syntax_too_many_args_replies_usage(conn, settings):
    """Two numeric arguments → reply is 'Usage: /unban <user_id>', no DB calls."""
    from unittest.mock import patch
    _, h, _ = setup_dp(conn, settings)
    msg = make_msg("/unban 123 456")
    with patch("ahsoka.bot.commands.db.get_user", new_callable=AsyncMock) as mock_get, \
         patch("ahsoka.bot.commands.db.unban_user", new_callable=AsyncMock) as mock_unban:
        await h["cmd_unban"](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "Usage: /unban" in reply_text
    mock_get.assert_not_called()
    mock_unban.assert_not_called()
