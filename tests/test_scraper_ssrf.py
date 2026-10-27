"""SSRF guard tests for ahsoka.pipeline.scraper.

Covers the DNS-free _is_url_allowed pre-flight, the connect-time _PinnedBackend
(resolve once, validate every candidate, connect to the validated IP), client wiring
via _make_client, and hop-by-hop redirect validation in _fetch_one.
No real DNS or network: loop.getaddrinfo and AnyIOBackend.connect_tcp are stubbed.
"""
import asyncio
import logging
import socket
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpcore
import httpx
import pytest

from ahsoka.models import Post
from ahsoka.pipeline import scraper
from ahsoka.pipeline.scraper import (
    _fetch_one,
    _is_url_allowed,
    _make_client,
    _PinnedBackend,
    scrape_content,
    scrape_url,
)

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
LOGGER = "ahsoka.pipeline.scraper"


def _gai(*addrs):
    out = []
    for a in addrs:
        if ":" in a:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (a, 80, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 80)))
    return out


def _patch_dns(*answers):
    """Each call to loop.getaddrinfo yields the next answer (a tuple of addrs)."""
    loop = asyncio.get_running_loop()
    return patch.object(
        loop, "getaddrinfo", new=AsyncMock(side_effect=[_gai(*a) for a in answers])
    )


def _patch_dns_raw(**kw):
    loop = asyncio.get_running_loop()
    return patch.object(loop, "getaddrinfo", new=AsyncMock(**kw))


def _patch_connect(**kw):
    """Stub the real socket connect; records the host it was asked to connect to."""
    kw.setdefault("return_value", MagicMock(name="stream"))
    return patch.object(httpcore.AnyIOBackend, "connect_tcp", new=AsyncMock(**kw))


# ---------------------------------------------------------------------------
# _is_url_allowed: pure, DNS-free
# ---------------------------------------------------------------------------

@pytest.fixture
def no_dns():
    with patch("socket.getaddrinfo", side_effect=AssertionError("DNS used")) as m:
        yield m


@pytest.mark.parametrize("url", [
    "http://evil.com\\@127.0.0.1/",
    "http://a@b@c/",
    "ftp://x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http:///x",
    "/relative/path",
    "not a url",
    "http://[::1",  # httpx.InvalidURL
    "http://user@example.com/",
    "http://user:pw@example.com/",
    "http://:pw@example.com/",
])
def test_is_url_allowed_rejects_malformed_userinfo_and_bad_scheme(url, no_dns):
    assert _is_url_allowed(url) is False
    no_dns.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://[::1]/",  # literal blocking is connect-time, not pre-flight
    "https://example.com/job",
    f"https://{PUBLIC_IP}/x",
    "HTTP://Example.com:8080/x?y=1",
])
def test_is_url_allowed_accepts_plain_http_urls_without_dns(url, no_dns):
    assert _is_url_allowed(url) is True
    no_dns.assert_not_called()


def test_is_url_allowed_is_synchronous():
    assert not asyncio.iscoroutinefunction(_is_url_allowed)


# ---------------------------------------------------------------------------
# _PinnedBackend.connect_tcp
# ---------------------------------------------------------------------------

async def test_connect_tcp_public_answer_connects_to_validated_ip_not_hostname():
    with _patch_dns((PUBLIC_IP,)), _patch_connect() as conn:
        stream = await _PinnedBackend().connect_tcp("jobs.example.com", 443, timeout=3)
    assert stream is conn.return_value
    assert conn.await_args.args == (PUBLIC_IP, 443)
    assert conn.await_args.kwargs["timeout"] == 3


async def test_connect_tcp_rebinding_second_lookup_private_is_refused():
    backend = _PinnedBackend()
    with _patch_dns((PUBLIC_IP,), ("127.0.0.1",)), _patch_connect() as conn:
        await backend.connect_tcp("rebind.example.com", 80)
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await backend.connect_tcp("rebind.example.com", 80)
    assert conn.await_count == 1
    assert conn.await_args.args[0] == PUBLIC_IP


async def test_connect_tcp_mixed_private_and_public_answer_refused_without_connect():
    with _patch_dns(("10.0.0.5", PUBLIC_IP)), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await _PinnedBackend().connect_tcp("mixed.example.com", 80)
    conn.assert_not_awaited()


