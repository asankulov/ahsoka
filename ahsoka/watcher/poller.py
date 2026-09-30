import asyncio
import logging

from pyrogram import Client
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate

from ahsoka.models import Post

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60.0  # seconds between sweeps
READY_TIMEOUT = 120.0  # max wait for the warm-up before polling anyway
MAX_BACKOFF = 900.0  # cap for per-channel failure backoff (15 min)
LOST_THRESHOLD = 3  # consecutive access-lost failures before alerting
ACCESS_LOST_ERRORS = (ChannelPrivate, ChannelBanned, ChannelInvalid)
MESSAGES_PER_CHANNEL = 20  # how many recent messages to fetch per channel


async def channel_poller(
    client: Client,
    queue: asyncio.Queue,
    watched_channels: set[int],
    ready: asyncio.Future | None = None,
    lost_channels: set[int] | None = None,
    channel_names: dict[int, str] | None = None,
) -> None:
    """
    Fallback poller for channels that don't deliver push UpdateNewChannelMessage.
    Fetches the most recent messages from each watched channel every POLL_INTERVAL
    seconds. Dedup in the pipeline discards anything already seen.

    A failing channel is skipped with exponential backoff (capped at MAX_BACKOFF)
    until it succeeds; other channels are unaffected. `ready` (e.g. the peer-cache
    warm-up task) is awaited before the first sweep; its failure is ignored, and
    after READY_TIMEOUT the poller proceeds without cancelling it.

    Access loss: LOST_THRESHOLD consecutive ChannelPrivate/ChannelBanned/
    ChannelInvalid failures mark a channel lost (added to `lost_channels`) and
    emit ONE logger.error (forwarded to the owner by the log handler). The
    streak counter is separate from the backoff count; a success resets it,
    a non-access error neither counts nor resets it. Failures of an already-lost
    channel log one line without traceback. A success on a lost channel
    clears it and logs INFO.
    """
    await asyncio.sleep(10)  # let the client fully settle after startup
    if ready is not None:
        _, pending = await asyncio.wait([ready], timeout=READY_TIMEOUT)
        if pending:
            logger.warning(
                "Peer-cache warm-up still running after %ds; polling anyway",
                int(READY_TIMEOUT),
            )
    if lost_channels is None:
        lost_channels = set()
    if channel_names is None:
        channel_names = {}
    failures: dict[int, int] = {}
    lost_streak: dict[int, int] = {}
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
                lost_streak.pop(channel_id, None)
                if channel_id in lost_channels:
                    lost_channels.discard(channel_id)
                    logger.info(
                        "Access restored to watched channel %s",
                        channel_names.get(channel_id, str(channel_id)),
                    )
            except Exception as exc:
                label = channel_names.get(channel_id, str(channel_id))
                already_lost = channel_id in lost_channels
                if isinstance(exc, ACCESS_LOST_ERRORS):
                    streak = lost_streak.get(channel_id, 0) + 1
                    lost_streak[channel_id] = streak
                    if streak >= LOST_THRESHOLD and not already_lost:
                        lost_channels.add(channel_id)
                        logger.error(
                            "Lost access to watched channel %s (%s)",
                            label, type(exc).__name__,
                        )
                        already_lost = True
                count = failures.get(channel_id, 0) + 1
                failures[channel_id] = count
                backoff = min(POLL_INTERVAL * 2 ** (count - 1), MAX_BACKOFF)
                retry_at[channel_id] = loop.time() + backoff
                if already_lost:
                    logger.info(
                        "Poll probe failed for lost channel %s (retry in %ds): %s",
                        label, backoff, exc,
                    )
                elif count == 1:
                    logger.warning("Poller error for %s", channel_id, exc_info=True)
                else:
                    logger.warning(
                        "Poller error for %s (failure #%d, retry in %ds): %s",
                        channel_id, count, backoff, exc,
                    )
        logger.debug("Poll sweep done (%d channels)", len(watched_channels))
        await asyncio.sleep(POLL_INTERVAL)
