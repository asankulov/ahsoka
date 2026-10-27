import logging

import aiosqlite

from ahsoka import database as db

logger = logging.getLogger(__name__)


def supergroup_chat_id(raw_channel_id: int) -> int:
    """Bot-API style id (-100<id>) for a raw MTProto channel id."""
    return int(f"-100{raw_channel_id}")


async def migrate_watched_chat(
    conn: aiosqlite.Connection | None,
    watched_channels: set[int],
    old_id: int,
    new_id: int,
    channel_names: dict[int, str] | None = None,
    lost_channels: set[int] | None = None,
) -> bool:
    """Re-key a watched basic group after it migrated to a supergroup.

    Updates the DB row (if `conn`) and the shared in-memory structures. Idempotent:
    returns False when `old_id` is no longer watched (already migrated). The
    WARNING is forwarded to the owner by the log handler.
    """
    if old_id not in watched_channels:
        return False
    if conn is not None:
        await db.migrate_channel(conn, old_id, new_id)
    watched_channels.discard(old_id)
    watched_channels.add(new_id)
    if lost_channels is not None:
        lost_channels.discard(old_id)
    if channel_names is not None:
        label = channel_names.pop(old_id, None)
        if label is not None:
            channel_names[new_id] = label.replace(f"[{old_id}]", f"[{new_id}]")
    logger.warning(
        "Watched group %d migrated to supergroup %d; watchlist updated", old_id, new_id
    )
    return True
