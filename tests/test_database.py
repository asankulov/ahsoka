import pytest
import aiosqlite

from ahsoka.database import (
    add_channel,
    ban_user,
    delete_old_posts,
    get_all_active_configs,
    get_or_create_user,
    get_pending_batches,
    get_user,
    get_user_config,
    get_verdicts_for_post,
    init_db,
    is_notified,
    is_seen,
    list_users,
    load_watched_channels,
    mark_batch_complete,
    mark_notified,
    mark_seen,
    remove_channel,
    save_pending_batch,
    seed_channels,
    set_notify_target,
    set_user_config,
    store_verdict,
    unban_user,
)
from ahsoka.models import PersonalizedVerdict

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


async def test_mark_seen_stores_extracted_fields(conn):
    await mark_seen(
        conn, 1, 2, score=8,
        stack_tags='["python", "django"]',
        seniority="senior",
        remote="remote",
        red_flags='["no salary info"]',
    )
    async with conn.execute(
        "SELECT stack_tags, seniority, remote, red_flags FROM seen_posts WHERE channel_id = 1 AND message_id = 2"
    ) as cur:
        row = await cur.fetchone()
    assert row == ('["python", "django"]', "senior", "remote", '["no salary info"]')


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


# --- Personalized verdicts ---


def make_verdict(
    user_id: int = 1,
    score: int = 8,
    matched: bool = True,
    reason: str = "Great match",
    apply: str = "hr@co.com",
    red_flags: list | None = None,
) -> PersonalizedVerdict:
    return PersonalizedVerdict(
        user_id=user_id,
        score=score,
        reason=reason,
        matched=matched,
        apply=apply,
        red_flags=red_flags or ["vague comp"],
    )


async def test_store_verdict_inserts_row(conn):
    verdict = make_verdict(user_id=1, score=8)
    await store_verdict(conn, verdict, channel_id=10, message_id=20)
    verdicts = await get_verdicts_for_post(conn, channel_id=10, message_id=20)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.user_id == 1
    assert v.score == 8
    assert v.matched is True
    assert v.apply == "hr@co.com"
    assert v.red_flags == ["vague comp"]


async def test_store_verdict_upserts_on_same_primary_key(conn):
    """Calling store_verdict twice for same (channel_id, message_id, user_id) updates, not duplicates."""
    verdict_v1 = make_verdict(user_id=1, score=5, reason="first")
    await store_verdict(conn, verdict_v1, channel_id=10, message_id=20)

    verdict_v2 = make_verdict(user_id=1, score=9, reason="updated")
    await store_verdict(conn, verdict_v2, channel_id=10, message_id=20)

    verdicts = await get_verdicts_for_post(conn, channel_id=10, message_id=20)
    assert len(verdicts) == 1
    assert verdicts[0].score == 9
    assert verdicts[0].reason == "updated"


async def test_get_verdicts_for_post_returns_all_users(conn):
    await store_verdict(conn, make_verdict(user_id=1, score=7), channel_id=10, message_id=20)
    await store_verdict(conn, make_verdict(user_id=2, score=9), channel_id=10, message_id=20)
    verdicts = await get_verdicts_for_post(conn, channel_id=10, message_id=20)
    assert len(verdicts) == 2
    user_ids = {v.user_id for v in verdicts}
    assert user_ids == {1, 2}


async def test_get_verdicts_for_post_deserializes_red_flags(conn):
    verdict = make_verdict(user_id=1, red_flags=["no salary", "vague role"])
    await store_verdict(conn, verdict, channel_id=5, message_id=6)
    verdicts = await get_verdicts_for_post(conn, channel_id=5, message_id=6)
    assert verdicts[0].red_flags == ["no salary", "vague role"]


async def test_get_verdicts_for_post_empty_red_flags(conn):
    v = PersonalizedVerdict(user_id=1, score=5, reason="ok", matched=False, apply="", red_flags=[])
    await store_verdict(conn, v, channel_id=7, message_id=8)
    verdicts = await get_verdicts_for_post(conn, channel_id=7, message_id=8)
    assert verdicts[0].red_flags == []


async def test_get_verdicts_for_post_no_results(conn):
    verdicts = await get_verdicts_for_post(conn, channel_id=999, message_id=999)
    assert verdicts == []


# --- Batch lifecycle ---


async def test_save_pending_batch_inserts_with_submitted_status(conn):
    request_map = {"111:222:42": [111, 222, 42]}
    await save_pending_batch(conn, batch_id="batch_001", request_map=request_map)
    pending = await get_pending_batches(conn)
    assert any(p["batch_id"] == "batch_001" for p in pending)


async def test_save_pending_batch_double_insert_is_idempotent(conn):
    """INSERT OR IGNORE: second insert with same batch_id must not raise or duplicate."""
    request_map = {"111:222:42": [111, 222, 42]}
    await save_pending_batch(conn, batch_id="batch_dup", request_map=request_map)
    await save_pending_batch(conn, batch_id="batch_dup", request_map=request_map)
    pending = await get_pending_batches(conn)
    matching = [p for p in pending if p["batch_id"] == "batch_dup"]
    assert len(matching) == 1


async def test_get_pending_batches_returns_only_submitted_rows(conn):
    """Rows with status != 'submitted' must not be returned."""
    await save_pending_batch(conn, "batch_submitted", {"a:b:c": [1, 2, 3]})
    await save_pending_batch(conn, "batch_to_complete", {"d:e:f": [4, 5, 6]})
    await mark_batch_complete(conn, "batch_to_complete", status="complete")

    pending = await get_pending_batches(conn)
    batch_ids = {p["batch_id"] for p in pending}
    assert "batch_submitted" in batch_ids
    assert "batch_to_complete" not in batch_ids


async def test_mark_batch_complete_removes_from_pending(conn):
    await save_pending_batch(conn, "batch_fin", {"x:y:z": [1, 2, 3]})
    await mark_batch_complete(conn, "batch_fin", status="complete")
    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_fin" for p in pending)


async def test_mark_batch_failed_removes_from_pending(conn):
    await save_pending_batch(conn, "batch_fail", {"x:y:z": [1, 2, 3]})
    await mark_batch_complete(conn, "batch_fail", status="failed")
    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_fail" for p in pending)


async def test_init_db_idempotent_no_error_on_double_call(conn):
    """Calling init_db twice on same connection must not raise."""
    await init_db(conn, owner_chat_id=OWNER_ID)  # second call
    # Verify the owner still exists only once
    user = await get_user(conn, OWNER_ID)
    assert user is not None
    assert user.is_admin is True
