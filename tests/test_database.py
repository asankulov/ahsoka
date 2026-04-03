import pytest
import aiosqlite

from ahsoka.database import (
    add_channel,
    delete_old_posts,
    get_config,
    init_db,
    is_seen,
    load_watched_channels,
    mark_seen,
    remove_channel,
    seed_channels,
    set_config,
)


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


async def test_not_seen_initially(conn):
    assert await is_seen(conn, 1, 1) is False


async def test_seen_after_mark(conn):
    await mark_seen(conn, 1, 1, score=7)
    assert await is_seen(conn, 1, 1) is True


async def test_mark_seen_idempotent(conn):
    await mark_seen(conn, 1, 1)
    await mark_seen(conn, 1, 1)  # should not raise
    assert await is_seen(conn, 1, 1) is True


async def test_different_messages_tracked_independently(conn):
    await mark_seen(conn, 1, 1)
    assert await is_seen(conn, 1, 2) is False
    assert await is_seen(conn, 2, 1) is False


async def test_config_defaults(conn):
    config = await get_config(conn)
    assert config.threshold == 7
    assert config.paused is False
    assert config.keywords == ""
    assert config.stack == ""


async def test_set_config(conn):
    await set_config(conn, "stack", "python go")
    config = await get_config(conn)
    assert config.stack == "python go"


async def test_paused_flag(conn):
    await set_config(conn, "paused", "true")
    config = await get_config(conn)
    assert config.paused is True


async def test_add_and_load_channel(conn):
    await add_channel(conn, -1001234567890)
    channels = await load_watched_channels(conn)
    assert -1001234567890 in channels


async def test_remove_channel(conn):
    await add_channel(conn, -1001234567890)
    await remove_channel(conn, -1001234567890)
    channels = await load_watched_channels(conn)
    assert -1001234567890 not in channels


async def test_seed_channels_on_empty_table(conn):
    await seed_channels(conn, [-100111, -100222])
    channels = await load_watched_channels(conn)
    assert -100111 in channels
    assert -100222 in channels


async def test_seed_channels_skipped_when_not_empty(conn):
    await add_channel(conn, -100111)
    await seed_channels(conn, [-100999])  # should not insert
    channels = await load_watched_channels(conn)
    assert -100999 not in channels


async def test_delete_old_posts(conn):
    await conn.execute(
        "INSERT INTO seen_posts (channel_id, message_id, score, scored_at) "
        "VALUES (?, ?, ?, datetime('now', '-31 days'))",
        (1, 999, 5),
    )
    await conn.commit()
    deleted = await delete_old_posts(conn, days=30)
    assert deleted == 1
    assert await is_seen(conn, 1, 999) is False


async def test_delete_old_posts_keeps_recent(conn):
    await mark_seen(conn, 1, 1, score=8)
    deleted = await delete_old_posts(conn, days=30)
    assert deleted == 0
    assert await is_seen(conn, 1, 1) is True
