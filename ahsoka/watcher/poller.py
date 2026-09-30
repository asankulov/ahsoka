import asyncio
import logging

from pyrogram import Client

from ahsoka.models import Post

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60.0  # seconds between sweeps
READY_TIMEOUT = 120.0  # max wait for the warm-up before polling anyway
MAX_BACKOFF = 900.0  # cap for per-channel failure backoff (15 min)
MESSAGES_PER_CHANNEL = 20  # how many recent messages to fetch per channel


async def channel_poller(
    client: Client,
    queue: asyncio.Queue,
    watched_channels: set[int],
    ready: asyncio.Future | None = None,
) -> None:
    """
    Fallback poller for channels that don't deliver push UpdateNewChannelMessage.
    Fetches the most recent messages from each watched channel every POLL_INTERVAL
    seconds. Dedup in the pipeline discards anything already seen.

    A failing channel is skipped with exponential backoff (capped at MAX_BACKOFF)
    until it succeeds; other channels are unaffected. `ready` (e.g. the peer-cache
    warm-up task) is awaited before the first sweep; its failure is ignored, and
    after READY_TIMEOUT the poller proceeds without cancelling it.
    """
    await asyncio.sleep(10)  # let the client fully settle after startup
    if ready is not None:
        _, pending = await asyncio.wait([ready], timeout=READY_TIMEOUT)
        if pending:
            logger.warning(
                "Peer-cache warm-up still running after %ds; polling anyway",
                int(READY_TIMEOUT),
            )
    failures: dict[int, int] = {}
    retry_at: dict[int, float] = {}
    loop = asyncio.get_running_loop()
    while True:
        for channel_id in list(watched_channels):
            if loop.time() < retry_at.get(channel_id, 0.0):
                continue
            try:
                async for message in client.get_chat_history(
                    channel_id, limit=MESSAGES_PER_CHANNEL
                ):
                    post = Post.from_message(message)
                    await queue.put(post)
                failures.pop(channel_id, None)
                retry_at.pop(channel_id, None)
            except Exception as exc:
                count = failures.get(channel_id, 0) + 1
                failures[channel_id] = count
                backoff = min(POLL_INTERVAL * 2 ** (count - 1), MAX_BACKOFF)
                retry_at[channel_id] = loop.time() + backoff
                if count == 1:
                    logger.warning("Poller error for %s", channel_id, exc_info=True)
                else:
                    logger.warning(
                        "Poller error for %s (failure #%d, retry in %ds): %s",
                        channel_id, count, backoff, exc,
                    )
        logger.debug("Poll sweep done (%d channels)", len(watched_channels))
        await asyncio.sleep(POLL_INTERVAL)
