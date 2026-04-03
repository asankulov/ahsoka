import asyncio
import logging

from pyrogram import Client

from ahsoka.models import Post

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60.0  # seconds between sweeps
MESSAGES_PER_CHANNEL = 20  # how many recent messages to fetch per channel


async def channel_poller(
    client: Client,
    queue: asyncio.Queue,
    watched_channels: set[int],
) -> None:
    """
    Fallback poller for channels that don't deliver push UpdateNewChannelMessage.
    Fetches the most recent messages from each watched channel every POLL_INTERVAL
    seconds. Dedup in the pipeline discards anything already seen.
    """
    await asyncio.sleep(10)  # let the client fully settle after startup
    while True:
        for channel_id in list(watched_channels):
            try:
                async for message in client.get_chat_history(
                    channel_id, limit=MESSAGES_PER_CHANNEL
                ):
                    post = Post.from_message(message)
                    await queue.put(post)
            except Exception:
                logger.warning("Poller error for %s", channel_id, exc_info=True)
        logger.debug("Poll sweep done (%d channels)", len(watched_channels))
        await asyncio.sleep(POLL_INTERVAL)
