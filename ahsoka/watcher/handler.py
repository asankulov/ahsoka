import asyncio
import logging
from datetime import datetime

from pyrogram import Client
from pyrogram.raw import types as raw_types

from ahsoka.models import Post

logger = logging.getLogger(__name__)


def register_watcher_handlers(
    client: Client,
    queue: asyncio.Queue,
    watched_channels: set[int],
) -> None:
    @client.on_raw_update()
    async def on_raw(c: Client, update, users: dict, chats: dict) -> None:
        logger.debug("RAW UPDATE: %s", type(update).__name__)

        if not isinstance(update, (raw_types.UpdateNewChannelMessage, raw_types.UpdateNewMessage)):
            return

        msg = update.message
        if not isinstance(msg, raw_types.Message):
            return

        peer = msg.peer_id
        if isinstance(peer, raw_types.PeerChannel):
            chat_id = int(f"-100{peer.channel_id}")
            raw_id = peer.channel_id
        elif isinstance(peer, raw_types.PeerChat):
            chat_id = -peer.chat_id
            raw_id = peer.chat_id
        else:
            return

        if chat_id not in watched_channels:
            return

        text = msg.message or ""

        channel_name = str(chat_id)
        if raw_id in chats:
            ch = chats[raw_id]
            channel_name = getattr(ch, "username", None) or str(chat_id)

        url: str | None = None
        for entity in (msg.entities or []):
            if isinstance(entity, raw_types.MessageEntityTextUrl):
                url = entity.url
                break
            if isinstance(entity, raw_types.MessageEntityUrl):
                url = text[entity.offset : entity.offset + entity.length]
                break

        post = Post(
            channel_id=chat_id,
            message_id=msg.id,
            channel_name=channel_name,
            text=text,
            url=url,
            timestamp=datetime.fromtimestamp(msg.date) if msg.date else datetime.now(),
        )
        logger.info("Queued post %s/%s from @%s", chat_id, msg.id, channel_name)
        await queue.put(post)