async def test_connect_tcp_public_then_private_order_also_refused():
    with _patch_dns((PUBLIC_IP, "192.168.1.1")), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await _PinnedBackend().connect_tcp("mixed.example.com", 80)
    conn.assert_not_awaited()


@pytest.mark.parametrize("blocked", [
    "::ffff:127.0.0.1",   # IPv4-mapped loopback
    "::ffff:10.0.0.1",
    "64:ff9b::a00:1",     # NAT64
    "2002:a00:1::",       # 6to4
    "::1",
    "fe80::1",
    "fd00::1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
])
async def test_connect_tcp_blocked_addresses_raise_ssrf_connect_error(blocked):
    with _patch_dns((blocked,)), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await _PinnedBackend().connect_tcp("evil.example.com", 80)
    conn.assert_not_awaited()


async def test_connect_tcp_empty_answer_raises_ssrf_connect_error():
    with _patch_dns_raw(return_value=[]), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await _PinnedBackend().connect_tcp("empty.example.com", 80)
    conn.assert_not_awaited()


async def test_connect_tcp_gaierror_becomes_connect_error_without_ssrf_marker():
    with _patch_dns_raw(side_effect=socket.gaierror("nx")), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError) as ei:
            await _PinnedBackend().connect_tcp("nx.invalid", 80)
    assert "SSRF guard" not in str(ei.value)
    assert "DNS resolution failed" in str(ei.value)
    conn.assert_not_awaited()


async def test_connect_tcp_ipv6_public_with_scope_id_stripped_before_connect():
    info = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC_V6 + "%eth0", 80, 0, 2))]
    with _patch_dns_raw(return_value=info), _patch_connect() as conn:
        await _PinnedBackend().connect_tcp("v6.example.com", 80)
    assert conn.await_args.args[0] == PUBLIC_V6


async def test_connect_tcp_ipv6_scoped_link_local_is_validated_and_blocked():
    info = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 80, 0, 2))]
    with _patch_dns_raw(return_value=info), _patch_connect() as conn:
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await _PinnedBackend().connect_tcp("scoped.example.com", 80)
    conn.assert_not_awaited()


async def test_connect_tcp_falls_back_to_second_ip_when_first_connect_fails():
    effects = [httpcore.ConnectError("refused"), MagicMock(name="stream2")]
    with _patch_dns((PUBLIC_IP, PUBLIC_IP_2)), _patch_connect(side_effect=effects) as conn:
        stream = await _PinnedBackend().connect_tcp("multi.example.com", 80)
    assert stream is effects[1]
    assert [c.args[0] for c in conn.await_args_list] == [PUBLIC_IP, PUBLIC_IP_2]


async def test_connect_tcp_all_candidates_fail_raises_last_error():
    effects = [httpcore.ConnectError("first"), httpcore.ConnectError("second")]
    with _patch_dns((PUBLIC_IP, PUBLIC_IP_2)), _patch_connect(side_effect=effects):
        with pytest.raises(httpcore.ConnectError, match="second"):
            await _PinnedBackend().connect_tcp("multi.example.com", 80)


async def test_connect_tcp_duplicate_records_tried_once():
    with _patch_dns((PUBLIC_IP, PUBLIC_IP)), _patch_connect(
        side_effect=httpcore.ConnectError("refused")
    ) as conn:
        with pytest.raises(httpcore.ConnectError):
            await _PinnedBackend().connect_tcp("dup.example.com", 80)
    assert conn.await_count == 1


# ---------------------------------------------------------------------------
# End-to-end through a real _make_client (httpx -> httpcore -> _PinnedBackend)
# ---------------------------------------------------------------------------

async def test_fetch_one_via_real_client_all_blocked_returns_none_and_warns(caplog):
    client = _make_client(5)
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER), \
             _patch_dns(("10.0.0.5",)), _patch_connect() as conn:
            assert await _fetch_one(client, "http://internal.example.com/x") is None
    finally:
        await client.aclose()
    conn.assert_not_awaited()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "SSRF guard" in warnings[0].getMessage()


