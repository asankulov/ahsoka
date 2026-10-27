import asyncio
import ipaddress
import logging
import socket

import httpcore
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


def _is_url_allowed(url: str) -> bool:
    """Cheap, DNS-free pre-flight: http(s) scheme, non-empty host, no userinfo.

    httpx.URL is the single parser (the same one that decides where the socket
    connects), so there is no parser differential. Address validation happens at
    connect time in _PinnedBackend, from the very lookup that is connected to.
    """
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as exc:
        logger.debug("Rejected malformed URL %r: %s", url, exc)
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.host) and not parsed.userinfo


def _parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(raw.split("%", 1)[0])


class _PinnedBackend(httpcore.AnyIOBackend):
    """Network backend that resolves once, validates every candidate, and connects to that IP.

    Closes the DNS-rebinding window: the address checked is the address connected.
    TLS SNI / cert verification and the Host header still use the hostname because
    httpcore derives them from the request origin, not from the connect target.
    """

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(f"DNS resolution failed for {host}: {exc}") from exc
        ips: list[str] = []
        for info in infos:
            raw = info[4][0]
            if _is_blocked_ip(_parse_ip(raw)):
                # Any blocked candidate poisons the whole answer (no cherry-picking).
                raise httpcore.ConnectError(f"SSRF guard: {host} resolves to blocked address {raw}")
            ip = raw.split("%", 1)[0]
            if ip not in ips:
                ips.append(ip)
        if not ips:
            raise httpcore.ConnectError(f"SSRF guard: {host} resolved to no addresses")
        last_exc: Exception | None = None
        for ip in ips:
            try:
                return await super().connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc


def _make_client(timeout: float) -> httpx.AsyncClient:
    """AsyncClient whose connection pool uses _PinnedBackend.

    httpx.AsyncHTTPTransport has no public network_backend hook, so we swap its
    internal pool for an httpcore.AsyncConnectionPool built with the public
    network_backend= argument. Tested with httpx 0.28.1 / httpcore 1.0.9. The
    shape check below makes an httpx upgrade fail loudly instead of silently
    disabling the pin. trust_env=False keeps env proxies from bypassing it.
    """
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    if not isinstance(getattr(transport, "_pool", None), httpcore.AsyncConnectionPool):
        raise RuntimeError("Unsupported httpx/httpcore: cannot install SSRF-pinned backend")
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        max_connections=httpx.Limits().max_connections,
        max_keepalive_connections=httpx.Limits().max_keepalive_connections,
        keepalive_expiry=httpx.Limits().keepalive_expiry,
        http1=True,
        http2=False,
        network_backend=_PinnedBackend(),
    )
    if not isinstance(transport._pool._network_backend, _PinnedBackend):
        raise RuntimeError("SSRF-pinned backend was not installed")
    return httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    )


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not _is_url_allowed(current):
                logger.warning("SSRF guard blocked fetch of %s (from %s)", current, url)
                return None
            try:
                response = await client.get(current)
            except httpx.ConnectError as exc:
                if "SSRF guard" in str(exc):
                    logger.warning("SSRF guard blocked fetch of %s (from %s): %s", current, url, exc)
                    return None
                raise
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
    async with _make_client(timeout) as client:
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
        async with _make_client(timeout) as client:
            results = await asyncio.gather(
                *[_fetch_one(client, u) for u in http_urls],
                return_exceptions=True,
            )
        for url, result in zip(http_urls, results):
            if isinstance(result, str):
                parts.append(f"--- scraped from {url} ---\n{result}")

    return "\n\n".join(parts)
