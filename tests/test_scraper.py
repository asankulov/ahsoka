from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ahsoka.models import Post
from ahsoka.pipeline.scraper import scrape_content


def make_post(text: str, url: str | None = None, urls: list[str] | None = None) -> Post:
    resolved = urls if urls is not None else ([url] if url else [])
    return Post(channel_id=1, message_id=1, channel_name="test",
                text=text, url=url, urls=resolved, timestamp=datetime.now())


async def test_no_url_returns_post_text():
    post = make_post("raw post text")
    assert await scrape_content(post) == "raw post text"


async def test_fallback_on_timeout():
    post = make_post("fallback text", urls=["http://example.com"])
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        result = await scrape_content(post)
    assert result == "fallback text"


async def test_fallback_when_trafilatura_returns_none():
    post = make_post("fallback", urls=["http://example.com"])
    mock_response = MagicMock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = lambda: None
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value=None):
            result = await scrape_content(post)
    assert result == "fallback"


async def test_returns_extracted_text():
    post = make_post("fallback", urls=["http://example.com"])
    mock_response = MagicMock()
    mock_response.text = "<html><body><article>Job description here</article></body></html>"
    mock_response.raise_for_status = lambda: None
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="Job description here"):
            result = await scrape_content(post)
    assert "Job description here" in result
    assert "fallback" in result


async def test_scrapes_multiple_urls():
    post = make_post("original text", urls=["http://a.com", "http://b.com"])
    with patch("ahsoka.pipeline.scraper._fetch_one", new=AsyncMock(side_effect=["scraped A", "scraped B"])):
        result = await scrape_content(post)
    assert "original text" in result
    assert "scraped A" in result
    assert "scraped B" in result


async def test_partial_scrape_failure():
    post = make_post("original", urls=["http://fail.com", "http://ok.com"])
    with patch("ahsoka.pipeline.scraper._fetch_one", new=AsyncMock(side_effect=[None, "scraped ok"])):
        result = await scrape_content(post)
    assert "original" in result
    assert "scraped ok" in result
    assert "fail.com" not in result


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

async def test_http_error_status_falls_back_to_post_text():
    """A non-2xx HTTP response should be silently skipped."""
    post = make_post("fallback text", urls=["http://example.com/404"])
    mock_response = MagicMock()
    mock_response.text = ""
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        result = await scrape_content(post)
    assert result == "fallback text"


async def test_connection_error_falls_back_to_post_text():
    """A connection error should be silently skipped."""
    post = make_post("fallback text", urls=["http://unreachable.example.com"])
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await scrape_content(post)
    assert result == "fallback text"


async def test_caps_at_three_urls_even_when_post_has_more():
    """scrape_content should only attempt the first 3 URLs."""
    post = make_post("original", urls=[f"http://site{i}.com" for i in range(5)])
    fetch_calls = []

    async def counting_fetch(client, url):
        fetch_calls.append(url)
        return f"content from {url}"

    with patch("ahsoka.pipeline.scraper._fetch_one", new=counting_fetch):
        result = await scrape_content(post)

    assert len(fetch_calls) == 3
    assert "site3.com" not in result
    assert "site4.com" not in result
