"""Tests for ahsoka.pipeline.batch_queue.BatchQueue."""
from datetime import datetime
from unittest.mock import patch

import pytest

from ahsoka.models import Post, UserConfig
from ahsoka.pipeline.batch_queue import BatchQueue, BatchRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_post(channel_id: int = 111, message_id: int = 222) -> Post:
    return Post(
        channel_id=channel_id,
        message_id=message_id,
        channel_name="testchan",
        text="Python backend job",
        timestamp=datetime.now(),
    )


def make_config(user_id: int = 42, stack: str = "python", threshold: int = 7) -> UserConfig:
    return UserConfig(
        user_id=user_id,
        notify_chat_id=user_id,
        stack=stack,
        threshold=threshold,
    )


def make_queue(flush_size: int = 10, flush_seconds: int = 600) -> BatchQueue:
    return BatchQueue(flush_size=flush_size, flush_seconds=flush_seconds)


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


async def test_enqueue_empty_configs_does_nothing():
    q = make_queue()
    await q.enqueue(make_post(), "content", [])
    assert await q.size() == 0


async def test_enqueue_expands_n_configs_into_n_requests():
    q = make_queue()
    configs = [make_config(user_id=i) for i in range(5)]
    await q.enqueue(make_post(), "content", configs)
    assert await q.size() == 5


async def test_enqueue_multiple_posts_accumulates():
    q = make_queue()
    await q.enqueue(make_post(message_id=1), "c1", [make_config(user_id=1)])
    await q.enqueue(make_post(message_id=2), "c2", [make_config(user_id=2)])
    assert await q.size() == 2


# ---------------------------------------------------------------------------
# BatchRequest.custom_id format
# ---------------------------------------------------------------------------


async def test_batch_request_custom_id_format():
    q = make_queue()
    post = make_post(channel_id=111, message_id=222)
    config = make_config(user_id=42)
    await q.enqueue(post, "content", [config])
    drained = await q.drain()
    assert len(drained) == 1
    assert drained[0].custom_id == "111_222_42"


async def test_batch_request_custom_id_uses_post_channel_and_message_ids():
    q = make_queue()
    post = make_post(channel_id=999, message_id=777)
    config = make_config(user_id=55)
    await q.enqueue(post, "content", [config])
    drained = await q.drain()
    assert drained[0].custom_id == "999_777_55"


# ---------------------------------------------------------------------------
# Snapshot semantics (the load-bearing correctness test)
# ---------------------------------------------------------------------------


async def test_snapshot_semantics_mutation_after_enqueue_does_not_affect_stored():
    """Mutating the original UserConfig after enqueue must NOT change the stored snapshot."""
    q = make_queue()
    config = make_config(user_id=1, stack="python")
    await q.enqueue(make_post(), "content", [config])

    # Now mutate the original config object
    config.stack = "go rust erlang"
    config.threshold = 99

    # The stored snapshot must still have the original values
    drained = await q.drain()
    assert len(drained) == 1
    stored = drained[0].config
    assert stored.stack == "python", "snapshot was not deep-copied — original mutation leaked in"
    assert stored.threshold == 7


async def test_snapshot_is_different_object_from_original():
    q = make_queue()
    config = make_config(user_id=1)
    await q.enqueue(make_post(), "content", [config])
    drained = await q.drain()
    assert drained[0].config is not config


# ---------------------------------------------------------------------------
# should_flush
# ---------------------------------------------------------------------------


async def test_should_flush_returns_false_on_empty_queue():
    q = make_queue()
    assert await q.should_flush() is False


async def test_should_flush_returns_true_when_size_threshold_reached():
    q = make_queue(flush_size=3)
    configs = [make_config(user_id=i) for i in range(3)]
    await q.enqueue(make_post(), "content", configs)
    assert await q.should_flush() is True


async def test_should_flush_returns_false_when_below_size_threshold():
    q = make_queue(flush_size=5)
    await q.enqueue(make_post(), "content", [make_config()])
    assert await q.should_flush() is False


async def test_should_flush_returns_true_when_age_threshold_crossed():
    """Oldest entry is old enough — time-based flush trigger."""
    q = make_queue(flush_size=100, flush_seconds=60)

    # Enqueue with a fake "now" that's 61 seconds in the past
    past_time = 1000.0
    with patch("ahsoka.pipeline.batch_queue.time.monotonic", return_value=past_time):
        await q.enqueue(make_post(), "content", [make_config()])

    # should_flush checks current time = past_time + 61 = 1061.0
    future_time = past_time + 61
    with patch("ahsoka.pipeline.batch_queue.time.monotonic", return_value=future_time):
        result = await q.should_flush()

    assert result is True


async def test_should_flush_returns_false_when_age_below_threshold():
    q = make_queue(flush_size=100, flush_seconds=60)

    past_time = 1000.0
    with patch("ahsoka.pipeline.batch_queue.time.monotonic", return_value=past_time):
        await q.enqueue(make_post(), "content", [make_config()])

    # Only 10 seconds have passed, threshold is 60
    future_time = past_time + 10
    with patch("ahsoka.pipeline.batch_queue.time.monotonic", return_value=future_time):
        result = await q.should_flush()

    assert result is False


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------


async def test_drain_returns_all_pending():
    q = make_queue()
    configs = [make_config(user_id=i) for i in range(3)]
    await q.enqueue(make_post(), "content", configs)
    drained = await q.drain()
    assert len(drained) == 3


async def test_drain_resets_queue():
    q = make_queue()
    await q.enqueue(make_post(), "content", [make_config()])
    await q.drain()
    assert await q.size() == 0


async def test_drain_on_empty_queue_returns_empty_list():
    q = make_queue()
    result = await q.drain()
    assert result == []


async def test_should_flush_returns_false_after_drain():
    q = make_queue(flush_size=1)
    await q.enqueue(make_post(), "content", [make_config()])
    assert await q.should_flush() is True  # sanity check
    await q.drain()
    assert await q.should_flush() is False


async def test_drain_returns_correct_types():
    q = make_queue()
    await q.enqueue(make_post(), "my content", [make_config(user_id=7)])
    drained = await q.drain()
    req = drained[0]
    assert isinstance(req, BatchRequest)
    assert req.content == "my content"
    assert req.post.message_id == 222
    assert req.config.user_id == 7
