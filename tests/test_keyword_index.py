"""Tests for ahsoka.pipeline.keyword_index.KeywordIndex."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiosqlite

from ahsoka.database import init_db
from ahsoka.models import Post, UserConfig
from ahsoka.pipeline.keyword_index import KeywordIndex


def make_post(text: str = "Python backend developer") -> Post:
    return Post(
        channel_id=1,
        message_id=1,
        channel_name="testchan",
        text=text,
        timestamp=datetime.now(),
    )


def make_config(user_id: int = 1, keywords: str = "python golang") -> UserConfig:
    return UserConfig(user_id=user_id, notify_chat_id=user_id, keywords=keywords)


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


# ---------------------------------------------------------------------------
# Initial state (before rebuild)
# ---------------------------------------------------------------------------


def test_passes_returns_true_before_rebuild_any_empty_is_true():
    """Before rebuild, _any_empty=True so passes() always returns True."""
    idx = KeywordIndex()
    post = make_post("Java Spring developer")
    assert idx.passes(post) is True


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


async def test_rebuild_populates_union_from_all_user_keywords(conn):
    """After rebuild with multiple users, union contains all their keywords."""
    configs = [
        make_config(user_id=1, keywords="python django"),
        make_config(user_id=2, keywords="golang rust"),
    ]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert "python" in idx._union
    assert "django" in idx._union
    assert "golang" in idx._union
    assert "rust" in idx._union


async def test_rebuild_sets_any_empty_false_when_all_have_keywords(conn):
    """All users have non-empty keywords → _any_empty is False after rebuild."""
    configs = [
        make_config(user_id=1, keywords="python"),
        make_config(user_id=2, keywords="golang"),
    ]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx._any_empty is False


async def test_rebuild_sets_any_empty_true_when_one_user_has_empty_keywords(conn):
    """One user with empty keywords → _any_empty is True after rebuild."""
    configs = [
        make_config(user_id=1, keywords="python"),
        make_config(user_id=2, keywords="   "),  # whitespace-only = empty
    ]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx._any_empty is True


async def test_rebuild_empty_configs_list(conn):
    """No active configs → union is empty, _any_empty is False."""
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=[]):
        await idx.rebuild(conn)

    assert idx._union == set()
    assert idx._any_empty is False


# ---------------------------------------------------------------------------
# passes — with populated union
# ---------------------------------------------------------------------------


async def test_passes_true_when_any_empty_is_true(conn):
    """If any user has empty keywords, passes() always returns True regardless of text."""
    configs = [make_config(user_id=1, keywords="")]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx.passes(make_post("Java Spring developer")) is True


async def test_passes_true_when_union_is_empty(conn):
    """Empty union (no configs) → passes() returns True (no filter)."""
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=[]):
        await idx.rebuild(conn)

    # _union is empty and _any_empty is False → passes returns True via `not self._union`
    assert idx.passes(make_post("anything here")) is True


async def test_passes_true_when_keyword_matches(conn):
    """Keyword present in post text → passes() returns True."""
    configs = [make_config(user_id=1, keywords="python golang")]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx.passes(make_post("We are looking for a Python developer")) is True


async def test_passes_false_when_no_keyword_matches(conn):
    """No keyword match → passes() returns False."""
    configs = [make_config(user_id=1, keywords="python golang")]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx.passes(make_post("Java Spring developer needed")) is False


async def test_passes_case_insensitive(conn):
    """Keyword matching is case-insensitive."""
    configs = [make_config(user_id=1, keywords="python")]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx.passes(make_post("PYTHON developer needed")) is True


async def test_passes_keyword_from_any_user_is_enough(conn):
    """Keyword from user-2 matches even though user-1's keywords don't."""
    configs = [
        make_config(user_id=1, keywords="rust"),
        make_config(user_id=2, keywords="java"),
    ]
    idx = KeywordIndex()
    with patch("ahsoka.pipeline.keyword_index.db.get_all_active_configs", new_callable=AsyncMock, return_value=configs):
        await idx.rebuild(conn)

    assert idx.passes(make_post("Senior Java backend role")) is True
    assert idx.passes(make_post("Frontend only role")) is False
