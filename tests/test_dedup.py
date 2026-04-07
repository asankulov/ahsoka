import pytest
import aiosqlite

from ahsoka.database import init_db, mark_seen
from ahsoka.models import Post
from ahsoka.pipeline.dedup import is_duplicate


def make_post(channel_id: int = 1, message_id: int = 1) -> Post:
    return Post(
        channel_id=channel_id,
        message_id=message_id,
        channel_name="test",
        text="some text",
    )


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


async def test_new_post_is_not_duplicate(conn):
    post = make_post(channel_id=1, message_id=100)
    assert await is_duplicate(conn, post) is False


async def test_seen_post_is_duplicate(conn):
    post = make_post(channel_id=1, message_id=100)
    await mark_seen(conn, post.channel_id, post.message_id)
    assert await is_duplicate(conn, post) is True


async def test_different_channel_same_message_id_not_duplicate(conn):
    await mark_seen(conn, channel_id=1, message_id=100)
    post = make_post(channel_id=2, message_id=100)
    assert await is_duplicate(conn, post) is False


async def test_same_channel_different_message_id_not_duplicate(conn):
    await mark_seen(conn, channel_id=1, message_id=100)
    post = make_post(channel_id=1, message_id=101)
    assert await is_duplicate(conn, post) is False
