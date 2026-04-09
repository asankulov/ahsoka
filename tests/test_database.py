import pytest
import aiosqlite

from ahsoka.database import (
    add_channel,
    ban_user,
    delete_old_posts,
    get_all_active_configs,
    get_or_create_user,
    get_user,
    get_user_config,
    init_db,
    is_notified,
    is_seen,
    list_users,
    load_watched_channels,
    mark_notified,
    mark_seen,
    remove_channel,
    seed_channels,
    set_notify_target,
    set_user_config,
    unban_user,
)

OWNER_ID = 12345


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c, owner_chat_id=OWNER_ID)
        yield c


# --- Seen posts ---


async def test_not_seen_initially(conn):
    assert await is_seen(conn, 1, 1) is False


async def test_seen_after_mark(conn):
    await mark_seen(conn, 1, 1, score=7)
    assert await is_seen(conn, 1, 1) is True


async def test_mark_seen_idempotent(conn):
    await mark_seen(conn, 1, 1)
    await mark_seen(conn, 1, 1)
    assert await is_seen(conn, 1, 1) is True


async def test_different_messages_tracked_independently(conn):
    await mark_seen(conn, 1, 1)
    assert await is_seen(conn, 1, 2) is False
    assert await is_seen(conn, 2, 1) is False


async def test_mark_seen_stores_score_reason_and_apply(conn):
    await mark_seen(conn, 1, 1, score=8, score_reason="Good match", apply_info="hr@co.com")
    async with conn.execute(
        "SELECT score, score_reason, apply_info FROM seen_posts WHERE channel_id = 1 AND message_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row == (8, "Good match", "hr@co.com")


# --- User management ---


async def test_owner_created_on_init(conn):
    user = await get_user(conn, OWNER_ID)
    assert user is not None
    assert user.is_admin is True


async def test_get_or_create_user_new(conn):
    user = await get_or_create_user(conn, 99999)
    assert user.user_id == 99999
    assert user.notify_chat_id == 99999
    assert user.is_admin is False


async def test_get_or_create_user_existing(conn):
    user1 = await get_or_create_user(conn, 99999)
    user2 = await get_or_create_user(conn, 99999)
    assert user1.user_id == user2.user_id


async def test_list_users(conn):
    await get_or_create_user(conn, 11111)
    users = await list_users(conn)
    assert len(users) >= 2  # owner + new user


async def test_ban_and_unban(conn):
    await get_or_create_user(conn, 11111)
    await ban_user(conn, 11111)
    user = await get_user(conn, 11111)
    assert user.is_banned is True
    await unban_user(conn, 11111)
    user = await get_user(conn, 11111)
    assert user.is_banned is False


async def test_set_notify_target(conn):
    await set_notify_target(conn, OWNER_ID, -100999)
    user = await get_user(conn, OWNER_ID)
    assert user.notify_chat_id == -100999


# --- User config ---


async def test_config_defaults(conn):
    config = await get_user_config(conn, OWNER_ID)
    assert config.threshold == 7
    assert config.paused is False
    assert config.keywords == ""
    assert config.stack == ""


async def test_set_user_config(conn):
    await set_user_config(conn, OWNER_ID, "stack", "python go")
    config = await get_user_config(conn, OWNER_ID)
    assert config.stack == "python go"


async def test_paused_flag(conn):
    await set_user_config(conn, OWNER_ID, "paused", "1")
    config = await get_user_config(conn, OWNER_ID)
    assert config.paused is True


async def test_get_all_active_configs(conn):
    await get_or_create_user(conn, 11111)
    configs = await get_all_active_configs(conn)
    assert len(configs) >= 2


async def test_get_all_active_configs_excludes_banned(conn):
    await get_or_create_user(conn, 11111)
    await ban_user(conn, 11111)
    configs = await get_all_active_configs(conn)
    user_ids = {c.user_id for c in configs}
    assert 11111 not in user_ids


# --- Notification tracking ---


async def test_not_notified_initially(conn):
    assert await is_notified(conn, OWNER_ID, 1, 1) is False


async def test_notified_after_mark(conn):
    await mark_notified(conn, OWNER_ID, 1, 1)
    assert await is_notified(conn, OWNER_ID, 1, 1) is True


async def test_notification_per_user(conn):
    await get_or_create_user(conn, 11111)
    await mark_notified(conn, OWNER_ID, 1, 1)
    assert await is_notified(conn, 11111, 1, 1) is False


# --- Channels ---


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
    await seed_channels(conn, [-100999])
    channels = await load_watched_channels(conn)
    assert -100999 not in channels


async def test_add_channel_with_added_by(conn):
    await add_channel(conn, -100999, added_by=OWNER_ID)
    channels = await load_watched_channels(conn)
    assert -100999 in channels


# --- Cleanup ---


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
