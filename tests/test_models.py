from datetime import datetime
from unittest.mock import MagicMock

from ahsoka.models import Post


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
