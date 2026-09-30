import logging

import pyrogram.utils
from pyrogram import Client

from ahsoka.config import Settings

logger = logging.getLogger(__name__)

# Kurigram's value; pyrogram 2.0.x caps channel ids at 32 bits.
_MIN_CHANNEL_ID = -1007852516352


def patch_pyrogram_channel_id_limit() -> None:
    """
    Telegram channel ids now exceed 2**31, but pyrogram 2.0.x hardcodes
    MIN_CHANNEL_ID to a 32-bit cap, so get_peer_type() raises
    "ValueError: Peer id invalid" for newer channels. get_peer_type reads the
    module global at call time, so patching the attribute is enough. Idempotent.

    Fallback plan if this recurs: swap pyrogram for Kurigram.
    """
    pyrogram.utils.MIN_CHANNEL_ID = _MIN_CHANNEL_ID


def build_pyrogram_client(settings: Settings) -> Client:
    patch_pyrogram_channel_id_limit()
    return Client(
        name=settings.session_name,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )


async def warm_peer_cache(client: Client, watched_channels: set[int]) -> None:
    """
    Iterate dialogs once so pyrogram caches peers (access hashes) for joined
    channels, and log which watched channels the account is a member of.
    Never raises: failure only logs a warning.
    """
    try:
        joined: list[int] = []
        async for dialog in client.get_dialogs():
            if dialog.chat.id in watched_channels:
                joined.append(dialog.chat.id)
        not_joined = watched_channels - set(joined)
        if joined:
            logger.info("Confirmed member of: %s", joined)
        if not_joined:
            logger.warning("NOT a member of (won't receive updates): %s", not_joined)
    except Exception:
        logger.warning("Peer cache warm-up failed", exc_info=True)
