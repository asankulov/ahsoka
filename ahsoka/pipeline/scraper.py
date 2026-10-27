import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlsplit

import httpx
import trafilatura

from ahsoka.models import Post
from ahsoka.pipeline.tg_resolver import is_tg_link

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5

# Ranges whose embedded IPv4 can hide a private address (NAT64, 6to4) are rejected outright.
_BLOCKED_V6_NETS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not a plain public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_blocked_ip(ip.ipv4_mapped)
        if any(ip in net for net in _BLOCKED_V6_NETS):
            return True
    return (
        not ip.is_global  # private, CGNAT (100.64/10), link-local, loopback, reserved...
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_private
    )


async def _is_url_allowed(url: str) -> bool:
    """Resolve the URL's host and require every address to be public."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return False
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            literals = [ipaddress.ip_address(host)]
        except ValueError:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
            literals = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
        return bool(literals) and not any(_is_blocked_ip(ip) for ip in literals)
    except Exception as exc:
        logger.debug("URL safety check failed for %s: %s", url, exc)
        return False


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not await _is_url_allowed(current):
                logger.warning("SSRF guard blocked fetch of %s (from %s)", current, url)
                return None
            response = await client.get(current)
            location = response.headers.get("location") if response.is_redirect is True else None
            if not location:
                break
            current = str(response.url.join(location))
        else:
            logger.debug("Too many redirects for %s", url)
            return None
        response.raise_for_status()
        extracted = trafilatura.extract(response.text)
        if extracted:
            logger.debug("Scraped %d chars from %s", len(extracted), url)
            return extracted
    except Exception as exc:
        logger.debug("Scrape failed for %s: %s", url, exc)
    return None


async def scrape_url(url: str, context_text: str, timeout: float = 5.0) -> str:
    """Fetch a single URL and combine with the message text as context."""
    parts: list[str] = [context_text]
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        result = await _fetch_one(client, url)
    if result:
        parts.append(f"--- scraped from {url} ---\n{result}")
    return "\n\n".join(parts)


async def scrape_content(post: Post, timeout: float = 5.0) -> str:
    """Fetch all linked URLs concurrently and combine with original post text.

    Telegram deep links (t.me/channel/id) are excluded from HTTP fetching;
    they are resolved separately via the Pyrogram client in the pipeline worker.
    """
    all_urls = (getattr(post, "urls", None) or ([post.url] if post.url else []))[:3]
    http_urls = [u for u in all_urls if not is_tg_link(u)]
    parts: list[str] = [post.text]

    if http_urls:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            results = await asyncio.gather(
                *[_fetch_one(client, u) for u in http_urls],
                return_exceptions=True,
            )
        for url, result in zip(http_urls, results):
            if isinstance(result, str):
                parts.append(f"--- scraped from {url} ---\n{result}")

    return "\n\n".join(parts)