async def test_fetch_one_via_real_client_mixed_answer_returns_none_and_warns(caplog):
    client = _make_client(5)
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER), \
             _patch_dns(("10.0.0.5", PUBLIC_IP)), _patch_connect() as conn:
            assert await _fetch_one(client, "http://mixed.example.com/x") is None
    finally:
        await client.aclose()
    conn.assert_not_awaited()
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_via_real_client_empty_answer_returns_none_and_warns(caplog):
    client = _make_client(5)
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER), _patch_dns_raw(return_value=[]):
            assert await _fetch_one(client, "http://empty.example.com/x") is None
    finally:
        await client.aclose()
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_via_real_client_ip_literal_loopback_blocked(caplog):
    client = _make_client(5)
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            assert await _fetch_one(client, "http://127.0.0.1:9/admin") is None
    finally:
        await client.aclose()
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_via_real_client_dns_failure_returns_none_without_ssrf_warning(caplog):
    client = _make_client(5)
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER), \
             _patch_dns_raw(side_effect=socket.gaierror("nx")):
            assert await _fetch_one(client, "http://nx.invalid/") is None
    finally:
        await client.aclose()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Scrape failed" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)


async def test_fetch_one_non_ssrf_connect_error_is_reraised_into_debug_path(caplog):
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, f"http://{PUBLIC_IP}/x") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# _fetch_one: redirects are validated hop by hop
# ---------------------------------------------------------------------------

def _resp(status, url, location=None, text="<html>ok</html>"):
    headers = {"location": location} if location else {}
    return httpx.Response(status, headers=headers, text=text, request=httpx.Request("GET", url))


def _client(*responses):
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


async def test_fetch_one_public_url_returns_content():
    url = f"http://{PUBLIC_IP}/job"
    client = _client(_resp(200, url))
    with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="Job text"):
        assert await _fetch_one(client, url) == "Job text"
    client.get.assert_awaited_once_with(url)


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "http://user@example.com/",
    "http://evil.com\\@127.0.0.1/",
])
async def test_fetch_one_rejected_initial_url_never_requested_and_warns(url, caplog):
    client = _client()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, url) is None
    client.get.assert_not_awaited()
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_redirect_to_blocked_scheme_blocked_with_warning(caplog):
    url = f"http://{PUBLIC_IP}/r"
    client = _client(_resp(302, url, location="file:///etc/passwd"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_redirect_hop_to_blocked_host_returns_none_and_warns(caplog):
    """Hop 2 is rejected at connect time by the pinned backend (ConnectError 'SSRF guard')."""
    url = f"http://{PUBLIC_IP}/r"
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        _resp(302, url, location="http://internal.example.com/secret"),
        httpx.ConnectError("SSRF guard: internal.example.com resolves to blocked address 10.0.0.5"),
    ])
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, url) is None
    assert client.get.await_count == 2
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_redirect_hop_to_userinfo_url_blocked_without_request(caplog):
    url = f"http://{PUBLIC_IP}/r"
    client = _client(_resp(302, url, location="http://admin@10.0.0.1/"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_fetch_one_relative_location_is_joined_via_response_url():
    url = f"http://{PUBLIC_IP}/a/b"
    client = _client(_resp(301, url, location="c"), _resp(200, f"http://{PUBLIC_IP}/a/c"))
    with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="done"):
        assert await _fetch_one(client, url) == "done"
    assert [c.args[0] for c in client.get.await_args_list] == [url, f"http://{PUBLIC_IP}/a/c"]


async def test_fetch_one_absolute_path_location_is_joined_to_origin():
    url = f"http://{PUBLIC_IP}/a/b"
    client = _client(_resp(301, url, location="/final"), _resp(200, f"http://{PUBLIC_IP}/final"))
    with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="done"):
        assert await _fetch_one(client, url) == "done"
    assert client.get.await_args_list[1].args[0] == f"http://{PUBLIC_IP}/final"


async def test_fetch_one_protocol_relative_location_to_loopback_joined_then_blocked(caplog):
    url = f"http://{PUBLIC_IP}/r"
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        _resp(302, url, location="//127.0.0.1/x"),
        httpx.ConnectError("SSRF guard: 127.0.0.1 resolves to blocked address 127.0.0.1"),
    ])
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await _fetch_one(client, url) is None
    # joined onto the current scheme/host-less authority, i.e. the second request targets 127.0.0.1
    assert client.get.await_args_list[1].args[0] == "http://127.0.0.1/x"
    assert any(r.levelno == logging.WARNING and "SSRF guard" in r.getMessage() for r in caplog.records)


async def test_fetch_one_follows_up_to_five_redirects():
    url = f"http://{PUBLIC_IP}/0"
    hops = [_resp(302, f"http://{PUBLIC_IP}/{i}", location=f"/{i + 1}") for i in range(5)]
    client = _client(*hops, _resp(200, f"http://{PUBLIC_IP}/5"))
    with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="made it"):
        assert await _fetch_one(client, url) == "made it"
    assert client.get.await_count == 6


