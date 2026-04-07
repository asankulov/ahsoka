from unittest.mock import AsyncMock, MagicMock

import pytest

from ahsoka.pipeline.tg_resolver import is_tg_link, resolve_tg_link


# ---------------------------------------------------------------------------
# is_tg_link
# ---------------------------------------------------------------------------

def test_public_url_detected():
    assert is_tg_link("https://t.me/revacancy/137378") is True


def test_public_url_http_detected():
    assert is_tg_link("http://t.me/revacancy/137378") is True


def test_private_url_detected():
    assert is_tg_link("https://t.me/c/1234567890/5") is True


def test_non_tg_url_not_detected():
    assert is_tg_link("https://example.com/job") is False


def test_username_only_not_detected():
    # t.me/username with no message ID is a profile link, not a post
    assert is_tg_link("https://t.me/revacancy") is False


def test_invite_link_not_detected():
    # t.me/+hash invite links have no numeric message ID
    assert is_tg_link("https://t.me/+abc123XYZ") is False


def test_empty_string_not_detected():
    assert is_tg_link("") is False


# ---------------------------------------------------------------------------
# resolve_tg_link — mocked Pyrogram client
# ---------------------------------------------------------------------------

def make_client(text=None, caption=None, raises=None):
    client = MagicMock()
    if raises:
        client.get_messages = AsyncMock(side_effect=raises)
    else:
        msg = MagicMock()
        msg.text = text
        msg.caption = caption
        client.get_messages = AsyncMock(return_value=msg)
    return client


async def test_resolve_public_returns_text():
    client = make_client(text="Full job description here")
    result = await resolve_tg_link("https://t.me/revacancy/137378", client)
    assert result == "Full job description here"
    client.get_messages.assert_awaited_once_with("revacancy", 137378)


async def test_resolve_private_returns_text():
    client = make_client(text="Private channel JD")
    result = await resolve_tg_link("https://t.me/c/1234567890/5", client)
    assert result == "Private channel JD"
    client.get_messages.assert_awaited_once_with(-1001234567890, 5)


async def test_resolve_caption_fallback():
    # msg.text is None, should fall back to caption
    client = make_client(text=None, caption="Job caption text")
    result = await resolve_tg_link("https://t.me/chan/1", client)
    assert result == "Job caption text"


async def test_resolve_text_preferred_over_caption():
    client = make_client(text="Main text", caption="Caption text")
    result = await resolve_tg_link("https://t.me/chan/1", client)
    assert result == "Main text"


async def test_resolve_pyrogram_error_returns_none():
    client = make_client(raises=Exception("flood wait"))
    result = await resolve_tg_link("https://t.me/revacancy/1", client)
    assert result is None


async def test_resolve_empty_text_and_caption_returns_none():
    client = make_client(text="", caption="")
    result = await resolve_tg_link("https://t.me/chan/1", client)
    assert result is None


async def test_resolve_none_text_and_none_caption_returns_none():
    client = make_client(text=None, caption=None)
    result = await resolve_tg_link("https://t.me/chan/1", client)
    assert result is None


async def test_resolve_non_tg_url_returns_none_without_calling_client():
    client = make_client(text="should not be called")
    result = await resolve_tg_link("https://example.com/job", client)
    assert result is None
    client.get_messages.assert_not_awaited()
