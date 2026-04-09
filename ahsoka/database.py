import logging

import aiosqlite

from ahsoka.models import User, UserConfig

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    url          TEXT    NOT NULL DEFAULT '',
    score        INTEGER,
    score_reason TEXT    NOT NULL DEFAULT '',
    apply_info   TEXT    NOT NULL DEFAULT '',
    scored_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_id, message_id, url)
);

CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    notify_chat_id INTEGER NOT NULL,
    is_admin       INTEGER NOT NULL DEFAULT 0,
    is_banned      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_config (
    user_id    INTEGER PRIMARY KEY REFERENCES users(user_id),
    stack      TEXT    NOT NULL DEFAULT '',
    seniority  TEXT    NOT NULL DEFAULT '',
    remote     TEXT    NOT NULL DEFAULT '',
    location   TEXT    NOT NULL DEFAULT '',
    salary_min TEXT    NOT NULL DEFAULT '',
    salary_max TEXT    NOT NULL DEFAULT '',
    threshold  INTEGER NOT NULL DEFAULT 7,
    paused     INTEGER NOT NULL DEFAULT 0,
    keywords   TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watched_channels (
    channel_id  INTEGER PRIMARY KEY,
    added_by    INTEGER REFERENCES users(user_id),
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_notified (
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    url        TEXT    NOT NULL DEFAULT '',
    sent_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, channel_id, message_id, url)
);
"""


async def _migrate_legacy_config(
    conn: aiosqlite.Connection, owner_chat_id: int
) -> None:
    """Migrate old key-value user_config table to new columnar schema."""
    # Check if old schema exists (key-value style)
    async with conn.execute("PRAGMA table_info(user_config)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if "key" not in columns:
        return  # already new schema or fresh DB

    logger.info("Migrating legacy user_config table to multi-user schema")

    # Read existing config
    async with conn.execute("SELECT key, value FROM user_config") as cur:
        data = {row[0]: row[1] for row in await cur.fetchall()}

    # Drop old table
    await conn.execute("DROP TABLE user_config")

    # Create new tables
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            notify_chat_id INTEGER NOT NULL,
            is_admin       INTEGER NOT NULL DEFAULT 0,
            is_banned      INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_config (
            user_id    INTEGER PRIMARY KEY REFERENCES users(user_id),
            stack      TEXT    NOT NULL DEFAULT '',
            seniority  TEXT    NOT NULL DEFAULT '',
            remote     TEXT    NOT NULL DEFAULT '',
            location   TEXT    NOT NULL DEFAULT '',
            salary_min TEXT    NOT NULL DEFAULT '',
            salary_max TEXT    NOT NULL DEFAULT '',
            threshold  INTEGER NOT NULL DEFAULT 7,
            paused     INTEGER NOT NULL DEFAULT 0,
            keywords   TEXT    NOT NULL DEFAULT '',
            updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_notified (
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            url        TEXT    NOT NULL DEFAULT '',
            sent_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, channel_id, message_id, url)
        );
    """)

    # Insert owner as admin
    await conn.execute(
        "INSERT OR IGNORE INTO users (user_id, notify_chat_id, is_admin) VALUES (?, ?, 1)",
        (owner_chat_id, owner_chat_id),
    )

    # Migrate config
    await conn.execute(
        """INSERT OR IGNORE INTO user_config
           (user_id, stack, seniority, remote, location, salary_min, salary_max,
            threshold, paused, keywords)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            owner_chat_id,
            data.get("stack", ""),
            data.get("seniority", ""),
            data.get("remote", ""),
            data.get("location", ""),
            data.get("salary_min", ""),
            data.get("salary_max", ""),
            int(data.get("threshold", "7")),
            1 if data.get("paused", "false").lower() == "true" else 0,
            data.get("keywords", ""),
        ),
    )
    await conn.commit()
    logger.info("Legacy config migrated for owner %d", owner_chat_id)


async def init_db(conn: aiosqlite.Connection, owner_chat_id: int = 0) -> None:
    # Migrate legacy schema first (before CREATE TABLE IF NOT EXISTS overwrites)
    try:
        await _migrate_legacy_config(conn, owner_chat_id)
    except aiosqlite.OperationalError:
        pass  # no user_config table yet — fresh DB

    await conn.executescript(_SCHEMA)

    # Add columns to seen_posts if upgrading from older schema
    for col, default in [
        ("url", "TEXT NOT NULL DEFAULT ''"),
        ("score_reason", "TEXT NOT NULL DEFAULT ''"),
        ("apply_info", "TEXT NOT NULL DEFAULT ''"),
    ]:
        try:
            await conn.execute(f"ALTER TABLE seen_posts ADD COLUMN {col} {default}")
            await conn.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists

    # Add added_by column to watched_channels if upgrading
    try:
        await conn.execute(
            "ALTER TABLE watched_channels ADD COLUMN added_by INTEGER REFERENCES users(user_id)"
        )
        await conn.commit()
    except aiosqlite.OperationalError:
        pass

    # Ensure owner exists as admin
    if owner_chat_id:
        await conn.execute(
            "INSERT OR IGNORE INTO users (user_id, notify_chat_id, is_admin) VALUES (?, ?, 1)",
            (owner_chat_id, owner_chat_id),
        )
        await conn.execute(
            "INSERT OR IGNORE INTO user_config (user_id) VALUES (?)",
            (owner_chat_id,),
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


async def add_channel(
    conn: aiosqlite.Connection, channel_id: int, added_by: int | None = None
) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO watched_channels (channel_id, added_by) VALUES (?, ?)",
        (channel_id, added_by),
    )
    await conn.commit()


async def remove_channel(conn: aiosqlite.Connection, channel_id: int) -> None:
    await conn.execute(
        "DELETE FROM watched_channels WHERE channel_id = ?",
        (channel_id,),
    )
    await conn.commit()


# --- Dedup ---


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
    score_reason: str = "",
    apply_info: str = "",
) -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO seen_posts
           (channel_id, message_id, url, score, score_reason, apply_info)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (channel_id, message_id, url, score, score_reason, apply_info),
    )
    await conn.commit()


# --- User management ---


async def get_or_create_user(
    conn: aiosqlite.Connection, user_id: int, is_admin: bool = False
) -> User:
    async with conn.execute(
        "SELECT user_id, notify_chat_id, is_admin, is_banned FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return User(
            user_id=row[0],
            notify_chat_id=row[1],
            is_admin=bool(row[2]),
            is_banned=bool(row[3]),
        )
    await conn.execute(
        "INSERT INTO users (user_id, notify_chat_id, is_admin) VALUES (?, ?, ?)",
        (user_id, user_id, int(is_admin)),
    )
    await conn.execute(
        "INSERT INTO user_config (user_id) VALUES (?)",
        (user_id,),
    )
    await conn.commit()
    return User(user_id=user_id, notify_chat_id=user_id, is_admin=is_admin)


async def get_user(conn: aiosqlite.Connection, user_id: int) -> User | None:
    async with conn.execute(
        "SELECT user_id, notify_chat_id, is_admin, is_banned FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return User(
        user_id=row[0],
        notify_chat_id=row[1],
        is_admin=bool(row[2]),
        is_banned=bool(row[3]),
    )


async def list_users(conn: aiosqlite.Connection) -> list[User]:
    async with conn.execute(
        "SELECT user_id, notify_chat_id, is_admin, is_banned FROM users ORDER BY created_at"
    ) as cur:
        rows = await cur.fetchall()
    return [
        User(user_id=r[0], notify_chat_id=r[1], is_admin=bool(r[2]), is_banned=bool(r[3]))
        for r in rows
    ]


async def ban_user(conn: aiosqlite.Connection, user_id: int) -> None:
    await conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
    await conn.commit()


async def unban_user(conn: aiosqlite.Connection, user_id: int) -> None:
    await conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
    await conn.commit()


async def set_notify_target(conn: aiosqlite.Connection, user_id: int, chat_id: int) -> None:
    await conn.execute(
        "UPDATE users SET notify_chat_id = ? WHERE user_id = ?",
        (chat_id, user_id),
    )
    await conn.commit()


# --- User config ---


async def get_user_config(conn: aiosqlite.Connection, user_id: int) -> UserConfig:
    async with conn.execute(
        """SELECT u.user_id, u.notify_chat_id,
                  c.stack, c.seniority, c.remote, c.location,
                  c.salary_min, c.salary_max, c.threshold, c.paused, c.keywords
           FROM users u JOIN user_config c ON u.user_id = c.user_id
           WHERE u.user_id = ?""",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return UserConfig(user_id=user_id, notify_chat_id=user_id)
    return UserConfig(
        user_id=row[0],
        notify_chat_id=row[1],
        stack=row[2],
        seniority=row[3],
        remote=row[4],
        location=row[5],
        salary_min=row[6],
        salary_max=row[7],
        threshold=row[8],
        paused=bool(row[9]),
        keywords=row[10],
    )


async def set_user_config(conn: aiosqlite.Connection, user_id: int, key: str, value: str) -> None:
    await conn.execute(
        f"UPDATE user_config SET {key} = ?, updated_at = datetime('now') WHERE user_id = ?",
        (value, user_id),
    )
    await conn.commit()


async def get_all_active_configs(conn: aiosqlite.Connection) -> list[UserConfig]:
    async with conn.execute(
        """SELECT u.user_id, u.notify_chat_id,
                  c.stack, c.seniority, c.remote, c.location,
                  c.salary_min, c.salary_max, c.threshold, c.paused, c.keywords
           FROM users u JOIN user_config c ON u.user_id = c.user_id
           WHERE u.is_banned = 0""",
    ) as cur:
        rows = await cur.fetchall()
    return [
        UserConfig(
            user_id=r[0], notify_chat_id=r[1],
            stack=r[2], seniority=r[3], remote=r[4], location=r[5],
            salary_min=r[6], salary_max=r[7], threshold=r[8],
            paused=bool(r[9]), keywords=r[10],
        )
        for r in rows
    ]


# --- Notification tracking ---


async def is_notified(
    conn: aiosqlite.Connection,
    user_id: int,
    channel_id: int,
    message_id: int,
    url: str = "",
) -> bool:
    async with conn.execute(
        """SELECT 1 FROM user_notified
           WHERE user_id = ? AND channel_id = ? AND message_id = ? AND url = ?""",
        (user_id, channel_id, message_id, url),
    ) as cur:
        return await cur.fetchone() is not None


async def mark_notified(
    conn: aiosqlite.Connection,
    user_id: int,
    channel_id: int,
    message_id: int,
    url: str = "",
) -> None:
    await conn.execute(
        """INSERT OR IGNORE INTO user_notified (user_id, channel_id, message_id, url)
           VALUES (?, ?, ?, ?)""",
        (user_id, channel_id, message_id, url),
    )
    await conn.commit()


# --- Cleanup ---


async def delete_old_posts(conn: aiosqlite.Connection, days: int = 30) -> int:
    cursor = await conn.execute(
        "DELETE FROM seen_posts WHERE scored_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    await conn.execute(
        "DELETE FROM user_notified WHERE sent_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    await conn.commit()
    return cursor.rowcount  # type: ignore[return-value]
