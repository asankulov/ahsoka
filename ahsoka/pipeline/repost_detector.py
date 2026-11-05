import asyncio
import math
from array import array
from dataclasses import dataclass

import aiosqlite

from ahsoka.database import get_recent_post_embeddings, insert_post_embedding

_lock = asyncio.Lock()


@dataclass(frozen=True)
class RepostMatch:
    channel_id: int
    message_id: int
    similarity: float


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _cosine_with_norm(a: list[float], na: float, b: list[float]) -> float:
    nb = _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def cosine(a: list[float], b: list[float]) -> float:
    return _cosine_with_norm(a, _norm(a), b)


async def find_nearest(
    conn: aiosqlite.Connection, channel_id: int, message_id: int, vector: list[float]
) -> RepostMatch | None:
    best: RepostMatch | None = None
    qnorm = _norm(vector)
    for ch, msg, blob in await get_recent_post_embeddings(conn):
        if (ch, msg) == (channel_id, message_id):
            continue
        other = unpack(blob)
        if len(other) != len(vector):
            continue
        sim = _cosine_with_norm(vector, qnorm, other)
        if best is None or sim > best.similarity:
            best = RepostMatch(ch, msg, sim)
    return best


async def check_and_store(
    conn: aiosqlite.Connection,
    channel_id: int,
    message_id: int,
    vector: list[float],
    threshold: float,
) -> RepostMatch | None:
    async with _lock:
        match = await find_nearest(conn, channel_id, message_id, vector)
        if match and match.similarity >= threshold:
            return match
        await insert_post_embedding(conn, channel_id, message_id, pack(vector))
        return match
