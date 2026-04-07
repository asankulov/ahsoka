import logging
import re

from pyrogram import Client

logger = logging.getLogger(__name__)

# https://t.me/username/123
_PUBLIC = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)$")
# https://t.me/c/1234567890/123  (private/invite-link channels)
_PRIVATE = re.compile(r"https?://t\.me/c/(\d+)/(\d+)$")


def is_tg_link(url: str) -> bool:
    """Return True if *url* is a Telegram channel-post deep link."""
    return bool(_PUBLIC.match(url) or _PRIVATE.match(url))


async def resolve_tg_link(url: str, client: Client) -> str | None:
    """Fetch the text of the Telegram message at *url* via the Pyrogram client.

    Returns the message text/caption, or None if the link is not a recognised
    channel-post URL, the message is inaccessible, or an error occurs.
    """
    m = _PUBLIC.match(url)
    if m:
        chat: str | int = m.group(1)
        msg_id = int(m.group(2))
    else:
        m = _PRIVATE.match(url)
        if not m:
            return None
        chat = int(f"-100{m.group(1)}")
        msg_id = int(m.group(2))

    try:
        msg = await client.get_messages(chat, msg_id)
        text: str = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        if text:
            logger.debug("Resolved %s: %d chars", url, len(text))
            return text
        return None
    except Exception as exc:
        logger.debug("TG resolve failed for %s: %s", url, exc)
        return None
