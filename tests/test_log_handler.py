"""Tests for ahsoka/bot/log_handler.py"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ahsoka.bot.log_handler import (
    TelegramLogHandler, TG_MESSAGE_LIMIT, _NOISY_PREFIXES, _should_forward,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_handler(send_side_effect=None) -> tuple[TelegramLogHandler, AsyncMock]:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=send_side_effect)
    handler = TelegramLogHandler(bot=bot, chat_id=999)
    handler.setLevel(logging.ERROR)
    return handler, bot


def make_record(
    name: str = "ahsoka.pipeline",
    level: int = logging.ERROR,
    msg: str = "Something went wrong",
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_handler_is_logging_handler():
    handler, _ = make_handler()
    assert isinstance(handler, logging.Handler)


def test_handler_stores_chat_id():
    handler, _ = make_handler()
    assert handler._chat_id == 999


# ---------------------------------------------------------------------------
# emit() — happy path
# ---------------------------------------------------------------------------

async def test_emit_schedules_send_task():
    handler, bot = make_handler()
    record = make_record()
    handler.emit(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args[0][0] == 999  # chat_id


async def test_emit_includes_message_text():
    handler, bot = make_handler()
    record = make_record(msg="Pipeline failure for channel 123")
    handler.emit(record)
    await asyncio.sleep(0)
    sent_text = bot.send_message.call_args[0][1]
    assert "Pipeline failure for channel 123" in sent_text


# ---------------------------------------------------------------------------
# Noisy-logger filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("logger_name", [
    "aiogram",
    "aiogram.dispatcher.event.simple",
    "aiogram.client.session.aiohttp",
    "pyrogram",
    "pyrogram.connection.connection",
    "httpx",
    "httpcore._sync.http11",
])
async def test_noisy_loggers_are_silenced(logger_name):
    handler, bot = make_handler()
    record = make_record(name=logger_name, level=logging.ERROR)
    handler.emit(record)
    await asyncio.sleep(0)
    bot.send_message.assert_not_awaited()


async def test_non_noisy_logger_is_forwarded():
    handler, bot = make_handler()
    record = make_record(name="ahsoka.main", level=logging.ERROR)
    handler.emit(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()


async def test_root_logger_record_is_forwarded():
    handler, bot = make_handler()
    record = make_record(name="root", level=logging.ERROR)
    handler.emit(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

async def test_message_truncated_to_4096_chars():
    handler, bot = make_handler()
    record = make_record(msg="x" * 5000)
    handler.emit(record)
    await asyncio.sleep(0)
    sent_text = bot.send_message.call_args[0][1]
    assert len(sent_text) <= TG_MESSAGE_LIMIT


async def test_truncated_message_ends_with_original_tail():
    handler, bot = make_handler()
    tail = "TAIL" * 125  # 500 distinctive chars
    record = make_record(msg="x" * 5000 + tail)
    handler.emit(record)
    await asyncio.sleep(0)
    sent_text = bot.send_message.call_args[0][1]
    assert sent_text.endswith(tail)


async def test_short_message_not_truncated():
    handler, bot = make_handler()
    record = make_record(msg="short")
    handler.emit(record)
    await asyncio.sleep(0)
    sent_text = bot.send_message.call_args[0][1]
    assert "short" in sent_text
    assert not sent_text.startswith("...")


# ---------------------------------------------------------------------------
# Error resilience — send failure must NOT raise
# ---------------------------------------------------------------------------

async def test_send_failure_does_not_raise():
    handler, bot = make_handler(send_side_effect=Exception("Network error"))
    record = make_record()
    handler.emit(record)
    await asyncio.sleep(0)  # task runs; exception is swallowed


async def test_send_failure_calls_handle_error():
    handler, bot = make_handler(send_side_effect=Exception("Boom"))
    record = make_record()
    with patch.object(handler, "handleError") as mock_handle_error:
        handler.emit(record)
        await asyncio.sleep(0)
    mock_handle_error.assert_called_once()


# ---------------------------------------------------------------------------
# No running loop — emit must not raise
# ---------------------------------------------------------------------------

def test_emit_outside_event_loop_does_not_raise():
    """emit() called from synchronous context (no running loop) is a no-op."""
    handler, _ = make_handler()
    handler.emit(make_record())  # must not raise


# ---------------------------------------------------------------------------
# Level filtering via handle()
# ---------------------------------------------------------------------------

async def test_debug_record_not_forwarded_by_handler_level():
    handler, bot = make_handler()
    # Route through a real Logger so level comparison (Logger → Handler) is exercised.
    # Handler is set to ERROR; DEBUG is well below that threshold.
    test_logger = logging.getLogger("test.level_filter")
    test_logger.setLevel(logging.DEBUG)  # logger accepts all levels
    test_logger.addHandler(handler)
    try:
        test_logger.debug("should be filtered by handler level")
        await asyncio.sleep(0)
        bot.send_message.assert_not_awaited()
    finally:
        test_logger.removeHandler(handler)


async def test_warning_record_not_forwarded_by_handler_level():
    """WARNING is below the ERROR threshold — must NOT be forwarded."""
    handler, bot = make_handler()
    test_logger = logging.getLogger("test.warning_filter")
    test_logger.setLevel(logging.DEBUG)  # logger accepts all levels
    test_logger.addHandler(handler)
    try:
        test_logger.warning("should be filtered — below ERROR threshold")
        await asyncio.sleep(0)
        bot.send_message.assert_not_awaited()
    finally:
        test_logger.removeHandler(handler)


async def test_error_record_forwarded():
    """ERROR is exactly at the handler threshold — must be forwarded."""
    handler, bot = make_handler()
    record = make_record(level=logging.ERROR)
    handler.handle(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()


async def test_critical_record_forwarded():
    handler, bot = make_handler()
    record = make_record(level=logging.CRITICAL)
    handler.handle(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# WARNING allowlist (ahsoka.repost) gating in emit()
# ---------------------------------------------------------------------------

def make_warning_handler() -> tuple[TelegramLogHandler, AsyncMock]:
    """Handler configured as in main(): level WARNING, emit() does the gating."""
    handler, bot = make_handler()
    handler.setLevel(logging.WARNING)
    return handler, bot


async def test_emit_error_from_non_noisy_logger_forwarded_at_warning_level():
    handler, bot = make_warning_handler()
    handler.handle(make_record(name="ahsoka.main", level=logging.ERROR))
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize("name", ["ahsoka.main", "ahsoka.pipeline", "ahsoka.repost.sub", "ahsoka.reposted", "ahsoka"])
async def test_emit_warning_from_non_allowlisted_logger_not_forwarded(name):
    handler, bot = make_warning_handler()
    handler.handle(make_record(name=name, level=logging.WARNING))
    await asyncio.sleep(0)
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR])
async def test_emit_warning_or_higher_from_repost_logger_forwarded(level):
    handler, bot = make_warning_handler()
    handler.handle(make_record(name="ahsoka.repost", level=level, msg="Repost candidate"))
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()
    assert "Repost candidate" in bot.send_message.call_args[0][1]


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_emit_below_warning_from_repost_logger_not_forwarded(level):
    # Bypass handle()'s level check: emit() itself must still gate.
    handler, bot = make_warning_handler()
    handler.emit(make_record(name="ahsoka.repost", level=level))
    await asyncio.sleep(0)
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("name", ["aiogram.x", "pyrogram.connection", "httpx"])
async def test_emit_error_from_noisy_prefix_still_dropped(name):
    handler, bot = make_warning_handler()
    handler.handle(make_record(name=name, level=logging.ERROR))
    await asyncio.sleep(0)
    bot.send_message.assert_not_awaited()


async def test_emit_warning_from_noisy_logger_not_forwarded():
    """Noisy-prefix check runs first: a noisy WARNING is never forwarded."""
    handler, bot = make_warning_handler()
    handler.handle(make_record(name="aiogram.dispatcher", level=logging.WARNING))
    await asyncio.sleep(0)
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "name,level,expected",
    [
        ("ahsoka.repost", logging.WARNING, True),
        ("ahsoka.repost", logging.ERROR, True),
        ("ahsoka.repost", logging.INFO, False),
        ("ahsoka.repost", logging.DEBUG, False),
        ("ahsoka.repost.sub", logging.WARNING, False),
        ("ahsoka.reposted", logging.WARNING, False),
        ("ahsoka.main", logging.WARNING, False),
        ("ahsoka.main", logging.ERROR, True),
        ("ahsoka.main", logging.CRITICAL, True),
    ],
)
def test_should_forward_matrix(name, level, expected):
    assert _should_forward(make_record(name=name, level=level)) is expected
