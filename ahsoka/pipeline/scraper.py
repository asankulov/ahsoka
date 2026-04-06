import asyncio
import logging

import httpx
import trafilatura

from ahsoka.models import Post

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
    """Fetch all linked URLs concurrently and combine with original post text."""
    urls = (getattr(post, "urls", None) or ([post.url] if post.url else []))[:3]
    parts: list[str] = [post.text]

    if urls:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            results = await asyncio.gather(
                *[_fetch_one(client, u) for u in urls],
                return_exceptions=True,
            )
        for url, result in zip(urls, results):
            if isinstance(result, str):
                parts.append(f"--- scraped from {url} ---\n{result}")

    return "\n\n".join(parts)
