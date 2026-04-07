"""TelegramLogHandler — forwards WARNING+ log records to a dedicated Telegram bot."""
import asyncio
import logging

from aiogram import Bot

# High-frequency internal loggers — suppress to avoid flooding the owner.
_NOISY_PREFIXES: tuple[str, ...] = (
    "aiogram",
    "pyrogram",
    "httpx",
    "httpcore",
)

TG_MESSAGE_LIMIT = 4096


class TelegramLogHandler(logging.Handler):
    """Async-safe handler that sends log records to a Telegram chat.

    emit() schedules bot.send_message() as a fire-and-forget asyncio Task on
    the running event loop. Never raises — errors go to stderr via handleError(),
    not back through the logger (prevents infinite recursion).
    """

    def __init__(self, bot: Bot, chat_id: int) -> None:
        super().__init__()
        self._bot = bot
        self._chat_id = chat_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(_NOISY_PREFIXES):
            return
        try:
            text = self._render(record)
            loop = asyncio.get_running_loop()
            loop.create_task(self._send(text))
        except RuntimeError:
            pass  # No running loop (tests / import time) — silently skip
        except Exception:
            self.handleError(record)

    def _render(self, record: logging.LogRecord) -> str:
        text = self.format(record)
        if len(text) > TG_MESSAGE_LIMIT:
            text = "..." + text[-(TG_MESSAGE_LIMIT - 3):]
        return text

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(self._chat_id, text)
        except Exception:
            # Use a synthetic record to avoid routing through emit() again
            self.handleError(logging.makeLogRecord({"msg": text}))
