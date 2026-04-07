import asyncio
import logging

import httpx
import trafilatura

from ahsoka.models import Post
from ahsoka.pipeline.tg_resolver import is_tg_link

logger = logging.getLogger(__name__)


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
        extracted = trafilatura.extract(response.text)
        if extracted:
            logger.debug("Scraped %d chars from %s", len(extracted), url)
            return extracted
    except Exception as exc:
        logger.debug("Scrape failed for %s: %s", url, exc)
    return None


async def scrape_content(post: Post, timeout: float = 5.0) -> str:
    """Fetch all linked URLs concurrently and combine with original post text.

    Telegram deep links (t.me/channel/id) are excluded from HTTP fetching;
    they are resolved separately via the Pyrogram client in the pipeline worker.
    """
    all_urls = (getattr(post, "urls", None) or ([post.url] if post.url else []))[:3]
    http_urls = [u for u in all_urls if not is_tg_link(u)]
    parts: list[str] = [post.text]

    if http_urls:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            results = await asyncio.gather(
                *[_fetch_one(client, u) for u in http_urls],
                return_exceptions=True,
            )
        for url, result in zip(http_urls, results):
            if isinstance(result, str):
                parts.append(f"--- scraped from {url} ---\n{result}")

    return "\n\n".join(parts)