async def test_fetch_one_more_than_five_redirect_hops_returns_none():
    url = f"http://{PUBLIC_IP}/0"
    hops = [_resp(302, f"http://{PUBLIC_IP}/{i}", location=f"/{i + 1}") for i in range(10)]
    client = _client(*hops)
    assert await _fetch_one(client, url) is None
    assert client.get.await_count == 6


async def test_fetch_one_redirect_without_location_header_is_not_followed():
    url = f"http://{PUBLIC_IP}/x"
    client = _client(_resp(302, url, location=None))
    assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1


# ---------------------------------------------------------------------------
# _make_client wiring
# ---------------------------------------------------------------------------

async def test_make_client_installs_pinned_backend_and_hardened_options():
    client = _make_client(5)
    try:
        assert isinstance(client._transport._pool._network_backend, _PinnedBackend)
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.timeout == httpx.Timeout(5)
    finally:
        await client.aclose()


async def test_make_client_raises_when_transport_has_no_pool():
    class NoPool:
        def __init__(self, *a, **k):
            pass

    with patch("ahsoka.pipeline.scraper.httpx.AsyncHTTPTransport", NoPool):
        with pytest.raises(RuntimeError, match="Unsupported httpx/httpcore"):
            _make_client(5)


async def test_make_client_raises_when_pool_is_wrong_type():
    class WrongPool:
        def __init__(self, *a, **k):
            self._pool = object()

    with patch("ahsoka.pipeline.scraper.httpx.AsyncHTTPTransport", WrongPool):
        with pytest.raises(RuntimeError, match="Unsupported httpx/httpcore"):
            _make_client(5)


# ---------------------------------------------------------------------------
# scrape_content / scrape_url integration with the guard
# ---------------------------------------------------------------------------

def _post(urls):
    return Post(channel_id=1, message_id=1, channel_name="c", text="post text",
                url=urls[0] if urls else None, urls=urls, timestamp=datetime.now())


def _fake_client(get):
    client = MagicMock()
    client.get = get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def test_scrape_content_blocked_url_leaves_post_text_only():
    get = AsyncMock()
    with patch("ahsoka.pipeline.scraper._make_client", return_value=_fake_client(get)):
        result = await scrape_content(_post(["http://user@example.com/admin"]))
    assert result == "post text"
    get.assert_not_awaited()


async def test_scrape_content_skips_tg_links_and_fetches_public_url():
    post = _post(["https://t.me/chan/1", f"http://{PUBLIC_IP}/job"])
    get = AsyncMock(return_value=_resp(200, f"http://{PUBLIC_IP}/job"))
    with patch("ahsoka.pipeline.scraper._make_client", return_value=_fake_client(get)), \
         patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="public body"):
        result = await scrape_content(post)
    assert "public body" in result
    assert [c.args[0] for c in get.await_args_list] == [f"http://{PUBLIC_IP}/job"]


async def test_scrape_content_only_tg_links_builds_no_client():
    with patch("ahsoka.pipeline.scraper._make_client") as mk:
        result = await scrape_content(_post(["https://t.me/chan/1"]))
    mk.assert_not_called()
    assert result == "post text"


async def test_scrape_content_and_scrape_url_both_build_clients_via_make_client():
    get = AsyncMock(return_value=_resp(200, f"http://{PUBLIC_IP}/"))
    with patch("ahsoka.pipeline.scraper._make_client", return_value=_fake_client(get)) as mk:
        await scrape_content(_post([f"http://{PUBLIC_IP}/"]), timeout=7)
        await scrape_url(f"http://{PUBLIC_IP}/", "ctx", timeout=9)
    assert [c.args for c in mk.call_args_list] == [(7,), (9,)]


async def test_scrape_url_blocked_url_returns_context_only():
    assert await scrape_url("ftp://10.0.0.1/x", "ctx text") == "ctx text"


async def test_scrape_url_appends_scraped_content_on_success():
    get = AsyncMock(return_value=_resp(200, f"http://{PUBLIC_IP}/j"))
    with patch("ahsoka.pipeline.scraper._make_client", return_value=_fake_client(get)), \
         patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="body"):
        out = await scrape_url(f"http://{PUBLIC_IP}/j", "ctx")
    assert out.startswith("ctx") and "body" in out
