import asyncio
import logging
from datetime import datetime

from pyrogram import Client
from pyrogram.raw import types as raw_types

from ahsoka.models import Post
from ahsoka.watcher.migration import migrate_watched_chat, supergroup_chat_id
from ahsoka.text_utils import button_urls_from_raw, merge_urls, slice_utf16

logger = logging.getLogger(__name__)


def register_watcher_handlers(
    client: Client,
    queue: asyncio.Queue,
    watched_channels: set[int],
    own_bot_id: int | None = None,
    conn=None,
    channel_names: dict[int, str] | None = None,
    lost_channels: set[int] | None = None,
) -> None:
    @client.on_raw_update()
    async def on_raw(c: Client, update, users: dict, chats: dict) -> None:
        logger.debug("RAW UPDATE: %s", type(update).__name__)

        if not isinstance(update, (raw_types.UpdateNewChannelMessage, raw_types.UpdateNewMessage)):
            return

        msg = update.message
        if isinstance(msg, raw_types.MessageService):
            action = msg.action
            if (
                isinstance(action, raw_types.MessageActionChatMigrateTo)
                and isinstance(msg.peer_id, raw_types.PeerChat)
            ):
                await migrate_watched_chat(
                    conn,
                    watched_channels,
                    -msg.peer_id.chat_id,
                    supergroup_chat_id(action.channel_id),
                    channel_names,
                    lost_channels,
                )
            return
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

        # A channel post auto-forwarded into its (watched) discussion group duplicates
        # the original; skip it only when the origin channel is itself watched.
        fwd = msg.fwd_from
        if (
            fwd is not None
            and fwd.saved_from_peer is not None
            and isinstance(fwd.from_id, raw_types.PeerChannel)
            and fwd.channel_post is not None
            and supergroup_chat_id(fwd.from_id.channel_id) in watched_channels
        ):
            logger.debug("Auto-forward of watched channel dropped: %s/%s", chat_id, msg.id)
            return

        # Skip bot-authored messages (group chatter from bots). Channel posts have no sender.
        sender = msg.from_id
        if isinstance(sender, raw_types.PeerUser):
            if own_bot_id is not None and sender.user_id == own_bot_id:
                return  # our own notifications (group is also a notify target)
            if getattr(users.get(sender.user_id), "bot", False):
                return

        text = msg.message or ""

        channel_name = str(chat_id)
        if raw_id in chats:
            ch = chats[raw_id]
            channel_name = getattr(ch, "username", None) or str(chat_id)

        entity_urls: list[str] = []
        for entity in (msg.entities or []):
            if isinstance(entity, raw_types.MessageEntityTextUrl):
                u = entity.url
            elif isinstance(entity, raw_types.MessageEntityUrl):
                u = slice_utf16(text, entity.offset, entity.length)
            else:
                continue
            if u:
                entity_urls.append(u)

        # Button URLs first so they survive the cap (they often hold the real description).
        urls = merge_urls(
            button_urls_from_raw(
                getattr(msg, "reply_markup", None), raw_types.KeyboardButtonUrl
            ),
            entity_urls,
        )

        post = Post(
            channel_id=chat_id,
            message_id=msg.id,
            channel_name=channel_name,
            text=text,
            url=urls[0] if urls else None,
            urls=urls,
            timestamp=datetime.fromtimestamp(msg.date) if msg.date else datetime.now(),
        )
        logger.info("Queued post %s/%s from @%s", chat_id, msg.id, channel_name)
        await queue.put(post)
