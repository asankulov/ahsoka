import asyncio

import aiosqlite
import pytest

from ahsoka.database import get_recent_post_embeddings, init_db
from ahsoka.pipeline.repost_detector import (
    RepostMatch,
    check_and_store,
    cosine,
    find_nearest,
    pack,
    unpack,
)
from ahsoka.database import insert_post_embedding


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c, owner_chat_id=1)
        yield c


def test_pack_unpack_roundtrip():
    assert unpack(pack([0.5, -1.0, 0.25])) == [0.5, -1.0, 0.25]


def test_cosine_identical_orthogonal_opposite():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_returns_zero():
    assert cosine([0, 0], [1, 1]) == 0.0
    assert cosine([1, 1], [0, 0]) == 0.0


async def test_find_nearest_picks_best_match(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0]))
    await insert_post_embedding(conn, 1, 2, pack([0.9, 0.1]))
    m = await find_nearest(conn, 2, 9, [0.9, 0.1])
    assert isinstance(m, RepostMatch)
    assert (m.channel_id, m.message_id) == (1, 2)
    assert m.similarity == pytest.approx(1.0, abs=1e-6)


async def test_find_nearest_skips_dimension_mismatch(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0, 0.0]))
    assert await find_nearest(conn, 2, 9, [1.0, 0.0]) is None


async def test_find_nearest_excludes_self(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0]))
    assert await find_nearest(conn, 1, 1, [1.0, 0.0]) is None


async def test_check_and_store_stores_when_below_threshold(conn):
    assert await check_and_store(conn, 1, 1, [1.0, 0.0], 0.95) is None
    assert len(await get_recent_post_embeddings(conn)) == 1


async def test_check_and_store_stores_when_match_below_threshold(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0]))
    m = await check_and_store(conn, 2, 2, [0.0, 1.0], 0.95)
    assert m is not None and m.similarity == pytest.approx(0.0)
    assert len(await get_recent_post_embeddings(conn)) == 2


async def test_check_and_store_threshold_is_inclusive(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0]))
    m = await check_and_store(conn, 2, 2, [1.0, 0.0], 1.0)
    assert m is not None and (m.channel_id, m.message_id) == (1, 1)
    assert len(await get_recent_post_embeddings(conn)) == 1


async def test_check_and_store_concurrent_copies(conn):
    results = await asyncio.gather(
        check_and_store(conn, 1, 1, [1.0, 0.0], 0.95),
        check_and_store(conn, 2, 2, [1.0, 0.0], 0.95),
    )
    assert len(await get_recent_post_embeddings(conn)) == 1
    assert sum(1 for r in results if r is not None and r.similarity >= 0.95) == 1


async def test_find_nearest_zero_query_vector_returns_zero_similarity(conn):
    await insert_post_embedding(conn, 1, 1, pack([1.0, 0.0]))
    m = await find_nearest(conn, 9, 9, [0.0, 0.0])
    assert m == RepostMatch(1, 1, 0.0)


async def test_find_nearest_zero_query_vector_empty_store_returns_none(conn):
    assert await find_nearest(conn, 9, 9, [0.0, 0.0]) is None


async def test_find_nearest_zero_stored_vector_returns_zero_similarity(conn):
    await insert_post_embedding(conn, 1, 1, pack([0.0, 0.0]))
    m = await find_nearest(conn, 9, 9, [1.0, 0.0])
    assert m == RepostMatch(1, 1, 0.0)


def test_cosine_non_unit_vectors_unchanged():
    assert cosine([3, 4], [3, 4]) == pytest.approx(1.0)
    assert cosine([1, 1], [1, 0]) == pytest.approx(2**-0.5)
