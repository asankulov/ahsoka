import logging
import re

import httpx

from ahsoka.config import settings

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"

_WS = re.compile(r"\s+")
_tokens = 0


def normalize(text: str, max_chars: int) -> str:
    return _WS.sub(" ", text).strip()[:max_chars]


def take_token_count() -> int:
    """Return tokens accumulated since the last call and reset to 0."""
    global _tokens
    n, _tokens = _tokens, 0
    return n


async def _post(client: httpx.AsyncClient, text: str) -> list[float] | None:
    global _tokens
    resp = await client.post(
        VOYAGE_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={
            "model": settings.voyage_model,
            "input": [text],
            "input_type": "document",
            "output_dimension": settings.repost_dimensions,
        },
    )
    if not resp.is_success:
        logger.warning("Voyage embeddings request failed: HTTP %d", resp.status_code)
        return None
    body = resp.json()
    try:
        embedding = body["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Voyage embeddings response missing data[0].embedding")
        return None
    if (
        not isinstance(embedding, list)
        or not embedding
        or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in embedding
        )
    ):
        logger.warning("Voyage embeddings response has invalid embedding")
        return None
    try:
        _tokens += int(body["usage"]["total_tokens"])
    except (KeyError, TypeError, ValueError):
        pass
    return embedding


async def embed(text: str, *, client: httpx.AsyncClient | None = None) -> list[float] | None:
    """Embed text via Voyage. Returns None when disabled, empty, or on any failure."""
    if not settings.voyage_api_key:
        return None
    normalized = normalize(text, settings.repost_max_chars)
    if not normalized:
        return None
    try:
        if client is not None:
            return await _post(client, normalized)
        async with httpx.AsyncClient(timeout=settings.embed_timeout_s) as c:
            return await _post(c, normalized)
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Voyage embeddings request error: %s", e)
        return None
