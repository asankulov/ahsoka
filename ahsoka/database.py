import aiosqlite

from ahsoka.models import UserConfig

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    url         TEXT    NOT NULL DEFAULT '',
    score       INTEGER,
    scored_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_id, message_id, url)
);

CREATE TABLE IF NOT EXISTS user_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watched_channels (
    channel_id  INTEGER PRIMARY KEY,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_DEFAULT_CONFIG: dict[str, str] = {
    "stack": "",
    "seniority": "",
    "remote": "",
    "location": "",
    "salary_min": "",
    "salary_max": "",
    "threshold": "7",
    "paused": "false",
    "keywords": "",
}


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(_SCHEMA)
    try:
        await conn.execute("ALTER TABLE seen_posts ADD COLUMN url TEXT NOT NULL DEFAULT ''")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass  # column already exists
    await conn.executemany(
        "INSERT OR IGNORE INTO user_config (key, value) VALUES (?, ?)",
        list(_DEFAULT_CONFIG.items()),
    )
    await conn.commit()


async def seed_channels(conn: aiosqlite.Connection, channel_ids: list[int]) -> None:
    """Populate watched_channels from env var only if the table is empty."""
    async with conn.execute("SELECT COUNT(*) FROM watched_channels") as cur:
        (count,) = await cur.fetchone()  # type: ignore[misc]
    if count == 0 and channel_ids:
        await conn.executemany(
            "INSERT OR IGNORE INTO watched_channels (channel_id) VALUES (?)",
            [(cid,) for cid in channel_ids],
        )
        await conn.commit()


async def load_watched_channels(conn: aiosqlite.Connection) -> set[int]:
    async with conn.execute("SELECT channel_id FROM watched_channels") as cur:
        rows = await cur.fetchall()
    return {row[0] for row in rows}


async def add_channel(conn: aiosqlite.Connection, channel_id: int) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO watched_channels (channel_id) VALUES (?)",
        (channel_id,),
    )
    await conn.commit()


async def remove_channel(conn: aiosqlite.Connection, channel_id: int) -> None:
    await conn.execute(
        "DELETE FROM watched_channels WHERE channel_id = ?",
        (channel_id,),
    )
    await conn.commit()


async def is_seen(
    conn: aiosqlite.Connection,
    channel_id: int,
    message_id: int,
    url: str = "",
) -> bool:
    async with conn.execute(
        "SELECT 1 FROM seen_posts WHERE channel_id = ? AND message_id = ? AND url = ?",
        (channel_id, message_id, url),
    ) as cur:
        return await cur.fetchone() is not None


async def mark_seen(
    conn: aiosqlite.Connection,
    channel_id: int,
    message_id: int,
    score: int | None = None,
    url: str = "",
) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO seen_posts (channel_id, message_id, url, score) VALUES (?, ?, ?, ?)",
        (channel_id, message_id, url, score),
    )
    await conn.commit()


async def get_config(conn: aiosqlite.Connection) -> UserConfig:
    async with conn.execute("SELECT key, value FROM user_config") as cur:
        rows = await cur.fetchall()
    data = {row[0]: row[1] for row in rows}
    return UserConfig(
        stack=data.get("stack", ""),
        seniority=data.get("seniority", ""),
        remote=data.get("remote", ""),
        location=data.get("location", ""),
        salary_min=data.get("salary_min", ""),
        salary_max=data.get("salary_max", ""),
        threshold=int(data.get("threshold", "7")),
        paused=data.get("paused", "false").lower() == "true",
        keywords=data.get("keywords", ""),
    )


async def set_config(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO user_config (key, value) VALUES (?, ?)",
        (key, value),
    )
    await conn.commit()


async def delete_old_posts(conn: aiosqlite.Connection, days: int = 30) -> int:
    cursor = await conn.execute(
        "DELETE FROM seen_posts WHERE scored_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    await conn.commit()
    return cursor.rowcount  # type: ignore[return-value]
