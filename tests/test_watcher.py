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
async def test_handler_extracts_url_entity_with_emoji_before_it():
    """on_raw slices MessageEntityUrl in UTF-16 space so an astral emoji before
    the URL does not shift/truncate the extracted string.

    This is the user's motivating example: '🚀 Apply https://jobs.io now' with
    UTF-16 offset 9 / length 15 must yield the full 'https://jobs.io', not a
    shifted/truncated substring as plain str[9:24] slicing would produce (the
    rocket emoji occupies 2 UTF-16 units but only 1 Python str index).
    """
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 88888
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

    text = "\U0001F680 Apply https://jobs.io now"

    entity = MagicMock(spec=raw_types.MessageEntityUrl)
    entity.offset = 9
    entity.length = 15

    msg = _make_channel_message(
        channel_id=channel_id_raw, message_id=67, text=text, entities=[entity]
    )
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert post.url == "https://jobs.io"
    assert post.urls == ["https://jobs.io"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_extracts_mixed_text_url_and_url_entities_with_emoji():
    """Emoji before a MessageEntityUrl, plus a MessageEntityTextUrl after it —
    both should resolve correctly, and the plain-ASCII text_link path (which
    doesn't slice) remains unaffected by the emoji shift."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 88889
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

    text = "\U0001F680 Apply https://jobs.io now"

    url_entity = MagicMock(spec=raw_types.MessageEntityUrl)
    url_entity.offset = 9
    url_entity.length = 15

    text_link_entity = MagicMock(spec=raw_types.MessageEntityTextUrl)
    text_link_entity.url = "https://example.com/apply"

    msg = _make_channel_message(
        channel_id=channel_id_raw,
        message_id=68,
        text=text,
        entities=[url_entity, text_link_entity],
    )
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert post.urls == ["https://jobs.io", "https://example.com/apply"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_handler_extracts_url_entity_ascii_only_unchanged():
    """ASCII-only text (no astral chars) behaves exactly as before the fix —
    a regression guard for the plain-str-equivalent path of slice_utf16."""
    try:
        from pyrogram.raw import types as raw_types
    except Exception:
        pytest.skip("Pyrogram not importable in this environment")

    from ahsoka.watcher.handler import register_watcher_handlers

    queue: asyncio.Queue = asyncio.Queue()
    channel_id_raw = 88890
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

    text = "Apply at https://jobs.example.com/1 today"
    url_in_text = "https://jobs.example.com/1"
    offset = text.index(url_in_text)

    entity = MagicMock(spec=raw_types.MessageEntityUrl)
    entity.offset = offset
    entity.length = len(url_in_text)

    msg = _make_channel_message(
        channel_id=channel_id_raw, message_id=69, text=text, entities=[entity]
    )
    update = _make_update(msg)

    await captured_handler(client, update, users={}, chats={})

    post: Post = queue.get_nowait()
    assert post.urls == [url_in_text]


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


# ---------------------------------------------------------------------------
# patch_pyrogram_channel_id_limit
# ---------------------------------------------------------------------------
# pyrogram is imported inside each (async) test: its import calls
# asyncio.get_event_loop(), which needs a running loop on Python 3.12+.
# monkeypatch restores MIN_CHANNEL_ID so other tests are unaffected.

LARGE_CHANNEL_ID = -1002341925485
OLD_CHANNEL_ID = -1001904490423
PATCHED_MIN = -1007852516352


async def test_patch_channel_id_limit_fixes_large_channel_peer_type(monkeypatch):
    import pyrogram.utils as pu
    from ahsoka.watcher.client import patch_pyrogram_channel_id_limit

    # Ensure a known pre-patch state (the 32-bit cap) and restore afterwards.
    monkeypatch.setattr(pu, "MIN_CHANNEL_ID", -1002147483647)
    with pytest.raises(ValueError):
        pu.get_peer_type(LARGE_CHANNEL_ID)

    patch_pyrogram_channel_id_limit()

    assert pu.get_peer_type(LARGE_CHANNEL_ID) == "channel"
    assert pu.get_peer_type(OLD_CHANNEL_ID) == "channel"


async def test_patch_channel_id_limit_is_idempotent(monkeypatch):
    import pyrogram.utils as pu
    from ahsoka.watcher.client import patch_pyrogram_channel_id_limit

    monkeypatch.setattr(pu, "MIN_CHANNEL_ID", pu.MIN_CHANNEL_ID)
    patch_pyrogram_channel_id_limit()
    patch_pyrogram_channel_id_limit()

    assert pu.MIN_CHANNEL_ID == PATCHED_MIN


async def test_build_pyrogram_client_applies_channel_id_patch(monkeypatch):
    import pyrogram.utils as pu
    from ahsoka.watcher.client import build_pyrogram_client

    monkeypatch.setattr(pu, "MIN_CHANNEL_ID", -1002147483647)
    settings = MagicMock(session_name="s", telegram_api_id=1, telegram_api_hash="h")

    with patch("ahsoka.watcher.client.Client"):
        build_pyrogram_client(settings)

    assert pu.MIN_CHANNEL_ID == PATCHED_MIN


# ---------------------------------------------------------------------------
# warm_peer_cache
# ---------------------------------------------------------------------------


def _dialogs_client(chat_ids):
    async def get_dialogs():
        for cid in chat_ids:
            yield MagicMock(chat=MagicMock(id=cid))

    client = MagicMock()
    client.get_dialogs = get_dialogs
    return client


async def test_warm_peer_cache_logs_member_and_not_member(caplog):
    from ahsoka.watcher.client import warm_peer_cache

    client = _dialogs_client([-1001, -1009])  # -1009 is not watched
    with caplog.at_level("INFO", logger="ahsoka.watcher.client"):
        await warm_peer_cache(client, {-1001, -1002})

    info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    warn = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("Confirmed member of" in m and "-1001" in m for m in info)
    assert any("NOT a member" in m and "-1002" in m and "-1001" not in m for m in warn)


async def test_warm_peer_cache_all_joined_logs_no_warning(caplog):
    from ahsoka.watcher.client import warm_peer_cache

    with caplog.at_level("INFO", logger="ahsoka.watcher.client"):
        await warm_peer_cache(_dialogs_client([-1001]), {-1001})

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


async def test_warm_peer_cache_none_joined_logs_no_confirmation(caplog):
    from ahsoka.watcher.client import warm_peer_cache

    with caplog.at_level("INFO", logger="ahsoka.watcher.client"):
        await warm_peer_cache(_dialogs_client([]), {-1001})

    assert not [r for r in caplog.records if "Confirmed" in r.getMessage()]
    assert [r for r in caplog.records if "NOT a member" in r.getMessage()]


async def test_warm_peer_cache_swallows_error_and_logs_warning_with_exc_info(caplog):
    from ahsoka.watcher.client import warm_peer_cache

    async def boom():
        raise RuntimeError("FLOOD_WAIT")
        yield  # pragma: no cover - makes this an async generator

    client = MagicMock()
    client.get_dialogs = boom

    with caplog.at_level("INFO", logger="ahsoka.watcher.client"):
        await warm_peer_cache(client, {-1001})  # must not raise

    recs = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(recs) == 1
    assert "warm-up failed" in recs[0].getMessage()
    assert recs[0].exc_info is not None


# ---------------------------------------------------------------------------
# channel_poller: ready gate + per-channel backoff
# ---------------------------------------------------------------------------

_real_sleep = asyncio.sleep


class _PollerHarness:
    """
    Drives channel_poller on a virtual clock. Patched sleep advances `offset`,
    loop.time() is shifted by `offset`, and fetch attempts are recorded as
    (virtual_time, channel_id). `script[channel_id]` is a list of "ok"/"fail"
    outcomes consumed per fetch; the last outcome repeats.
    """

    def __init__(self, script, sweeps):
        self.script = {c: list(s) for c, s in script.items()}
        self.sweeps = sweeps
        self.offset = 0.0
        self.fetches: list[tuple[float, int]] = []
        self.sleeps: list[float] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client = MagicMock()
        self.client.get_chat_history = self._history

    async def _history(self, channel_id, limit):
        self.fetches.append((self.offset, channel_id))
        outcomes = self.script[channel_id]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if outcome == "fail":
            raise RuntimeError("Peer id invalid")
        yield f"msg-{channel_id}"

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.offset += seconds
        if len(self.sleeps) > self.sweeps:  # sleeps[0] is the startup delay
            raise asyncio.CancelledError

    def times_for(self, channel_id):
        return [t for t, c in self.fetches if c == channel_id]

    async def run(self, ready=None):
        from ahsoka.watcher.poller import channel_poller

        loop = asyncio.get_running_loop()
        real_time = loop.time
        loop.time = lambda: real_time() + self.offset  # type: ignore[method-assign]
        try:
            with patch("ahsoka.watcher.poller.asyncio.sleep", side_effect=self._sleep), \
                 patch("ahsoka.watcher.poller.Post.from_message", side_effect=lambda m: m), \
                 patch("ahsoka.watcher.poller.logger") as mock_logger:
                try:
                    await channel_poller(
                        self.client, self.queue, set(self.script), ready=ready
                    )
                except asyncio.CancelledError:
                    pass
        finally:
            del loop.time  # drop instance override, restore the bound method
        return mock_logger


async def test_channel_poller_waits_for_ready_before_first_sweep():
    from ahsoka.watcher.poller import channel_poller

    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()

    with patch("ahsoka.watcher.poller.asyncio.sleep", side_effect=h._sleep), \
         patch("ahsoka.watcher.poller.Post.from_message", side_effect=lambda m: m):
        task = asyncio.create_task(channel_poller(h.client, h.queue, {-1001}, ready=ready))
        for _ in range(10):
            await _real_sleep(0)
        assert h.fetches == []  # still gated on ready
        assert not task.done()

        ready.set_result(None)
        try:
            await asyncio.wait_for(task, 1)
        except asyncio.CancelledError:
            pass

    assert len(h.fetches) == 1
    assert h.queue.qsize() == 1


async def test_channel_poller_sweeps_even_if_ready_failed():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()
    ready.set_exception(RuntimeError("warm-up blew up"))

    await h.run(ready=ready)

    assert len(h.fetches) == 1
    assert h.queue.qsize() == 1
    ready.exception()  # mark retrieved


def _timeout_warnings(logger_mock):
    return [
        c for c in logger_mock.warning.call_args_list
        if "warm-up still running" in c.args[0]
    ]


async def test_channel_poller_ready_timeout_warns_once_and_still_sweeps():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=2)
    ready = asyncio.get_running_loop().create_future()  # never completes

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 0.05):
        logger_mock = await h.run(ready=ready)

    warnings = _timeout_warnings(logger_mock)
    assert len(warnings) == 1
    assert warnings[0].args[1] == 0  # int(0.05) formatted into the message
    assert len(h.fetches) == 2  # sweeps continued after the timeout
    assert h.queue.qsize() == 2


async def test_channel_poller_ready_timeout_does_not_cancel_ready():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 0.05):
        await h.run(ready=ready)

    assert len(h.fetches) == 1
    assert not ready.done()
    assert not ready.cancelled()
    ready.cancel()  # cleanup


async def test_channel_poller_ready_timeout_leaves_real_task_running():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    release = asyncio.Event()
    warm = asyncio.create_task(release.wait())

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 0.05):
        await h.run(ready=warm)

    assert len(h.fetches) == 1
    assert not warm.done()
    release.set()
    await warm


async def test_channel_poller_ready_completes_before_timeout_no_warning():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()
    ready.set_result(None)

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 5.0):
        logger_mock = await h.run(ready=ready)

    assert _timeout_warnings(logger_mock) == []
    assert len(h.fetches) == 1


async def test_channel_poller_ready_failed_before_timeout_no_warning():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()
    ready.set_exception(RuntimeError("warm-up blew up"))

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 5.0):
        logger_mock = await h.run(ready=ready)

    assert _timeout_warnings(logger_mock) == []
    assert len(h.fetches) == 1
    ready.exception()  # mark retrieved


async def test_channel_poller_ready_none_skips_wait_and_warning():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)

    with patch("ahsoka.watcher.poller.asyncio.wait") as mock_wait:
        logger_mock = await h.run(ready=None)

    mock_wait.assert_not_called()
    assert _timeout_warnings(logger_mock) == []
    assert len(h.fetches) == 1


async def test_channel_poller_ready_wait_uses_ready_timeout_value():
    h = _PollerHarness({-1001: ["ok"]}, sweeps=1)
    ready = asyncio.get_running_loop().create_future()
    ready.set_result(None)

    real_wait = asyncio.wait
    seen = {}

    async def spy(fs, timeout=None):
        seen["timeout"] = timeout
        return await real_wait(fs, timeout=timeout)

    with patch("ahsoka.watcher.poller.READY_TIMEOUT", 7.5), \
         patch("ahsoka.watcher.poller.asyncio.wait", side_effect=spy):
        await h.run(ready=ready)

    assert seen["timeout"] == 7.5


async def test_channel_poller_first_failure_logs_exc_info_and_uses_base_delay():
    h = _PollerHarness({-1001: ["fail"]}, sweeps=3)

    logger_mock = await h.run()

    first = logger_mock.warning.call_args_list[0]
    assert first.kwargs.get("exc_info") is True
    # sweep 1 fails at t=10, retried at t=70 (POLL_INTERVAL == 60), not earlier
    assert h.times_for(-1001)[:2] == [10.0, 70.0]


async def test_channel_poller_backoff_doubles_and_caps_at_max():
    from ahsoka.watcher.poller import MAX_BACKOFF, POLL_INTERVAL

    h = _PollerHarness({-1001: ["fail"]}, sweeps=60)

    logger_mock = await h.run()

    times = h.times_for(-1001)
    deltas = [b - a for a, b in zip(times, times[1:])]
    assert deltas[:4] == [POLL_INTERVAL, 120.0, 240.0, 480.0]
    assert deltas[4:] and all(d == MAX_BACKOFF for d in deltas[4:])
    # later failures: one-line warning without traceback
    later = logger_mock.warning.call_args_list[1:]
    assert later
    assert all("exc_info" not in c.kwargs for c in later)


async def test_channel_poller_channel_in_backoff_is_skipped():
    h = _PollerHarness({-1001: ["fail"]}, sweeps=3)

    await h.run()

    # Sweeps ran at t=10, 70, 130; the second failure sets a 120s backoff, so
    # the third sweep at t=130 must NOT fetch.
    assert h.times_for(-1001) == [10.0, 70.0]


async def test_channel_poller_failing_channel_does_not_block_others():
    h = _PollerHarness({-1001: ["fail"], -1002: ["ok"]}, sweeps=3)

    await h.run()

    assert len(h.times_for(-1002)) == 3  # polled every sweep
    assert len(h.times_for(-1001)) == 2  # failed, backed off
    assert h.queue.qsize() == 3
    items = []
    while not h.queue.empty():
        items.append(h.queue.get_nowait())
    assert set(items) == {"msg--1002"}


async def test_channel_poller_success_resets_failure_count_and_traceback_logging():
    # fail, fail, ok, fail: after the success the next failure is "first" again.
    h = _PollerHarness({-1001: ["fail", "fail", "ok", "fail"]}, sweeps=12)

    logger_mock = await h.run()

    times = h.times_for(-1001)
    # t=10 fail(b60), t=70 fail(b120), t=190 ok, t=250 fail(b60 again), t=310
    assert times[:5] == [10.0, 70.0, 190.0, 250.0, 310.0]
    calls = logger_mock.warning.call_args_list
    with_tb = [c for c in calls if c.kwargs.get("exc_info") is True]
    # failures #1 (before reset) and the first failure after reset both carry tracebacks
    assert len(with_tb) == 2
    assert h.queue.qsize() == 1
