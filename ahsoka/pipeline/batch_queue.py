"""BatchQueue: buffers per-user scoring requests until a flush threshold is met."""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass

from ahsoka.models import Post, UserConfig

logger = logging.getLogger(__name__)


@dataclass
class BatchRequest:
    """Single pending score request; config is a snapshot taken at enqueue time."""
    custom_id: str          # "{channel_id}:{message_id}:{user_id}"
    post: Post
    content: str
    config: UserConfig      # immutable snapshot — never mutated after creation


class BatchQueue:
    """Thread-safe asyncio queue of pending BatchRequest entries.

    Flush policy: drain when `len(pending) >= flush_size` OR when the
    oldest entry has been waiting for `>= flush_seconds`.
    """

    def __init__(self, flush_size: int, flush_seconds: int) -> None:
        self._flush_size = flush_size
        self._flush_seconds = flush_seconds
        self._pending: list[BatchRequest] = []
        self._oldest_ts: float | None = None
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        post: Post,
        content: str,
        configs: list[UserConfig],
    ) -> None:
        """Expand (post, content) × configs into one pending request per user.

        Each UserConfig is deep-copied so that later mutations to the live
        config object cannot affect the in-flight snapshot.
        """
        if not configs:
            return
        now = time.monotonic()
        async with self._lock:
            for config in configs:
                snapshot = copy.deepcopy(config)
                req = BatchRequest(
                    custom_id=f"{post.channel_id}:{post.message_id}:{config.user_id}",
                    post=post,
                    content=content,
                    config=snapshot,
                )
                self._pending.append(req)
            if self._oldest_ts is None:
                self._oldest_ts = now
            logger.debug(
                "Enqueued %d user requests for %s/%s — queue depth: %d",
                len(configs), post.channel_id, post.message_id, len(self._pending),
            )

    async def should_flush(self) -> bool:
        """Return True if size or age threshold has been crossed."""
        async with self._lock:
            if not self._pending:
                return False
            if len(self._pending) >= self._flush_size:
                return True
            if self._oldest_ts is not None:
                age = time.monotonic() - self._oldest_ts
                if age >= self._flush_seconds:
                    return True
            return False

    async def drain(self) -> list[BatchRequest]:
        """Return all pending requests and reset the queue."""
        async with self._lock:
            if not self._pending:
                return []
            requests = list(self._pending)
            self._pending = []
            self._oldest_ts = None
            logger.debug("Drained %d requests from BatchQueue", len(requests))
            return requests

    async def size(self) -> int:
        async with self._lock:
            return len(self._pending)
