import json
import logging
from datetime import datetime, timezone
from typing import Literal

import aiosqlite

from ahsoka.pipeline.costs import cost_for
from ahsoka.models import NotifiedPost, PersonalizedVerdict, User, UserConfig

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
    stack_tags   TEXT    NOT NULL DEFAULT '',
    seniority    TEXT    NOT NULL DEFAULT '',
    remote       TEXT    NOT NULL DEFAULT '',
    red_flags    TEXT    NOT NULL DEFAULT '',
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
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    username    TEXT,
    title       TEXT
);

CREATE TABLE IF NOT EXISTS user_notified (
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    url        TEXT    NOT NULL DEFAULT '',
    sent_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, channel_id, message_id, url)
);

CREATE TABLE IF NOT EXISTS post_verdicts (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    score      INTEGER NOT NULL,
    reason     TEXT    NOT NULL,
    matched    INTEGER NOT NULL,
    apply      TEXT,
    red_flags  TEXT,
    scored_at  TEXT    NOT NULL,
    PRIMARY KEY (channel_id, message_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_post_verdicts_user ON post_verdicts(user_id, scored_at);

CREATE TABLE IF NOT EXISTS pending_batches (
    batch_id     TEXT PRIMARY KEY,
    submitted_at TEXT NOT NULL,
    status       TEXT NOT NULL,
    request_map  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_usage (
    batch_id                    TEXT PRIMARY KEY,
    model                       TEXT NOT NULL,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    succeeded                   INTEGER NOT NULL DEFAULT 0,
    recorded_at                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_usage (
    day      TEXT NOT NULL,
    model    TEXT NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS cost_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    key       TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);

CREATE TABLE IF NOT EXISTS setting_snapshot (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    day      TEXT PRIMARY KEY,
    users    INTEGER NOT NULL,
    channels INTEGER NOT NULL,
    keywords INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_reconciliation (
    day           TEXT PRIMARY KEY,
    billed_usd    REAL NOT NULL,
    estimated_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS post_embeddings (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    vector     BLOB    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_post_embeddings_created ON post_embeddings(created_at);
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
        ("stack_tags", "TEXT NOT NULL DEFAULT ''"),
        ("seniority", "TEXT NOT NULL DEFAULT ''"),
        ("remote", "TEXT NOT NULL DEFAULT ''"),
        ("red_flags", "TEXT NOT NULL DEFAULT ''"),
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

    # Add username/title columns to watched_channels if upgrading
    for col in ("username", "title"):
        try:
            await conn.execute(f"ALTER TABLE watched_channels ADD COLUMN {col} TEXT")
            await conn.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists

    # Add cost columns to batch_usage if upgrading (guarded: idempotent)
    async with conn.execute("PRAGMA table_info(batch_usage)") as cur:
        usage_cols = {row[1] for row in await cur.fetchall()}
    if "cost_usd" not in usage_cols:
        await conn.execute("ALTER TABLE batch_usage ADD COLUMN cost_usd REAL")
    if "estimated" not in usage_cols:
        await conn.execute(
            "ALTER TABLE batch_usage ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0"
        )
    await conn.commit()

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

    await backfill_batch_costs(conn)


async def backfill_batch_costs(conn: aiosqlite.Connection) -> int:
    """Price pre-existing batch_usage rows (cost_usd IS NULL). Returns rows updated.

    Priced rows get estimated=1; unknown-model rows stay NULL. Idempotent.
    """
    async with conn.execute(
        """SELECT batch_id, model, input_tokens, output_tokens,
                  cache_creation_input_tokens, cache_read_input_tokens
           FROM batch_usage WHERE cost_usd IS NULL"""
    ) as cur:
        rows = await cur.fetchall()
    updated = 0
    for batch_id, model, inp, out, cw, cr in rows:
        cost = cost_for(model, inp, out, cw, cr)
        if cost is None:
            continue
        await conn.execute(
            "UPDATE batch_usage SET cost_usd = ?, estimated = 1 WHERE batch_id = ?",
            (cost, batch_id),
        )
        updated += 1
    await conn.commit()
    return updated


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


def format_channel_label(
    channel_id: int, username: str | None = None, title: str | None = None
) -> str:
    """Return "Title (@username) [id]", dropping missing parts; id is always present."""
    parts = []
    if title:
        parts.append(title)
    if username:
        parts.append(f"(@{username.lstrip('@')})")
    if not parts:
        return str(channel_id)
    parts.append(f"[{channel_id}]")
    return " ".join(parts)


async def add_channel(
    conn: aiosqlite.Connection,
    channel_id: int,
    added_by: int | None = None,
    username: str | None = None,
    title: str | None = None,
) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO watched_channels (channel_id, added_by, username, title) "
        "VALUES (?, ?, ?, ?)",
        (channel_id, added_by, username, title),
    )
    await conn.commit()


async def update_channel_names(
    conn: aiosqlite.Connection,
    names: dict[int, tuple[str | None, str | None]],
) -> None:
    """Persist (username, title) for watched channels. Rows with both None are skipped."""
    rows = [
        (username, title, channel_id)
        for channel_id, (username, title) in names.items()
        if username or title
    ]
    if rows:
        await conn.executemany(
            "UPDATE watched_channels SET username = ?, title = ? WHERE channel_id = ?",
            rows,
        )
        await conn.commit()


async def load_channel_names(conn: aiosqlite.Connection) -> dict[int, str]:
    """Map channel_id -> display label for every watched channel."""
    async with conn.execute(
        "SELECT channel_id, username, title FROM watched_channels"
    ) as cur:
        rows = await cur.fetchall()
    return {cid: format_channel_label(cid, username, title) for cid, username, title in rows}


async def migrate_channel(conn: aiosqlite.Connection, old_id: int, new_id: int) -> bool:
    """Re-key a watched chat (basic group -> supergroup), keeping its metadata.

    Idempotent: returns False (no changes) if `old_id` is not in the table;
    `INSERT OR IGNORE` keeps an already-present `new_id` row untouched.
    """
    async with conn.execute(
        "SELECT 1 FROM watched_channels WHERE channel_id = ?", (old_id,)
    ) as cur:
        if await cur.fetchone() is None:
            return False
    await conn.execute(
        "INSERT OR IGNORE INTO watched_channels (channel_id, added_by, added_at, username, title) "
        "SELECT ?, added_by, added_at, username, title FROM watched_channels WHERE channel_id = ?",
        (new_id, old_id),
    )
    await conn.execute("DELETE FROM watched_channels WHERE channel_id = ?", (old_id,))
    await conn.commit()
    return True


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
    stack_tags: str = "",
    seniority: str = "",
    remote: str = "",
    red_flags: str = "",
) -> bool:
    """Claim (channel_id, message_id, url) as seen.

    Returns True if this call actually inserted the row (i.e. won the claim),
    False if a row already existed (another caller already claimed it). This
    return value is the authoritative guard against concurrent
    pipeline_worker tasks double-processing the same post.
    """
    cursor = await conn.execute(
        """INSERT OR IGNORE INTO seen_posts
           (channel_id, message_id, url, score, score_reason, apply_info,
            stack_tags, seniority, remote, red_flags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (channel_id, message_id, url, score, score_reason, apply_info,
         stack_tags, seniority, remote, red_flags),
    )
    await conn.commit()
    return cursor.rowcount > 0


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


async def get_notify_chat_ids(conn: aiosqlite.Connection) -> set[int]:
    """Distinct notification target chat ids across all users (DMs included)."""
    async with conn.execute("SELECT DISTINCT notify_chat_id FROM users") as cur:
        rows = await cur.fetchall()
    return {r[0] for r in rows}


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
                  c.salary_min, c.salary_max, c.threshold, c.paused, c.keywords,
                  u.is_banned
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
            is_banned=bool(r[11]),
        )
        for r in rows
    ]


async def is_user_banned(conn: aiosqlite.Connection, user_id: int) -> bool:
    """Return True if the user exists and has is_banned = 1."""
    async with conn.execute(
        "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return bool(row and row[0])


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


# --- Export ---


async def get_notified_posts(
    conn: aiosqlite.Connection,
    user_id: int | None,
    start: datetime,
    end: datetime,
) -> list[NotifiedPost]:
    """Return notified posts (joined with stored verdicts) in [start, end) UTC.

    One row per (user_id, channel_id, message_id): user_notified rows for the
    same post are collapsed via GROUP BY (a post can have multiple url variants
    stored — see main.py's mark_notified call — so we take the earliest sent_at
    and concatenate the non-empty urls, which are unique per post via the
    table's UNIQUE(user_id, channel_id, message_id, url) constraint).
    """
    start_str = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_str = end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with conn.execute(
        """SELECT n.user_id, n.channel_id, n.message_id,
                  MIN(n.sent_at) AS sent_at,
                  group_concat(NULLIF(n.url, ''), char(10)) AS urls,
                  v.score, v.reason, v.apply, v.red_flags
           FROM user_notified n
           LEFT JOIN post_verdicts v
               ON v.channel_id = n.channel_id
              AND v.message_id = n.message_id
              AND v.user_id = n.user_id
           WHERE n.sent_at >= ? AND n.sent_at < ? AND (? IS NULL OR n.user_id = ?)
           GROUP BY n.user_id, n.channel_id, n.message_id
           ORDER BY n.user_id, MIN(n.sent_at)""",
        (start_str, end_str, user_id, user_id),
    ) as cur:
        rows = await cur.fetchall()
    return [
        NotifiedPost(
            user_id=row[0],
            channel_id=row[1],
            message_id=row[2],
            sent_at=row[3],
            urls=[u for u in (row[4] or "").split("\n") if u],
            score=row[5],
            reason=row[6] or "",
            apply=row[7] or "",
            red_flags=json.loads(row[8]) if row[8] else [],
        )
        for row in rows
    ]


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
    await conn.execute(
        "DELETE FROM post_embeddings WHERE created_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    await conn.commit()
    return cursor.rowcount  # type: ignore[return-value]


async def count_scored_posts(conn: aiosqlite.Connection) -> int:
    """Count seen_posts rows that have at least one post_verdicts row.

    Counts per seen_posts row (same unit as ``SELECT COUNT(*) FROM seen_posts``),
    so the result is always <= the seen count. post_verdicts is never pruned
    while seen_posts is, hence the join direction from seen_posts.
    """
    async with conn.execute(
        """SELECT COUNT(*) FROM seen_posts s
           WHERE EXISTS (
               SELECT 1 FROM post_verdicts v
               WHERE v.channel_id = s.channel_id AND v.message_id = s.message_id
           )"""
    ) as cur:
        (n,) = await cur.fetchone()  # type: ignore[misc]
    return n


# --- Post embeddings (repost detection) ---


async def insert_post_embedding(
    conn: aiosqlite.Connection, channel_id: int, message_id: int, vector: bytes
) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO post_embeddings (channel_id, message_id, vector) VALUES (?, ?, ?)",
        (channel_id, message_id, vector),
    )
    await conn.commit()


async def get_recent_post_embeddings(
    conn: aiosqlite.Connection, days: int = 30
) -> list[tuple[int, int, bytes]]:
    async with conn.execute(
        "SELECT channel_id, message_id, vector FROM post_embeddings "
        "WHERE created_at >= datetime('now', ?)",
        (f"-{days} days",),
    ) as cur:
        rows = await cur.fetchall()
    return [(r[0], r[1], bytes(r[2])) for r in rows]


# --- Personalized verdicts ---


async def store_verdict(
    conn: aiosqlite.Connection,
    verdict: PersonalizedVerdict,
    channel_id: int,
    message_id: int,
) -> None:
    """Upsert a per-user verdict for a (channel_id, message_id) pair."""
    scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await conn.execute(
        """INSERT OR REPLACE INTO post_verdicts
           (channel_id, message_id, user_id, score, reason, matched, apply, red_flags, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            channel_id,
            message_id,
            verdict.user_id,
            verdict.score,
            verdict.reason,
            int(verdict.matched),
            verdict.apply,
            json.dumps(verdict.red_flags),
            scored_at,
        ),
    )
    await conn.commit()


async def get_verdicts_for_post(
    conn: aiosqlite.Connection,
    channel_id: int,
    message_id: int,
) -> list[PersonalizedVerdict]:
    """Return all stored verdicts for a given post."""
    async with conn.execute(
        """SELECT user_id, score, reason, matched, apply, red_flags
           FROM post_verdicts
           WHERE channel_id = ? AND message_id = ?""",
        (channel_id, message_id),
    ) as cur:
        rows = await cur.fetchall()
    return [
        PersonalizedVerdict(
            user_id=row[0],
            score=row[1],
            reason=row[2],
            matched=bool(row[3]),
            apply=row[4] or "",
            red_flags=json.loads(row[5]) if row[5] else [],
        )
        for row in rows
    ]


# --- Batch lifecycle ---


async def save_pending_batch(
    conn: aiosqlite.Connection,
    batch_id: str,
    request_map: dict[str, tuple[int, int, int]],
) -> None:
    """Persist a newly submitted batch so it can be recovered on restart."""
    submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # request_map: custom_id → (channel_id, message_id, user_id) as a JSON list
    serialized = json.dumps({k: list(v) for k, v in request_map.items()})
    await conn.execute(
        """INSERT OR IGNORE INTO pending_batches (batch_id, submitted_at, status, request_map)
           VALUES (?, ?, 'submitted', ?)""",
        (batch_id, submitted_at, serialized),
    )
    await conn.commit()


async def get_pending_batches(
    conn: aiosqlite.Connection,
) -> list[dict]:
    """Return all batches with status='submitted' for restart recovery."""
    async with conn.execute(
        "SELECT batch_id, submitted_at, request_map FROM pending_batches WHERE status = 'submitted'"
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "batch_id": row[0],
            "submitted_at": row[1],
            "request_map": json.loads(row[2]),
        }
        for row in rows
    ]


async def mark_batch_complete(
    conn: aiosqlite.Connection,
    batch_id: str,
    status: str = "complete",
) -> None:
    """Update batch status to 'complete' or 'failed'."""
    await conn.execute(
        "UPDATE pending_batches SET status = ? WHERE batch_id = ?",
        (status, batch_id),
    )
    await conn.commit()


# --- Batch usage / cost tracking ---


async def save_batch_usage(
    conn: aiosqlite.Connection,
    batch_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    succeeded: int,
    cost_usd: float | None = None,
) -> None:
    """Persist aggregated token counts for a completed batch."""
    recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await conn.execute(
        """INSERT OR REPLACE INTO batch_usage
           (batch_id, model, input_tokens, output_tokens,
            cache_creation_input_tokens, cache_read_input_tokens,
            succeeded, recorded_at, cost_usd)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_id, model, input_tokens, output_tokens,
            cache_creation_input_tokens, cache_read_input_tokens,
            succeeded, recorded_at, cost_usd,
        ),
    )
    await conn.commit()


async def get_total_usage(conn: aiosqlite.Connection) -> dict:
    """Return aggregated token counts across all recorded batches, grouped by model."""
    async with conn.execute(
        """SELECT model,
                  SUM(input_tokens),
                  SUM(output_tokens),
                  SUM(cache_creation_input_tokens),
                  SUM(cache_read_input_tokens),
                  SUM(succeeded),
                  COUNT(*)
           FROM batch_usage
           GROUP BY model"""
    ) as cur:
        rows = await cur.fetchall()
    return {
        row[0]: {
            "input_tokens": row[1] or 0,
            "output_tokens": row[2] or 0,
            "cache_creation_input_tokens": row[3] or 0,
            "cache_read_input_tokens": row[4] or 0,
            "succeeded": row[5] or 0,
            "batches": row[6] or 0,
        }
        for row in rows
    }


async def get_total_cost(conn: aiosqlite.Connection) -> dict:
    """Sum of stored per-batch cost_usd plus the count of batches with no cost."""
    async with conn.execute(
        """SELECT COALESCE(SUM(cost_usd), 0),
                  COALESCE(SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END), 0)
           FROM batch_usage"""
    ) as cur:
        row = await cur.fetchone()
    return {"cost_usd": row[0] or 0.0, "unpriced_batches": row[1] or 0}


# --- Cost tracking: embeddings, events, snapshots, buckets, reconciliation ---


async def record_embedding_usage(
    conn: aiosqlite.Connection,
    day: str,
    model: str,
    tokens: int,
    cost_usd: float | None,
) -> None:
    """Accumulate embedding tokens/cost per (day, model)."""
    await conn.execute(
        """INSERT INTO embedding_usage (day, model, tokens, cost_usd)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(day, model) DO UPDATE SET
               tokens = tokens + excluded.tokens,
               cost_usd = CASE
                   WHEN cost_usd IS NULL AND excluded.cost_usd IS NULL THEN NULL
                   ELSE COALESCE(cost_usd, 0) + COALESCE(excluded.cost_usd, 0)
               END""",
        (day, model, tokens, cost_usd),
    )
    await conn.commit()


async def add_cost_event(
    conn: aiosqlite.Connection,
    kind: str,
    key: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await conn.execute(
        "INSERT INTO cost_events (ts, kind, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
        (ts, kind, key, old_value, new_value),
    )
    await conn.commit()


async def list_cost_events(
    conn: aiosqlite.Connection, since: str, until: str
) -> list[dict]:
    async with conn.execute(
        """SELECT ts, kind, key, old_value, new_value FROM cost_events
           WHERE ts >= ? AND ts <= ? ORDER BY ts, id""",
        (since, until),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"ts": r[0], "kind": r[1], "key": r[2], "old_value": r[3], "new_value": r[4]}
        for r in rows
    ]


async def get_setting_snapshot(conn: aiosqlite.Connection) -> dict[str, str] | None:
    async with conn.execute("SELECT key, value FROM setting_snapshot") as cur:
        rows = await cur.fetchall()
    if not rows:
        return None
    return {r[0]: r[1] for r in rows}


async def save_setting_snapshot(
    conn: aiosqlite.Connection, values: dict[str, str]
) -> None:
    """Upsert keys; never deletes other keys sharing the table."""
    for k, v in values.items():
        await conn.execute(
            """INSERT INTO setting_snapshot (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (k, v),
        )
    await conn.commit()


async def save_population_snapshot(
    conn: aiosqlite.Connection, day: str, users: int, channels: int, keywords: int
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO daily_snapshots (day, users, channels, keywords) VALUES (?, ?, ?, ?)",
        (day, users, channels, keywords),
    )
    await conn.commit()


async def get_last_population_snapshot(conn: aiosqlite.Connection) -> dict | None:
    async with conn.execute(
        "SELECT day, users, channels, keywords FROM daily_snapshots ORDER BY day DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {"day": row[0], "users": row[1], "channels": row[2], "keywords": row[3]}


_BUCKET_LEN = {"day": 10, "month": 7, "year": 4}


async def get_cost_buckets(
    conn: aiosqlite.Connection,
    granularity: Literal["day", "month", "year"],
    limit: int,
) -> list[dict]:
    """UTC-bucketed cost/usage, newest first. Unpriced batches are counted, not summed."""
    n = _BUCKET_LEN[granularity]
    buckets: dict[str, dict] = {}

    def _get(b: str) -> dict:
        return buckets.setdefault(b, {
            "bucket": b, "scoring_usd": 0.0, "embedding_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "embedding_tokens": 0, "batches": 0, "succeeded": 0,
            "unpriced_batches": 0,
        })

    async with conn.execute(
        f"""SELECT substr(recorded_at, 1, {n}) AS b,
                   COALESCE(SUM(cost_usd), 0),
                   SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_input_tokens),
                   COUNT(*), SUM(succeeded),
                   SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END)
            FROM batch_usage GROUP BY b"""
    ) as cur:
        for r in await cur.fetchall():
            d = _get(r[0])
            d.update(
                scoring_usd=r[1] or 0.0, input_tokens=r[2] or 0,
                output_tokens=r[3] or 0, cache_read_tokens=r[4] or 0,
                batches=r[5] or 0, succeeded=r[6] or 0, unpriced_batches=r[7] or 0,
            )

    async with conn.execute(
        f"""SELECT substr(day, 1, {n}) AS b, COALESCE(SUM(cost_usd), 0), SUM(tokens)
            FROM embedding_usage GROUP BY b"""
    ) as cur:
        for r in await cur.fetchall():
            d = _get(r[0])
            d["embedding_usd"] = r[1] or 0.0
            d["embedding_tokens"] = r[2] or 0

    return sorted(buckets.values(), key=lambda x: x["bucket"], reverse=True)[:limit]


async def save_reconciliation(
    conn: aiosqlite.Connection, day: str, billed_usd: float, estimated_usd: float
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO cost_reconciliation (day, billed_usd, estimated_usd) VALUES (?, ?, ?)",
        (day, billed_usd, estimated_usd),
    )
    await conn.commit()


async def get_reconciliation(conn: aiosqlite.Connection, day: str) -> dict | None:
    async with conn.execute(
        "SELECT billed_usd, estimated_usd FROM cost_reconciliation WHERE day = ?", (day,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {"billed_usd": row[0], "estimated_usd": row[1]}
