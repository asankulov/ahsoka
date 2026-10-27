"""Tests for ahsoka.text_utils.slice_utf16 and entity_type_name."""
import enum
from unittest.mock import MagicMock

import pytest

from ahsoka.text_utils import (
    MAX_POST_URLS,
    build_message_link,
    button_urls_from_pyrogram,
    button_urls_from_raw,
    entity_type_name,
    merge_urls,
    slice_utf16,
)


def test_slice_utf16_emoji_before_url_extracts_full_url():
    """The user's motivating example: emoji occupies 2 UTF-16 units, plain str
    slicing by those offsets truncates the URL — slice_utf16 must not."""
    text = "\U0001F680 Apply https://jobs.io now"
    assert slice_utf16(text, 9, 15) == "https://jobs.io"


def test_slice_utf16_ascii_only_matches_plain_str_slicing():
    text = "Apply at https://jobs.example.com/1 today"
    offset = text.index("https://jobs.example.com/1")
    length = len("https://jobs.example.com/1")
    assert slice_utf16(text, offset, length) == text[offset:offset + length]


def test_slice_utf16_multiple_astral_chars_compounding_offset():
    """Two astral emoji before the entity — offset must account for 2 UTF-16
    units each (4 units total), not 2 Python str indices."""
    text = "\U0001F680\U0001F525 https://x.io end"
    # UTF-16 units: 2 (rocket) + 2 (fire) + 1 (space) = 5, then URL starts.
    url = "https://x.io"
    assert slice_utf16(text, 5, len(url)) == url


def test_slice_utf16_entity_at_offset_zero():
    text = "https://start.io rest of text"
    url = "https://start.io"
    assert slice_utf16(text, 0, len(url)) == url


def test_slice_utf16_offset_plus_length_beyond_text_clamps():
    text = "short"
    # Way beyond the end — should clamp like plain str slicing, not raise.
    assert slice_utf16(text, 2, 1000) == "ort"


def test_slice_utf16_offset_beyond_text_returns_empty():
    text = "short"
    assert slice_utf16(text, 100, 5) == ""


def test_slice_utf16_zero_length_returns_empty_string():
    text = "https://jobs.io"
    assert slice_utf16(text, 0, 0) == ""


def test_slice_utf16_negative_length_returns_empty_without_raising():
    text = "https://jobs.io"
    assert slice_utf16(text, 5, -3) == ""


def test_slice_utf16_offset_splitting_surrogate_pair_does_not_raise():
    """An offset that lands inside an astral char's surrogate pair would
    normally corrupt UTF-16 decoding; errors='ignore' must drop the unpaired
    surrogate instead of raising UnicodeDecodeError."""
    text = "\U0001F680abc"  # rocket (2 UTF-16 units) + 'abc'
    # offset=1 splits the rocket's surrogate pair in half: the low surrogate
    # half is dropped by decode(errors="ignore"), leaving just "ab".
    assert slice_utf16(text, 1, 3) == "ab"


def test_slice_utf16_lone_surrogate_in_input_does_not_raise():
    """A lone (unpaired) surrogate already present in `text` — e.g. from
    malformed upstream input — must not raise UnicodeEncodeError on encode
    (text_utils encodes with errors='surrogatepass'), and is then dropped by
    the decode(errors='ignore') step, same as a split surrogate pair."""
    text = "a\ud800b"
    assert slice_utf16(text, 0, len(text)) == "ab"


# ---------------------------------------------------------------------------
# entity_type_name
#
# Regression coverage for: pyrogram.enums.MessageEntityType members have
# explicit values that are raw TL *classes* (e.g. TEXT_LINK =
# raw.types.MessageEntityTextUrl), never strings. Resolution must go through
# `.name.lower()` first, falling back to a plain string or a string `.value`
# for other client shapes. These doubles mirror the real Pyrogram shape with
# a real `enum.Enum` subclass instead of a MagicMock, whose `.value` would
# (incorrectly) already be a plain string and so could hide this bug.
#
# `Post.from_message`'s end-to-end use of this helper is covered separately
# in tests/test_models.py; these are the direct unit tests.
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


class _UnresolvableEntityType:
    """Neither `.name` nor `.value` is a string (e.g. raw ints), and the
    object itself is not a string either — must resolve to None.
    """
    name = 1
    value = 2


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
    assert entity_type_name(etype) == expected


