"""Tests for ahsoka.watcher: handler, poller, client."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ahsoka.models import Post


# ---------------------------------------------------------------------------
# Helpers — build fake Pyrogram raw types without importing Pyrogram
# We create plain objects that replicate the attribute shapes handler.py reads.
# ---------------------------------------------------------------------------


def _make_channel_message(
    channel_id: int = 12345,
    message_id: int = 99,
    text: str = "Python dev job",
    date: int = 1_700_000_000,
    entities=None,
) -> MagicMock:
    """Return a fake raw_types.Message for a channel post."""
    from pyrogram.raw import types as raw_types  # noqa: PLC0415 — deferred to avoid collection error

    msg = MagicMock(spec=raw_types.Message)
    msg.id = message_id
    msg.message = text
    msg.date = date
    msg.entities = entities or []

    peer = MagicMock(spec=raw_types.PeerChannel)
    peer.channel_id = channel_id
    msg.peer_id = peer
    return msg


def _make_update(msg: MagicMock, update_type: str = "channel") -> MagicMock:
    """Wrap a message in a fake UpdateNewChannelMessage."""
    from pyrogram.raw import types as raw_types  # noqa: PLC0415

    if update_type == "channel":
        update = MagicMock(spec=raw_types.UpdateNewChannelMessage)
    else:
        update = MagicMock(spec=raw_types.UpdateNewMessage)
    update.message = msg
    return update


# ---------------------------------------------------------------------------
# register_watcher_handlers / on_raw inner function
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_queues_post_for_watched_channel():
    """on_raw queues a Post when the channel is in watched_channels."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 12345
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    # Capture the handler registered via @client.on_raw_update()
    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)
    assert captured_handler is not None, "Handler was not registered"

    msg = _make_channel_message(channel_id=channel_id_raw, message_id=42, text="Remote Python role")
    update = _make_update(msg)

    chats = {channel_id_raw: MagicMock(username="testchan")}
    await captured_handler(client, update, users={}, chats=chats)

    assert not queue.empty()
    post: Post = queue.get_nowait()
    assert post.channel_id == chat_id
    assert post.message_id == 42
    assert post.text == "Remote Python role"
    assert post.channel_name == "testchan"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_ignores_unwatched_channel():
    """on_raw does not queue a Post when channel is not in watched_channels."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels: set[int] = set()  # empty — nothing watched

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)
    assert captured_handler is not None

    msg = _make_channel_message(channel_id=99999, message_id=1)
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    assert queue.empty()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_skips_non_message_update():
    """on_raw returns early for updates that are not UpdateNewChannelMessage/UpdateNewMessage."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels: set[int] = {-10099999}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    # Non-message update type (e.g. UpdateReadChannelInbox)
    irrelevant_update = MagicMock()
    # Make isinstance checks fail by not using spec from the expected types
    irrelevant_update.__class__ = object

    await captured_handler(client, irrelevant_update, users={}, chats={})

    assert queue.empty()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_extracts_text_link_entity():
    """on_raw collects URLs from MessageEntityTextUrl entities."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 77777
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    # Create a MessageEntityTextUrl entity
    entity = MagicMock(spec=raw_types.MessageEntityTextUrl)
    entity.url = "https://example.com/job"

    msg = _make_channel_message(
        channel_id=channel_id_raw, message_id=55, entities=[entity]
    )
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert "https://example.com/job" in post.urls


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_extracts_url_entity():
    """on_raw collects URLs from MessageEntityUrl entities (inline URL in text)."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 66666
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    text = "Apply at https://jobs.example.com/1"
    url_in_text = "https://jobs.example.com/1"
    offset = text.index(url_in_text)

    entity = MagicMock(spec=raw_types.MessageEntityUrl)
    entity.offset = offset
    entity.length = len(url_in_text)

    msg = _make_channel_message(
        channel_id=channel_id_raw, message_id=66, text=text, entities=[entity]
    )
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert url_in_text in post.urls


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_limits_urls_to_three():
    """on_raw caps URL collection at 3 entries."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 55555
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    # 5 distinct URLs
    entities = []
    for i in range(5):
        e = MagicMock(spec=raw_types.MessageEntityTextUrl)
        e.url = f"https://example.com/job{i}"
        entities.append(e)

    msg = _make_channel_message(channel_id=channel_id_raw, message_id=77, entities=entities)
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert len(post.urls) == 3


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_uses_chat_id_as_channel_name_when_no_username():
    """on_raw falls back to str(chat_id) when chat has no username."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 44444
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    msg = _make_channel_message(channel_id=channel_id_raw, message_id=88)
    update = _make_update(msg)

    # Chat with username=None
    chat_mock = MagicMock()
    chat_mock.username = None
    chats = {channel_id_raw: chat_mock}

    await captured_handler(client, update, users={}, chats=chats)

    post: Post = queue.get_nowait()
    assert post.channel_name == str(chat_id)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_channel_name_from_chats_dict():
    """on_raw uses username from chats dict when raw_id is present."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 33333
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    msg = _make_channel_message(channel_id=channel_id_raw, message_id=91)
    update = _make_update(msg)

    chat_mock = MagicMock()
    chat_mock.username = "mychannel"
    chats = {channel_id_raw: chat_mock}

    await captured_handler(client, update, users={}, chats=chats)

    post: Post = queue.get_nowait()
    assert post.channel_name == "mychannel"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_peer_chat_computes_negative_chat_id():
    """on_raw computes -chat_id for PeerChat peers."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    raw_chat_id = 11111
    expected_chat_id = -raw_chat_id
    queue: asyncio.Queue = asyncio.Queue()
    watched_channels = {expected_chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    peer = MagicMock(spec=raw_types.PeerChat)
    peer.chat_id = raw_chat_id

    msg = MagicMock(spec=raw_types.Message)
    msg.id = 200
    msg.message = "hello"
    msg.date = 1_700_000_000
    msg.entities = []
    msg.peer_id = peer

    update = MagicMock(spec=raw_types.UpdateNewMessage)
    update.message = msg

    await captured_handler(client, update, users={}, chats={})

    assert not queue.empty()
    post: Post = queue.get_nowait()
    assert post.channel_id == expected_chat_id


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_skips_non_message_type():
    """on_raw skips if msg is not a raw_types.Message instance."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels: set[int] = {-10012345}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    update = MagicMock(spec=raw_types.UpdateNewChannelMessage)
    # msg is NOT a raw_types.Message — it's a MessageService, for example
    update.message = MagicMock(spec=raw_types.MessageService)

    await captured_handler(client, update, users={}, chats={})

    assert queue.empty()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_skips_unrecognised_entity_type():
    """on_raw skips entities that are neither MessageEntityTextUrl nor MessageEntityUrl."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 22222
    chat_id = int(f"-100{channel_id_raw}")
    watched_channels = {chat_id}

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    # Use a bold entity — not a URL type, should be skipped
    bold_entity = MagicMock(spec=raw_types.MessageEntityBold)
    msg = _make_channel_message(channel_id=channel_id_raw, message_id=111, entities=[bold_entity])
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    # No URLs extracted — bold entity is skipped
    assert post.urls == []


async def test_handler_skips_unknown_peer_type():
    """on_raw returns early for peer types that are neither PeerChannel nor PeerChat."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels: set[int] = set()

    captured_handler = None

    def fake_on_raw_update():
        def decorator(fn):
            nonlocal captured_handler
            captured_handler = fn
            return fn
        return decorator

    client = MagicMock()
    client.on_raw_update = fake_on_raw_update

    register_watcher_handlers(client, queue, watched_channels)

    peer = MagicMock(spec=raw_types.PeerUser)  # PeerUser — not channel or chat

    msg = MagicMock(spec=raw_types.Message)
    msg.id = 300
    msg.message = "dm message"
    msg.date = 1_700_000_000
    msg.entities = []
    msg.peer_id = peer

    update = MagicMock(spec=raw_types.UpdateNewChannelMessage)
    update.message = msg

    await captured_handler(client, update, users={}, chats={})

    assert queue.empty()


# ---------------------------------------------------------------------------
# channel_poller
# ---------------------------------------------------------------------------


async def test_channel_poller_enqueues_posts_for_watched_channels():
    """channel_poller fetches history for each watched channel and enqueues posts."""
    from ahsoka.watcher.poller import channel_poller

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels = {-1001111, -1002222}

    post1 = MagicMock()
    post2 = MagicMock()

    # Post.from_message is called on each message returned by get_chat_history
    fake_messages = [post1, post2]

    async def fake_get_history(channel_id, limit):
        for m in fake_messages:
            yield m

    client = MagicMock()
    client.get_chat_history = fake_get_history

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # First call is the startup 10-second delay — let it pass.
        # Second call is the POLL_INTERVAL sleep — cancel here.
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with patch("ahsoka.watcher.poller.asyncio.sleep", side_effect=fake_sleep), \
         patch("ahsoka.watcher.poller.Post.from_message", side_effect=lambda m: m):
        try:
            await channel_poller(client, queue, watched_channels)
        except asyncio.CancelledError:
            pass

    # Each channel × each message should have been enqueued
    assert queue.qsize() == len(watched_channels) * len(fake_messages)


async def test_channel_poller_logs_warning_on_channel_error():
    """channel_poller logs a warning and continues when iterating history raises."""
    from ahsoka.watcher.poller import channel_poller

    queue: asyncio.Queue = asyncio.Queue()
    watched_channels = {-1001111}

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # First sleep is startup delay — allow it.
        # Second sleep (POLL_INTERVAL after the error) — cancel.
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    async def failing_get_history(channel_id, limit):
        # Raise when the async generator is iterated — caught by `except Exception`
        raise RuntimeError("Flood wait")
        yield MagicMock()  # pragma: no cover  — makes it an async generator

    client = MagicMock()
    client.get_chat_history = failing_get_history

    with patch("ahsoka.watcher.poller.asyncio.sleep", side_effect=fake_sleep), \
         patch("ahsoka.watcher.poller.logger") as mock_logger:
        try:
            await channel_poller(client, queue, watched_channels)
        except asyncio.CancelledError:
            pass

    # The except Exception block in channel_poller should log a warning
    assert mock_logger.warning.called
    assert queue.empty()


# ---------------------------------------------------------------------------
# build_pyrogram_client
# ---------------------------------------------------------------------------


def test_build_pyrogram_client_passes_settings_to_client():
    """build_pyrogram_client constructs a Pyrogram Client with correct settings."""
    from ahsoka.watcher.client import build_pyrogram_client

    settings = MagicMock()
    settings.session_name = "test_session"
    settings.telegram_api_id = 12345
    settings.telegram_api_hash = "abc123"

    with patch("ahsoka.watcher.client.Client") as mock_client_class:
        mock_client_class.return_value = MagicMock()
        result = build_pyrogram_client(settings)

    mock_client_class.assert_called_once_with(
        name="test_session",
        api_id=12345,
        api_hash="abc123",
    )
    assert result is mock_client_class.return_value
