"""Tests for bot command handlers in ahsoka/bot/commands.py.

Handlers are registered as closures inside register_bot_commands, so we extract
their callbacks directly from the aiogram Router and call them with mock Messages.
The owner-only middleware is bypassed intentionally — its logic is trivially simple
and is verified by a dedicated test below.
"""
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

from aiogram import Dispatcher

from ahsoka.bot.commands import register_bot_commands
from ahsoka.config import Settings
from ahsoka.database import get_config, init_db, load_watched_channels


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OWNER_ID = 12345


@pytest.fixture
def settings() -> MagicMock:
    s = MagicMock(spec=Settings)
    s.owner_chat_id = OWNER_ID
    return s


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


def make_msg(text: str) -> MagicMock:
    """Create a minimal mock aiogram Message."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    return msg


def get_handlers(dp: Dispatcher) -> list:
    """Return handler callbacks in registration order from the first sub-router."""
    router = dp.sub_routers[0]
    return [h.callback for h in router.message.handlers]


def setup_dp(conn, settings, watched=None) -> tuple[Dispatcher, list]:
    watched_channels: set[int] = watched if watched is not None else set()
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels)
    return dp, get_handlers(dp), watched_channels


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_includes_router(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    assert len(dp.sub_routers) == 1


def test_register_adds_expected_number_of_handlers(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    handlers = get_handlers(dp)
    # setstack, setseniority, setremote, setlocation, setsalary, setthreshold,
    # setkeywords, status, pause, resume, addchannel, removechannel, channels
    assert len(handlers) == 13


# ---------------------------------------------------------------------------
# /setstack
# ---------------------------------------------------------------------------

async def test_setstack_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[0](make_msg("/setstack python go"))
    assert (await get_config(conn)).stack == "python go"


async def test_setstack_clears_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[0](make_msg("/setstack python"))
    await handlers[0](make_msg("/setstack"))
    assert (await get_config(conn)).stack == ""


async def test_setstack_replies(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setstack rust")
    await handlers[0](msg)
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setseniority
# ---------------------------------------------------------------------------

async def test_setseniority_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[1](make_msg("/setseniority senior"))
    assert (await get_config(conn)).seniority == "senior"


# ---------------------------------------------------------------------------
# /setremote
# ---------------------------------------------------------------------------

async def test_setremote_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[2](make_msg("/setremote remote"))
    assert (await get_config(conn)).remote == "remote"


# ---------------------------------------------------------------------------
# /setlocation
# ---------------------------------------------------------------------------

async def test_setlocation_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[3](make_msg("/setlocation Berlin"))
    assert (await get_config(conn)).location == "Berlin"


# ---------------------------------------------------------------------------
# /setsalary
# ---------------------------------------------------------------------------

async def test_setsalary_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setsalary 3000 6000")
    await handlers[4](msg)
    cfg = await get_config(conn)
    assert cfg.salary_min == "3000"
    assert cfg.salary_max == "6000"
    msg.reply.assert_awaited_once()


async def test_setsalary_invalid_shows_usage(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setsalary notanumber")
    await handlers[4](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


async def test_setsalary_missing_args_shows_usage(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setsalary")
    await handlers[4](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


# ---------------------------------------------------------------------------
# /setthreshold
# ---------------------------------------------------------------------------

async def test_setthreshold_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setthreshold 5")
    await handlers[5](msg)
    assert (await get_config(conn)).threshold == 5


async def test_setthreshold_boundary_zero(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[5](make_msg("/setthreshold 0"))
    assert (await get_config(conn)).threshold == 0


async def test_setthreshold_boundary_ten(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[5](make_msg("/setthreshold 10"))
    assert (await get_config(conn)).threshold == 10


async def test_setthreshold_out_of_range_shows_usage(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setthreshold 11")
    await handlers[5](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


async def test_setthreshold_non_digit_shows_usage(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setthreshold high")
    await handlers[5](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


# ---------------------------------------------------------------------------
# /setkeywords
# ---------------------------------------------------------------------------

async def test_setkeywords_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python django fastapi"))
    assert (await get_config(conn)).keywords == "python django fastapi"


async def test_setkeywords_cleared_reply(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setkeywords")
    await handlers[6](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "cleared" in reply_text.lower() or "pass" in reply_text.lower()


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def test_status_reflects_current_config(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    watched.add(-100111)
    # Set some config first
    await handlers[0](make_msg("/setstack python"))  # setstack
    await handlers[5](make_msg("/setthreshold 6"))   # setthreshold

    msg = make_msg("/status")
    await handlers[7](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "python" in reply_text
    assert "6/10" in reply_text
    assert "-100111" in reply_text


# ---------------------------------------------------------------------------
# /pause and /resume
# ---------------------------------------------------------------------------

async def test_pause_sets_paused_flag(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[8](make_msg("/pause"))
    assert (await get_config(conn)).paused is True


async def test_resume_clears_paused_flag(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[8](make_msg("/pause"))
    await handlers[9](make_msg("/resume"))
    assert (await get_config(conn)).paused is False


# ---------------------------------------------------------------------------
# /addchannel and /removechannel
# ---------------------------------------------------------------------------

async def test_addchannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    msg = make_msg("/addchannel -100999")
    await handlers[10](msg)
    assert -100999 in watched
    channels = await load_watched_channels(conn)
    assert -100999 in channels


async def test_addchannel_invalid_id_replies_error(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    msg = make_msg("/addchannel notanid")
    await handlers[10](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "integer" in reply_text.lower()
    assert len(watched) == 0


async def test_addchannel_missing_arg_replies_usage(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    msg = make_msg("/addchannel")
    await handlers[10](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


async def test_removechannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    # Add first
    await handlers[10](make_msg("/addchannel -100999"))
    assert -100999 in watched
    # Now remove
    msg = make_msg("/removechannel -100999")
    await handlers[11](msg)
    assert -100999 not in watched
    channels = await load_watched_channels(conn)
    assert -100999 not in channels


async def test_removechannel_invalid_id_replies_error(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/removechannel notanid")
    await handlers[11](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "integer" in reply_text.lower()


async def test_removechannel_missing_arg_replies_usage(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/removechannel")
    await handlers[11](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "Usage" in reply_text


# ---------------------------------------------------------------------------
# /channels
# ---------------------------------------------------------------------------

async def test_channels_lists_watched(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    watched.update([-100111, -100222])
    msg = make_msg("/channels")
    await handlers[12](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "-100111" in reply_text
    assert "-100222" in reply_text


async def test_channels_empty_set(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/channels")
    await handlers[12](msg)
    reply_text = msg.reply.call_args[0][0]
    assert "No channels" in reply_text


# ---------------------------------------------------------------------------
# Owner-only middleware
# ---------------------------------------------------------------------------

async def test_owner_only_middleware_allows_owner(conn, settings):
    """Middleware should call handler when message is from the owner."""
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[0]

    # Extract the middleware callback
    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    owner_msg = MagicMock()
    owner_msg.from_user = MagicMock()
    owner_msg.from_user.id = OWNER_ID

    await middleware(fake_handler, owner_msg, {})
    assert called is True


async def test_owner_only_middleware_blocks_stranger(conn, settings):
    """Middleware should NOT call handler when message is from someone else."""
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[0]

    middleware = router.message.middleware._middlewares[0]

    called = False

    async def fake_handler(msg, data):
        nonlocal called
        called = True

    stranger_msg = MagicMock()
    stranger_msg.from_user = MagicMock()
    stranger_msg.from_user.id = 99999  # not the owner

    await middleware(fake_handler, stranger_msg, {})
    assert called is False
