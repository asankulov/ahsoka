import logging

import aiosqlite
import pyrogram.utils
from pyrogram import Client

from ahsoka import database as db
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


async def warm_peer_cache(
    client: Client,
    watched_channels: set[int],
    conn: aiosqlite.Connection | None = None,
    channel_names: dict[int, str] | None = None,
    lost_channels: set[int] | None = None,
) -> None:
    """
    Iterate dialogs once so pyrogram caches peers (access hashes) for joined
    channels, persist their public names, and report watched channels the
    account is not a member of (seeded into `lost_channels`, one aggregated
    ERROR so the log bot alerts the owner). Never raises.
    """
    try:
        joined: list[int] = []
        names: dict[int, tuple[str | None, str | None]] = {}
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.id in watched_channels:
                joined.append(chat.id)
                names[chat.id] = (
                    getattr(chat, "username", None),
                    getattr(chat, "title", None),
                )
        not_joined = watched_channels - set(joined)

        if names and conn is not None:
            try:
                await db.update_channel_names(conn, names)
            except Exception:
                logger.warning("Failed to persist channel names", exc_info=True)
        if channel_names is not None:
            for cid, (username, title) in names.items():
                if username or title:
                    channel_names[cid] = db.format_channel_label(cid, username, title)

        if joined:
            logger.info("Confirmed member of: %s", joined)
        if not_joined:
            if lost_channels is not None:
                lost_channels.update(not_joined)
            labels = [
                (channel_names or {}).get(cid, str(cid)) for cid in sorted(not_joined)
            ]
            logger.error(
                "Session account is NOT a member of %d watched channel(s) "
                "(no updates will arrive): %s",
                len(labels),
                "; ".join(labels),
            )
    except Exception:
        logger.warning("Peer cache warm-up failed", exc_info=True)
