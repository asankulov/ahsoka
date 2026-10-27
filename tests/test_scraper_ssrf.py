"""SSRF guard tests for ahsoka.pipeline.scraper: _is_url_allowed + _fetch_one redirects."""
import asyncio
import socket
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ahsoka.models import Post
from ahsoka.pipeline.scraper import _fetch_one, _is_url_allowed, scrape_content, scrape_url

PUBLIC_IP = "93.184.216.34"


def _gai(*addrs):
    """getaddrinfo result for the given textual addresses."""
    out = []
    for a in addrs:
        fam = socket.AF_INET6 if ":" in a else socket.AF_INET
        out.append((fam, socket.SOCK_STREAM, 6, "", (a, 80)))
    return out


def _patch_dns(*addrs):
    loop = asyncio.get_running_loop()
    return patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=_gai(*addrs)))


# ---------------------------------------------------------------------------
# _is_url_allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "127.0.0.1",
    "10.1.2.3",
    "172.16.5.4",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "[::1]",
    "[fe80::1]",
    "[fc00::1]",
    "[::ffff:10.0.0.1]",
    "224.0.0.1",
    "[64:ff9b::a00:1]",
    "0.0.0.0",
    "[2002:a00:1::]",
])
async def test_is_url_allowed_blocks_non_public_ip_literals(host):
    assert await _is_url_allowed(f"http://{host}/path") is False


async def test_is_url_allowed_accepts_public_ipv4_literal():
    assert await _is_url_allowed(f"https://{PUBLIC_IP}/job") is True


async def test_is_url_allowed_accepts_public_ipv6_literal():
    assert await _is_url_allowed("https://[2606:2800:220:1:248:1893:25c8:1946]/") is True


async def test_is_url_allowed_accepts_hostname_with_only_public_records():
    with _patch_dns(PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"):
        assert await _is_url_allowed("https://jobs.example.com/x") is True


@pytest.mark.parametrize("private", ["10.0.0.5", "127.0.0.1", "fd00::1", "::ffff:192.168.0.1"])
async def test_is_url_allowed_blocks_hostname_with_any_private_record(private):
    with _patch_dns(PUBLIC_IP, private):  # one public + one private => reject
        assert await _is_url_allowed("https://rebind.example.com/") is False


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "gopher://example.com/",
    "javascript:alert(1)",
    "http:///nohost",
    "not a url",
])
async def test_is_url_allowed_blocks_non_http_schemes_and_hostless_urls(url):
    assert await _is_url_allowed(url) is False


async def test_is_url_allowed_dns_failure_returns_false():
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", new=AsyncMock(side_effect=socket.gaierror("nx"))):
        assert await _is_url_allowed("http://nonexistent.invalid/") is False


async def test_is_url_allowed_empty_dns_result_returns_false():
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=[])):
        assert await _is_url_allowed("http://empty.example.com/") is False


async def test_is_url_allowed_strips_ipv6_scope_id_from_dns_result():
    loop = asyncio.get_running_loop()
    info = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 80, 0, 2))]
    with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=info)):
        assert await _is_url_allowed("http://scoped.example.com/") is False


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


async def test_fetch_one_blocked_initial_url_never_requested():
    client = _client()
    assert await _fetch_one(client, "http://127.0.0.1/admin") is None
    client.get.assert_not_awaited()


async def test_fetch_one_public_to_private_redirect_blocked():
    url = f"http://{PUBLIC_IP}/r"
    client = _client(_resp(302, url, location="http://169.254.169.254/latest/meta-data"))
    assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1  # the private hop was never fetched


async def test_fetch_one_relative_location_is_joined_to_current_url():
    url = f"http://{PUBLIC_IP}/a/b"
    client = _client(_resp(301, url, location="/final"), _resp(200, f"http://{PUBLIC_IP}/final"))
    with patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="done"):
        assert await _fetch_one(client, url) == "done"
    assert [c.args[0] for c in client.get.await_args_list] == [url, f"http://{PUBLIC_IP}/final"]


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
    assert client.get.await_count == 6  # initial request + 5 hops, then give up


async def test_fetch_one_redirect_without_location_header_is_not_followed():
    """No Location => loop breaks; raise_for_status rejects the 3xx, so no content, one request."""
    url = f"http://{PUBLIC_IP}/x"
    client = _client(_resp(302, url, location=None))
    assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1


async def test_fetch_one_non_http_redirect_target_blocked():
    url = f"http://{PUBLIC_IP}/r"
    client = _client(_resp(302, url, location="file:///etc/passwd"))
    assert await _fetch_one(client, url) is None
    assert client.get.await_count == 1


async def test_fetch_one_dns_failure_returns_none():
    loop = asyncio.get_running_loop()
    client = _client()
    with patch.object(loop, "getaddrinfo", new=AsyncMock(side_effect=socket.gaierror("nx"))):
        assert await _fetch_one(client, "http://nonexistent.invalid/") is None
    client.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# scrape_content / scrape_url integration with the guard
# ---------------------------------------------------------------------------

def _post(urls):
    return Post(channel_id=1, message_id=1, channel_name="c", text="post text",
                url=urls[0] if urls else None, urls=urls, timestamp=datetime.now())


async def test_scrape_content_blocked_url_leaves_post_text_only():
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        get = AsyncMock()
        mock_cls.return_value.__aenter__.return_value.get = get
        result = await scrape_content(_post(["http://127.0.0.1/admin"]))
    assert result == "post text"
    get.assert_not_awaited()


async def test_scrape_content_still_skips_tg_links_and_fetches_public_url():
    post = _post(["https://t.me/chan/1", f"http://{PUBLIC_IP}/job"])
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp(200, f"http://{PUBLIC_IP}/job"))
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls, \
         patch("ahsoka.pipeline.scraper.trafilatura.extract", return_value="public body"):
        mock_cls.return_value.__aenter__.return_value = client
        result = await scrape_content(post)
    assert "public body" in result
    assert [c.args[0] for c in client.get.await_args_list] == [f"http://{PUBLIC_IP}/job"]


async def test_scrape_clients_do_not_auto_follow_redirects():
    """Redirects must be followed manually (so each hop is validated)."""
    with patch("ahsoka.pipeline.scraper.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=_resp(200, f"http://{PUBLIC_IP}/")
        )
        await scrape_content(_post([f"http://{PUBLIC_IP}/"]))
        await scrape_url(f"http://{PUBLIC_IP}/", "ctx")
    assert mock_cls.call_count == 2
    assert all(c.kwargs["follow_redirects"] is False for c in mock_cls.call_args_list)


async def test_scrape_url_blocked_url_returns_context_only():
    assert await scrape_url("http://10.0.0.1/x", "ctx text") == "ctx text"
