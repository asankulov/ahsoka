"""Tests for ahsoka/bot/log_handler.py"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ahsoka.bot.log_handler import TelegramLogHandler, TG_MESSAGE_LIMIT, _NOISY_PREFIXES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_handler(send_side_effect=None) -> tuple[TelegramLogHandler, AsyncMock]:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=send_side_effect)
    handler = TelegramLogHandler(bot=bot, chat_id=999)
    handler.setLevel(logging.WARNING)
    return handler, bot


def make_record(
    name: str = "ahsoka.pipeline",
    level: int = logging.WARNING,
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
    record = make_record(name="root", level=logging.WARNING)
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
    # Route through a real Logger so level comparison (Logger → Handler) is exercised
    test_logger = logging.getLogger("test.level_filter")
    test_logger.setLevel(logging.DEBUG)  # logger accepts all levels
    test_logger.addHandler(handler)
    try:
        test_logger.debug("should be filtered by handler level")
        await asyncio.sleep(0)
        bot.send_message.assert_not_awaited()
    finally:
        test_logger.removeHandler(handler)


async def test_critical_record_forwarded():
    handler, bot = make_handler()
    record = make_record(level=logging.CRITICAL)
    handler.handle(record)
    await asyncio.sleep(0)
    bot.send_message.assert_awaited_once()
