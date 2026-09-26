import enum
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ahsoka.models import Post, _entity_type_name


def make_entity(etype_value: str, url: str | None = None, offset: int = 0, length: int = 0) -> MagicMock:
    entity = MagicMock()
    entity.type = MagicMock()
    entity.type.value = etype_value
    entity.url = url
    entity.offset = offset
    entity.length = length
    return entity


def make_message(
    text: str | None = None,
    caption: str | None = None,
    chat_id: int = -100123,
    chat_username: str | None = "testchannel",
    message_id: int = 42,
    entities: list | None = None,
    date: datetime | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.id = message_id
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.username = chat_username
    msg.entities = entities or []
    msg.date = date or datetime(2024, 1, 15, 12, 0, 0)
    return msg


# ---------------------------------------------------------------------------
# Basic field mapping
# ---------------------------------------------------------------------------

def test_from_message_uses_text():
    msg = make_message(text="Hello job post")
    post = Post.from_message(msg)
    assert post.text == "Hello job post"


def test_from_message_falls_back_to_caption():
    msg = make_message(text=None, caption="Job from caption")
    post = Post.from_message(msg)
    assert post.text == "Job from caption"


def test_from_message_empty_when_neither_text_nor_caption():
    msg = make_message(text=None, caption=None)
    post = Post.from_message(msg)
    assert post.text == ""


def test_from_message_channel_id():
    msg = make_message(chat_id=-100999)
    post = Post.from_message(msg)
    assert post.channel_id == -100999


def test_from_message_message_id():
    msg = make_message(message_id=77)
    post = Post.from_message(msg)
    assert post.message_id == 77


def test_from_message_channel_name_from_username():
    msg = make_message(chat_username="jobsrus")
    post = Post.from_message(msg)
    assert post.channel_name == "jobsrus"


def test_from_message_channel_name_falls_back_to_id():
    msg = make_message(chat_username=None, chat_id=-100555)
    post = Post.from_message(msg)
    assert post.channel_name == "-100555"


def test_from_message_timestamp():
    ts = datetime(2024, 6, 1, 9, 30, 0)
    msg = make_message(date=ts)
    post = Post.from_message(msg)
    assert post.timestamp == ts


# ---------------------------------------------------------------------------
# URL extraction — text_link entities
# ---------------------------------------------------------------------------

def test_from_message_text_link_entity():
    entities = [make_entity("text_link", url="https://example.com/job")]
    msg = make_message(text="Apply here", entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://example.com/job"
    assert "https://example.com/job" in post.urls


def test_from_message_url_entity():
    text = "https://jobs.example.com"
    entities = [make_entity("url", offset=0, length=len(text))]
    msg = make_message(text=text, entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://jobs.example.com"


def test_from_message_url_entity_mid_text():
    text = "Apply at https://jobs.io now"
    offset = len("Apply at ")
    url_str = "https://jobs.io"
    entities = [make_entity("url", offset=offset, length=len(url_str))]
    msg = make_message(text=text, entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://jobs.io"


def test_from_message_no_url_when_no_entities():
    msg = make_message(text="No links here")
    post = Post.from_message(msg)
    assert post.url is None
    assert post.urls == []


def test_from_message_deduplicates_urls():
    # Two entities pointing to the same URL
    entities = [
        make_entity("text_link", url="https://dupe.com"),
        make_entity("text_link", url="https://dupe.com"),
    ]
    msg = make_message(text="link link", entities=entities)
    post = Post.from_message(msg)
    assert post.urls.count("https://dupe.com") == 1


def test_from_message_caps_urls_at_three():
    entities = [
        make_entity("text_link", url=f"https://site{i}.com")
        for i in range(5)
    ]
    msg = make_message(text="many links", entities=entities)
    post = Post.from_message(msg)
    assert len(post.urls) == 3


def test_from_message_skips_unknown_entity_types():
    entities = [
        make_entity("bold"),
        make_entity("text_link", url="https://good.com"),
    ]
    msg = make_message(text="bold text", entities=entities)
    post = Post.from_message(msg)
    assert post.urls == ["https://good.com"]


# ---------------------------------------------------------------------------
# UserConfig.is_banned field
# ---------------------------------------------------------------------------

def test_user_config_is_banned_defaults_to_false():
    from ahsoka.models import UserConfig
    config = UserConfig(user_id=1, notify_chat_id=1)
    assert config.is_banned is False


def test_user_config_is_banned_can_be_set_true():
    from ahsoka.models import UserConfig
    config = UserConfig(user_id=1, notify_chat_id=1, is_banned=True)
    assert config.is_banned is True


def test_user_config_is_banned_survives_deepcopy():
    """Batch queue snapshots use deepcopy; is_banned must not reset."""
    import copy
    from ahsoka.models import UserConfig
    config = UserConfig(user_id=1, notify_chat_id=1, is_banned=True)
    cloned = copy.deepcopy(config)
    assert cloned.is_banned is True


def test_user_config_is_banned_false_survives_deepcopy():
    import copy
    from ahsoka.models import UserConfig
    config = UserConfig(user_id=1, notify_chat_id=1, is_banned=False)
    cloned = copy.deepcopy(config)
    assert cloned.is_banned is False


# ---------------------------------------------------------------------------
# _entity_type_name / URL extraction — Pyrogram-shaped enum entity types
#
# Regression coverage for: pyrogram.enums.MessageEntityType members have
# explicit values that are raw TL *classes* (e.g. TEXT_LINK =
# raw.types.MessageEntityTextUrl), never strings. `getattr(etype, "value",
# etype)` therefore never equals "text_link"/"url" for real Pyrogram
# messages, and no URLs get extracted. The fix resolves the name via
# `.name.lower()` first. These doubles mirror that shape with a real
# `enum.Enum` subclass instead of a MagicMock, whose `.value` would
# (incorrectly) already be a plain string and so could hide this bug.
# ---------------------------------------------------------------------------

class _RawEntityUrl:
    """Stand-in for pyrogram.raw.types.MessageEntityUrl — a non-string sentinel."""


class _RawEntityTextUrl:
    """Stand-in for pyrogram.raw.types.MessageEntityTextUrl."""


class _RawEntityMention:
    """Stand-in for pyrogram.raw.types.MessageEntityMention."""


class FakeMessageEntityType(enum.Enum):
    """Mirrors pyrogram.enums.MessageEntityType's shape: `.name` is the
    string member name, `.value` is a distinct non-string raw-TL-like class.
    Distinct classes per member matter — identical values would alias in
    a real Enum and silently collapse the members being tested.
    """
    URL = _RawEntityUrl
    TEXT_LINK = _RawEntityTextUrl
    MENTION = _RawEntityMention


def make_enum_entity(etype: FakeMessageEntityType, url: str | None = None, offset: int = 0, length: int = 0):
    """Like make_entity, but `.type` is a real enum member (not a MagicMock),
    matching what a live Pyrogram MessageEntity actually carries.
    """
    entity = MagicMock()
    entity.type = etype
    entity.url = url
    entity.offset = offset
    entity.length = length
    return entity


def test_from_message_enum_url_entity_extracted():
    """THE regression test: Pyrogram's real MessageEntityType.URL has a
    non-string `.value` (a raw TL class). Before the fix, `getattr(etype,
    "value", etype)` returned that class object, it never matched "url",
    and no URL was extracted from live/polled messages.
    """
    text = "Apply at https://jobs.io now"
    offset = len("Apply at ")
    url_str = "https://jobs.io"
    entities = [make_enum_entity(FakeMessageEntityType.URL, offset=offset, length=len(url_str))]
    msg = make_message(text=text, entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://jobs.io"
    assert post.urls == ["https://jobs.io"]


def test_from_message_enum_text_link_entity_extracted():
    entities = [make_enum_entity(FakeMessageEntityType.TEXT_LINK, url="https://example.com/job")]
    msg = make_message(text="Apply here", entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://example.com/job"
    assert "https://example.com/job" in post.urls


def test_from_message_enum_other_type_skipped():
    entities = [make_enum_entity(FakeMessageEntityType.MENTION)]
    msg = make_message(text="@someone mentioned", entities=entities)
    post = Post.from_message(msg)
    assert post.urls == []
    assert post.url is None


def test_from_message_enum_mixed_text_link_and_url_respects_order_and_dedup():
    text = "See https://a.com and also this"
    url_len = len("https://a.com")
    entities = [
        make_enum_entity(FakeMessageEntityType.TEXT_LINK, url="https://b.com"),
        make_enum_entity(FakeMessageEntityType.URL, offset=text.index("https://a.com"), length=url_len),
        make_enum_entity(FakeMessageEntityType.TEXT_LINK, url="https://b.com"),  # duplicate
    ]
    msg = make_message(text=text, entities=entities)
    post = Post.from_message(msg)
    assert post.urls == ["https://b.com", "https://a.com"]
    assert post.url == "https://b.com"


def test_from_message_plain_string_type_url_resolves():
    text = "https://jobs.example.com"
    entity = MagicMock()
    entity.type = "url"  # plain string, not an enum/MagicMock-with-.value
    entity.offset = 0
    entity.length = len(text)
    msg = make_message(text=text, entities=[entity])
    post = Post.from_message(msg)
    assert post.url == "https://jobs.example.com"


def test_from_message_plain_string_type_text_link_resolves():
    entity = MagicMock()
    entity.type = "text_link"
    entity.url = "https://example.com/direct"
    msg = make_message(text="Apply here", entities=[entity])
    post = Post.from_message(msg)
    assert post.url == "https://example.com/direct"


class _UnresolvableEntityType:
    """Neither `.name` nor `.value` is a string (e.g. raw ints), and the
    object itself is not a string either — must resolve to None and the
    entity must be skipped without raising.
    """
    name = 1
    value = 2


def test_from_message_unresolvable_entity_type_skipped_without_exception():
    entity = MagicMock()
    entity.type = _UnresolvableEntityType()
    entity.url = "https://should-not-appear.com"
    msg = make_message(text="whatever", entities=[entity])
    post = Post.from_message(msg)
    assert post.urls == []
    assert post.url is None


def test_from_message_magicmock_fallback_to_value_still_works():
    """Existing MagicMock-based double (its `.type` is itself a MagicMock
    whose `.value` attribute is a plain string) must keep resolving via the
    `.value` fallback branch of `_entity_type_name`.
    """
    entities = [make_entity("text_link", url="https://legacy.example.com")]
    msg = make_message(text="legacy path", entities=entities)
    post = Post.from_message(msg)
    assert post.url == "https://legacy.example.com"


# ---------------------------------------------------------------------------
# _entity_type_name — direct unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "etype, expected",
    [
        (FakeMessageEntityType.URL, "url"),
        (FakeMessageEntityType.TEXT_LINK, "text_link"),
        (FakeMessageEntityType.MENTION, "mention"),
        ("url", "url"),
        ("TEXT_LINK", "TEXT_LINK"),  # plain strings are used as-is, not lowercased
        (_UnresolvableEntityType(), None),
        (None, None),
    ],
)
def test_entity_type_name_parametrized(etype, expected):
    assert _entity_type_name(etype) == expected


def test_entity_type_name_magicmock_string_value_fallback():
    etype = MagicMock()
    etype.value = "url"
    # MagicMock auto-generates a `.name` attribute that is itself a MagicMock,
    # not a string, so this must fall through to the `.value` branch.
    assert _entity_type_name(etype) == "url"
