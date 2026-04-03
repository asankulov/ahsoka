from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ahsoka.models import Post
from ahsoka.pipeline.scraper import scrape_content


def make_post(text: str, url: str | None = None) -> Post:
    return Post(channel_id=1, message_id=1, channel_name="test", text=text, url=url, timestamp=datetime.now())


async def test_no_url_returns_post_text():
    post = make_post("raw post text")
    assert await scrape_content(post) == "raw post text"


async def test_fallback_on_timeout():
    post = make_post("fallback text", url="http://example.com")
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        result = await scrape_content(post)
    assert result == "fallback text"


async def test_fallback_when_trafilatura_returns_none():
    post = make_post("fallback", url="http://example.com")
    mock_response = MagicMock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = lambda: None
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value=None):
            result = await scrape_content(post)
    assert result == "fallback"


async def test_returns_extracted_text():
    post = make_post("fallback", url="http://example.com")
    mock_response = MagicMock()
    mock_response.text = "<html><body><article>Job description here</article></body></html>"
    mock_response.raise_for_status = lambda: None
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="Job description here"):
            result = await scrape_content(post)
    assert result == "Job description here"
