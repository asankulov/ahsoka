import aiosqlite

from ahsoka.database import is_seen
from ahsoka.models import Post


async def is_duplicate(conn: aiosqlite.Connection, post: Post, url: str = "") -> bool:
    return await is_seen(conn, post.channel_id, post.message_id, url)
