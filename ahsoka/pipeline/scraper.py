import logging

import httpx
import trafilatura

from ahsoka.models import Post

logger = logging.getLogger(__name__)


async def scrape_content(post: Post, timeout: float = 5.0) -> str:
    """Fetch the linked URL and extract readable text. Falls back to raw post text."""
    if post.url:
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(post.url)
                response.raise_for_status()
                extracted = trafilatura.extract(response.text)
                if extracted:
                    logger.debug("Scraped %d chars from %s", len(extracted), post.url)
                    return extracted
        except Exception as exc:
            logger.debug("Scrape failed for %s: %s", post.url, exc)
    return post.text
