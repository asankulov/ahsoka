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

from ahsoka.bot.commands import register_bot_commands, WaitingForInput
from ahsoka.config import Settings
from ahsoka.database import get_config, init_db, load_watched_channels


# ---------------------------------------------------------------------------
# Fixtures and helpers
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


def make_ctx() -> MagicMock:
    """Create a minimal mock FSMContext."""
    ctx = MagicMock()
    ctx.set_state = AsyncMock()
    ctx.clear = AsyncMock()
    ctx.get_state = AsyncMock(return_value=None)
    return ctx


def get_handlers(dp: Dispatcher) -> list:
    """Return handler callbacks in registration order from the first sub-router."""
    router = dp.sub_routers[0]
    return [h.callback for h in router.message.handlers]


def setup_dp(conn, settings, watched=None):
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
    # 15 command handlers + 10 state-input handlers
    assert len(handlers) == 25


# ---------------------------------------------------------------------------
# /setstack  (index 0 — command,  15 — state input)
# ---------------------------------------------------------------------------

async def test_setstack_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[0](make_msg("/setstack python go"), make_ctx())
    assert (await get_config(conn)).stack == "python go"


async def test_setstack_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("/setstack")
    await handlers[0](msg, ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setstack)
    msg.reply.assert_awaited_once()
    assert (await get_config(conn)).stack == ""  # no db write yet


async def test_setstack_clears_pending_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[0](make_msg("/setstack rust"), ctx)
    ctx.clear.assert_awaited_once()