def test_entity_type_name_magicmock_string_value_fallback():
    etype = MagicMock()
    etype.value = "url"
    # MagicMock auto-generates a `.name` attribute that is itself a MagicMock,
    # not a string, so this must fall through to the `.value` branch.
    assert entity_type_name(etype) == "url"


# ---------------------------------------------------------------------------
# build_message_link
# ---------------------------------------------------------------------------

def test_build_message_link_username_uses_public_form():
    assert build_message_link(-1001234567890, 7, "jobchan") == "https://t.me/jobchan/7"


def test_build_message_link_username_wins_even_for_basic_group_id():
    assert build_message_link(-4567, 7, "mygroup") == "https://t.me/mygroup/7"


def test_build_message_link_supergroup_without_username_uses_c_form():
    assert build_message_link(-1001234567890, 7) == "https://t.me/c/1234567890/7"


def test_build_message_link_basic_group_without_username_is_none():
    assert build_message_link(-4567, 7) is None


def test_build_message_link_boundary_is_c_form_and_just_above_is_none():
    assert build_message_link(-(10**12), 7) == "https://t.me/c/0000000000/7"
    assert build_message_link(-(10**12 - 1), 7) is None


# ---------------------------------------------------------------------------
# merge_urls / button URL extraction
# ---------------------------------------------------------------------------


def test_merge_urls_priority_order_and_dedupe():
    assert merge_urls(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_merge_urls_caps_at_default_limit_keeping_first_group():
    assert MAX_POST_URLS == 3
    assert merge_urls(["a", "b"], ["c", "d", "e"]) == ["a", "b", "c"]


def test_merge_urls_custom_limit():
    assert merge_urls(["a", "b", "c"], limit=1) == ["a"]


def test_merge_urls_drops_empty_and_non_str_and_none_groups():
    assert merge_urls(None, ["", None, 5, "a"], []) == ["a"]


def test_merge_urls_no_groups_empty():
    assert merge_urls() == []


def _btn(url):
    b = MagicMock()
    b.url = url
    return b


def test_button_urls_from_pyrogram_extracts_in_row_order():
    markup = MagicMock()
    markup.inline_keyboard = [[_btn("https://a"), _btn("https://b")], [_btn("https://c")]]
    assert button_urls_from_pyrogram(markup) == ["https://a", "https://b", "https://c"]


def test_button_urls_from_pyrogram_ignores_buttons_without_str_url():
    markup = MagicMock()
    markup.inline_keyboard = [[_btn(None), _btn(""), _btn(123), _btn("https://ok")]]
    assert button_urls_from_pyrogram(markup) == ["https://ok"]


def test_button_urls_from_pyrogram_none_and_bad_shapes():
    assert button_urls_from_pyrogram(None) == []
    assert button_urls_from_pyrogram(MagicMock()) == []  # inline_keyboard is a MagicMock
    bad = MagicMock()
    bad.inline_keyboard = ["not-a-row", [_btn("https://ok")]]
    assert button_urls_from_pyrogram(bad) == ["https://ok"]


class _UrlBtn:
    def __init__(self, url):
        self.url = url


class _CbBtn:
    url = "https://should-not-appear"


class _Row:
    def __init__(self, buttons):
        self.buttons = buttons


class _Markup:
    def __init__(self, rows):
        self.rows = rows


def test_button_urls_from_raw_only_url_type_buttons():
    markup = _Markup([_Row([_UrlBtn("https://a"), _CbBtn()]), _Row([_UrlBtn("https://b")])])
    assert button_urls_from_raw(markup, _UrlBtn) == ["https://a", "https://b"]


def test_button_urls_from_raw_empty_and_non_str_urls_skipped():
    markup = _Markup([_Row([_UrlBtn(""), _UrlBtn(None), _UrlBtn(7), _UrlBtn("https://ok")])])
    assert button_urls_from_raw(markup, _UrlBtn) == ["https://ok"]


def test_button_urls_from_raw_missing_or_bad_shapes():
    assert button_urls_from_raw(None, _UrlBtn) == []
    assert button_urls_from_raw(MagicMock(), _UrlBtn) == []  # rows not a list
    markup = _Markup([_Row(MagicMock()), _Row([_UrlBtn("https://ok")])])  # buttons not a list
    assert button_urls_from_raw(markup, _UrlBtn) == ["https://ok"]