async def test_setstack_replies(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setstack rust")
    await handlers[0](msg, make_ctx())
    msg.reply.assert_awaited_once()


async def test_input_setstack_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[15](make_msg("python go"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).stack == "python go"


async def test_input_setstack_empty_clears(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[15](make_msg(""), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).stack == ""


# ---------------------------------------------------------------------------
# /setseniority  (1 / 16)
# ---------------------------------------------------------------------------

async def test_setseniority_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[1](make_msg("/setseniority senior"), make_ctx())
    assert (await get_config(conn)).seniority == "senior"


async def test_setseniority_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[1](make_msg("/setseniority"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setseniority)


async def test_input_setseniority_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[16](make_msg("lead"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).seniority == "lead"


# ---------------------------------------------------------------------------
# /setremote  (2 / 17)
# ---------------------------------------------------------------------------

async def test_setremote_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[2](make_msg("/setremote remote"), make_ctx())
    assert (await get_config(conn)).remote == "remote"


async def test_setremote_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[2](make_msg("/setremote"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setremote)


async def test_input_setremote_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[17](make_msg("hybrid"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).remote == "hybrid"


# ---------------------------------------------------------------------------
# /setlocation  (3 / 18)
# ---------------------------------------------------------------------------

async def test_setlocation_persists_value(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[3](make_msg("/setlocation Berlin"), make_ctx())
    assert (await get_config(conn)).location == "Berlin"


async def test_setlocation_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[3](make_msg("/setlocation"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setlocation)


async def test_input_setlocation_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[18](make_msg("Amsterdam"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).location == "Amsterdam"


# ---------------------------------------------------------------------------
# /setsalary  (4 / 19)
# ---------------------------------------------------------------------------

async def test_setsalary_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/setsalary 3000 6000")
    await handlers[4](msg, make_ctx())
    cfg = await get_config(conn)
    assert cfg.salary_min == "3000"
    assert cfg.salary_max == "6000"
    msg.reply.assert_awaited_once()


async def test_setsalary_prompts_when_invalid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[4](make_msg("/setsalary notanumber"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setsalary)


async def test_setsalary_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[4](make_msg("/setsalary"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setsalary)


async def test_input_setsalary_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[19](make_msg("2000 5000"), ctx)
    ctx.clear.assert_awaited_once()
    cfg = await get_config(conn)
    assert cfg.salary_min == "2000"
    assert cfg.salary_max == "5000"


async def test_input_setsalary_invalid_stays_in_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("three thousand")
    await handlers[19](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setthreshold  (5 / 20)
# ---------------------------------------------------------------------------

async def test_setthreshold_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[5](make_msg("/setthreshold 5"), make_ctx())
    assert (await get_config(conn)).threshold == 5


async def test_setthreshold_boundary_zero(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[5](make_msg("/setthreshold 0"), make_ctx())
    assert (await get_config(conn)).threshold == 0


async def test_setthreshold_boundary_ten(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[5](make_msg("/setthreshold 10"), make_ctx())
    assert (await get_config(conn)).threshold == 10


async def test_setthreshold_prompts_when_out_of_range(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[5](make_msg("/setthreshold 11"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setthreshold)


async def test_setthreshold_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[5](make_msg("/setthreshold"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setthreshold)


async def test_input_setthreshold_valid(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[20](make_msg("7"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).threshold == 7


async def test_input_setthreshold_invalid_stays_in_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("eleven")
    await handlers[20](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


async def test_input_setthreshold_out_of_range_stays_in_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("99")
    await handlers[20](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setkeywords  (6 / 21)
# ---------------------------------------------------------------------------

async def test_setkeywords_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python django fastapi"), make_ctx())
    assert (await get_config(conn)).keywords == "python django fastapi"


async def test_setkeywords_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[6](make_msg("/setkeywords"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.setkeywords)


async def test_input_setkeywords_persists(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[21](make_msg("rust go wasm"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).keywords == "rust go wasm"


# ---------------------------------------------------------------------------
# /addkeyword  (7 / 22)
# ---------------------------------------------------------------------------

async def test_addkeyword_appends_to_empty(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[7](make_msg("/addkeyword python django"), make_ctx())
    assert (await get_config(conn)).keywords == "python django"


async def test_addkeyword_appends_to_existing(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python"), make_ctx())
    await handlers[7](make_msg("/addkeyword fastapi"), make_ctx())
    assert (await get_config(conn)).keywords == "python fastapi"


async def test_addkeyword_deduplicates(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python django"), make_ctx())
    await handlers[7](make_msg("/addkeyword django fastapi"), make_ctx())
    assert (await get_config(conn)).keywords == "python django fastapi"


async def test_addkeyword_all_duplicates_reply(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python"), make_ctx())
    msg = make_msg("/addkeyword python")
    await handlers[7](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "duplicate" in reply_text.lower() or "no new" in reply_text.lower()


async def test_addkeyword_preserves_order(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords c b a"), make_ctx())
    await handlers[7](make_msg("/addkeyword d"), make_ctx())
    assert (await get_config(conn)).keywords == "c b a d"


async def test_addkeyword_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[7](make_msg("/addkeyword"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.addkeyword)


async def test_input_addkeyword_appends(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    await handlers[22](make_msg("fastapi"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).keywords == "python fastapi"


async def test_input_addkeyword_deduplicates(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    await handlers[22](make_msg("python django"), ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).keywords == "python django"


async def test_input_addkeyword_empty_does_nothing(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python"), make_ctx())
    ctx = make_ctx()
    msg = make_msg("")
    await handlers[22](msg, ctx)
    ctx.clear.assert_awaited_once()
    assert (await get_config(conn)).keywords == "python"  # unchanged


# ---------------------------------------------------------------------------
# /resetkeywords  (8)
# ---------------------------------------------------------------------------

async def test_resetkeywords_clears_keywords(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[6](make_msg("/setkeywords python django"), make_ctx())
    await handlers[8](make_msg("/resetkeywords"), make_ctx())
    assert (await get_config(conn)).keywords == ""


async def test_resetkeywords_reply_mentions_cleared(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/resetkeywords")
    await handlers[8](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "cleared" in reply_text.lower() or "pass" in reply_text.lower()


async def test_resetkeywords_idempotent(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[8](make_msg("/resetkeywords"), make_ctx())
    await handlers[8](make_msg("/resetkeywords"), make_ctx())
    assert (await get_config(conn)).keywords == ""


# ---------------------------------------------------------------------------
# /status  (9)
# ---------------------------------------------------------------------------

async def test_status_reflects_current_config(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    watched.add(-100111)
    await handlers[0](make_msg("/setstack python"), make_ctx())
    await handlers[5](make_msg("/setthreshold 6"), make_ctx())

    msg = make_msg("/status")
    await handlers[9](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "python" in reply_text
    assert "6/10" in reply_text
    assert "-100111" in reply_text


async def test_status_cancels_waiting_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[9](make_msg("/status"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# /pause and /resume  (10, 11)
# ---------------------------------------------------------------------------

async def test_pause_sets_paused_flag(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[10](make_msg("/pause"), make_ctx())
    assert (await get_config(conn)).paused is True


async def test_resume_clears_paused_flag(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    await handlers[10](make_msg("/pause"), make_ctx())
    await handlers[11](make_msg("/resume"), make_ctx())
    assert (await get_config(conn)).paused is False


async def test_pause_cancels_waiting_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[10](make_msg("/pause"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# /addchannel and /removechannel  (12, 13 / 23, 24)
# ---------------------------------------------------------------------------

async def test_addchannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    msg = make_msg("/addchannel -100999")
    await handlers[12](msg, make_ctx())
    assert -100999 in watched
    channels = await load_watched_channels(conn)
    assert -100999 in channels


async def test_addchannel_invalid_id_replies_error(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    msg = make_msg("/addchannel notanid")
    await handlers[12](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "integer" in reply_text.lower()
    assert len(watched) == 0


async def test_addchannel_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[12](make_msg("/addchannel"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.addchannel)


async def test_removechannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    await handlers[12](make_msg("/addchannel -100999"), make_ctx())
    assert -100999 in watched
    msg = make_msg("/removechannel -100999")
    await handlers[13](msg, make_ctx())
    assert -100999 not in watched
    channels = await load_watched_channels(conn)
    assert -100999 not in channels


async def test_removechannel_invalid_id_replies_error(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/removechannel notanid")
    await handlers[13](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "integer" in reply_text.lower()


async def test_removechannel_prompts_when_no_arg(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[13](make_msg("/removechannel"), ctx)
    ctx.set_state.assert_awaited_once_with(WaitingForInput.removechannel)


async def test_input_addchannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[23](make_msg("-100777"), ctx)
    ctx.clear.assert_awaited_once()
    assert -100777 in watched
    assert -100777 in await load_watched_channels(conn)


async def test_input_addchannel_invalid_stays_in_state(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("notanid")
    await handlers[23](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()
    assert len(watched) == 0


async def test_input_removechannel_valid(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    await handlers[12](make_msg("/addchannel -100777"), make_ctx())
    ctx = make_ctx()
    await handlers[24](make_msg("-100777"), ctx)
    ctx.clear.assert_awaited_once()
    assert -100777 not in watched


async def test_input_removechannel_invalid_stays_in_state(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    msg = make_msg("notanid")
    await handlers[24](msg, ctx)
    ctx.clear.assert_not_called()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# /channels  (14)
# ---------------------------------------------------------------------------

async def test_channels_lists_watched(conn, settings):
    _, handlers, watched = setup_dp(conn, settings)
    watched.update([-100111, -100222])
    msg = make_msg("/channels")
    await handlers[14](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "-100111" in reply_text
    assert "-100222" in reply_text


async def test_channels_empty_set(conn, settings):
    _, handlers, _ = setup_dp(conn, settings)
    msg = make_msg("/channels")
    await handlers[14](msg, make_ctx())
    reply_text = msg.reply.call_args[0][0]
    assert "No channels" in reply_text


# ---------------------------------------------------------------------------
# State cancellation — any command clears a pending wait state
# ---------------------------------------------------------------------------

async def test_any_command_cancels_pending_state(conn, settings):
    """Sending /status while waiting for /setsalary input should clear the state."""
    _, handlers, _ = setup_dp(conn, settings)
    ctx = make_ctx()
    await handlers[9](make_msg("/status"), ctx)
    ctx.clear.assert_awaited_once()


# ---------------------------------------------------------------------------
# Owner-only middleware
# ---------------------------------------------------------------------------

async def test_owner_only_middleware_allows_owner(conn, settings):
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, set())
    router = dp.sub_routers[0]
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
    stranger_msg.from_user.id = 99999

    await middleware(fake_handler, stranger_msg, {})
    assert called is False
