import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import httpx
import pytest

from ahsoka import database as db
from ahsoka.database import init_db
from ahsoka.pipeline import reconcile
from ahsoka.pipeline.reconcile import fetch_billed_usd, reconcile_day

KEY = "sk-ant-admin-SECRETKEY123"


def _settings(key=KEY):
    s = MagicMock()
    s.anthropic_admin_key = key
    return s


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _item(amount):
    return {
        "amount": amount, "context_window": "0-200k", "cost_type": "tokens",
        "currency": "USD", "description": "Claude Usage - Input Tokens",
        "inference_geo": "global", "model": "claude-haiku-5-5",
        "service_tier": "batch", "speed": "standard", "token_type": "uncached_input_tokens",
        "workspace_id": "wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ",
    }


def _bucket(amounts, start="2026-11-04T00:00:00Z", end="2026-11-05T00:00:00Z"):
    return {"starting_at": start, "ending_at": end, "results": [_item(a) for a in amounts]}


def _body(buckets, has_more=False, next_page=None):
    return {"data": buckets, "has_more": has_more, "next_page": next_page}


def _recorder(body=None, status=200, content=None):
    def handler(request):
        handler.requests.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body if body is not None else _body([]))

    handler.requests = []
    return handler


async def _fetch(handler, day="2026-11-04"):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()):
        return await fetch_billed_usd(day, client=_client(handler))


async def test_fetch_billed_sums_cents_to_usd():
    h = _recorder(_body([_bucket(["123.45", "100.00"])]))
    assert await _fetch(h) == pytest.approx(2.2345)


async def test_fetch_billed_empty_results_is_zero():
    h = _recorder(_body([_bucket([])]))
    assert await _fetch(h) == 0.0


async def test_fetch_billed_no_buckets_is_zero():
    assert await _fetch(_recorder(_body([]))) == 0.0


async def test_fetch_billed_sums_across_buckets():
    h = _recorder(_body([_bucket(["50"]), _bucket(["150"])]))
    assert await _fetch(h) == pytest.approx(2.0)


async def test_fetch_billed_sends_expected_request():
    h = _recorder(_body([_bucket(["100"])]))
    await _fetch(h, day="2026-11-04")
    req = h.requests[0]
    assert req.method == "GET"
    assert req.url.scheme == "https"
    assert req.url.host == "api.anthropic.com"
    assert req.url.path == "/v1/organizations/cost_report"
    assert req.headers["x-api-key"] == KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    q = req.url.params
    assert q["starting_at"] == "2026-11-04T00:00:00Z"
    assert q["ending_at"] == "2026-11-05T00:00:00Z"
    assert q["bucket_width"] == "1d"
    assert q["limit"] == "31"
    assert "page" not in q


async def test_fetch_billed_ending_at_rolls_over_month_end():
    h = _recorder(_body([_bucket([])]))
    await _fetch(h, day="2026-11-30")
    q = h.requests[0].url.params
    assert q["starting_at"] == "2026-11-30T00:00:00Z"
    assert q["ending_at"] == "2026-12-01T00:00:00Z"


async def test_fetch_billed_follows_pagination():
    def handler(request):
        handler.requests.append(request)
        if "page" not in request.url.params:
            return httpx.Response(200, json=_body([_bucket(["100"])], True, "page_abc"))
        return httpx.Response(200, json=_body([_bucket(["250"])], False, None))

    handler.requests = []
    assert await _fetch(handler) == pytest.approx(3.5)
    assert len(handler.requests) == 2
    assert handler.requests[1].url.params["page"] == "page_abc"
    assert handler.requests[1].url.params["starting_at"] == "2026-11-04T00:00:00Z"


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_fetch_billed_non_200_returns_none_and_warns(status, caplog):
    h = _recorder(status=status, content=b'{"error": "nope"}')
    with caplog.at_level(logging.WARNING):
        assert await _fetch(h) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert KEY not in caplog.text


async def test_fetch_billed_malformed_json_returns_none(caplog):
    h = _recorder(content=b"<html>not json</html>")
    with caplog.at_level(logging.WARNING):
        assert await _fetch(h) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_fetch_billed_wrong_shape_returns_none():
    assert await _fetch(_recorder({"unexpected": True})) is None


async def test_fetch_billed_non_numeric_amount_returns_none(caplog):
    h = _recorder(_body([_bucket(["12.5", "abc"])]))
    with caplog.at_level(logging.WARNING):
        assert await _fetch(h) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_fetch_billed_timeout_returns_none(caplog):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with caplog.at_level(logging.WARNING):
        assert await _fetch(handler) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert KEY not in caplog.text


async def test_fetch_billed_connect_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert await _fetch(handler) is None


async def test_fetch_billed_failure_on_second_page_returns_none():
    def handler(request):
        if "page" not in request.url.params:
            return httpx.Response(200, json=_body([_bucket(["100"])], True, "p2"))
        return httpx.Response(500, content=b"err")

    assert await _fetch(handler) is None


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


async def test_reconcile_day_no_key_makes_no_http_call(conn):
    def handler(request):
        raise AssertionError("HTTP must not be called without an admin key")

    # Even if a real client were built, nothing may be sent; also guard httpx globally.
    with patch("ahsoka.pipeline.reconcile.settings", _settings(key="")), \
         patch("httpx.AsyncClient", side_effect=AssertionError("client built")), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(side_effect=AssertionError)):
        await reconcile_day(conn, "2026-11-04")
    assert await db.get_reconciliation(conn, "2026-11-04") is None


async def test_reconcile_day_stores_billed_vs_estimated(conn):
    await db.record_embedding_usage(conn, "2026-11-04", "voyage-4-lite", 1000, 0.25)
    buckets = [{"bucket": "2026-11-04", "scoring_usd": 1.5, "embedding_usd": 0.25}]
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=2.2345)) as fb, \
         patch("ahsoka.database.get_cost_buckets", new=AsyncMock(return_value=buckets)):
        await reconcile_day(conn, "2026-11-04")
    fb.assert_awaited_once()
    assert fb.await_args.args[0] == "2026-11-04"
    rec = await db.get_reconciliation(conn, "2026-11-04")
    assert rec["billed_usd"] == pytest.approx(2.2345)
    assert rec["estimated_usd"] == pytest.approx(1.75)  # scoring + embedding


async def test_reconcile_day_estimate_uses_only_requested_day(conn):
    buckets = [
        {"bucket": "2026-11-05", "scoring_usd": 9.0, "embedding_usd": 9.0},
        {"bucket": "2026-11-04", "scoring_usd": 1.0, "embedding_usd": 0.5},
    ]
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=2.0)), \
         patch("ahsoka.database.get_cost_buckets", new=AsyncMock(return_value=buckets)):
        await reconcile_day(conn, "2026-11-04")
    assert (await db.get_reconciliation(conn, "2026-11-04"))["estimated_usd"] == pytest.approx(1.5)


async def test_reconcile_day_no_bucket_estimates_zero(conn):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=0.4)):
        await reconcile_day(conn, "2026-11-04")
    rec = await db.get_reconciliation(conn, "2026-11-04")
    assert rec == {"billed_usd": pytest.approx(0.4), "estimated_usd": 0.0}


async def test_reconcile_day_end_to_end_with_mock_transport(conn):
    h = _recorder(_body([_bucket(["123.45", "100.00"])]))
    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        return real_client(transport=httpx.MockTransport(h))

    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.httpx.AsyncClient", side_effect=factory):
        await reconcile_day(conn, "2026-11-04")
    rec = await db.get_reconciliation(conn, "2026-11-04")
    assert rec["billed_usd"] == pytest.approx(2.2345)
    assert h.requests[0].headers["x-api-key"] == KEY


async def test_reconcile_day_stores_nothing_when_billed_none(conn):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=None)):
        await reconcile_day(conn, "2026-11-04")
    assert await db.get_reconciliation(conn, "2026-11-04") is None


async def test_reconcile_day_http_failure_stores_nothing(conn):
    def handler(request):
        return httpx.Response(401, content=b"unauthorized")

    real_client = httpx.AsyncClient
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.httpx.AsyncClient",
               side_effect=lambda *a, **k: real_client(transport=httpx.MockTransport(handler))):
        await reconcile_day(conn, "2026-11-04")
    assert await db.get_reconciliation(conn, "2026-11-04") is None


def _reconciled(caplog):
    return [r for r in caplog.records
            if r.name == "ahsoka.pipeline.reconcile" and "Reconciled" in r.getMessage()]


async def test_reconcile_day_success_logs_one_info_record(conn, caplog):
    buckets = [{"bucket": "2026-11-04", "scoring_usd": 1.5, "embedding_usd": 0.25}]
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=2.2345)), \
         patch("ahsoka.database.get_cost_buckets", new=AsyncMock(return_value=buckets)):
        with caplog.at_level(logging.INFO, logger="ahsoka.pipeline.reconcile"):
            await reconcile_day(conn, "2026-11-04")
    recs = _reconciled(caplog)
    assert len(recs) == 1
    assert recs[0].levelno == logging.INFO
    msg = recs[0].getMessage()
    assert "2026-11-04" in msg
    assert "$2.2345" in msg
    assert "$1.7500" in msg
    assert KEY not in caplog.text


async def test_reconcile_day_fetch_none_logs_no_reconciled_record(conn, caplog):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=None)):
        with caplog.at_level(logging.INFO, logger="ahsoka.pipeline.reconcile"):
            await reconcile_day(conn, "2026-11-04")
    assert _reconciled(caplog) == []


async def test_reconcile_day_no_key_logs_no_reconciled_record(conn, caplog):
    with patch("ahsoka.pipeline.reconcile.settings", _settings(key="")):
        with caplog.at_level(logging.INFO, logger="ahsoka.pipeline.reconcile"):
            await reconcile_day(conn, "2026-11-04")
    assert _reconciled(caplog) == []


async def test_reconcile_day_never_raises_on_fetch_error(conn, caplog):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(side_effect=RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING):
            await reconcile_day(conn, "2026-11-04")
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert KEY not in caplog.text


async def test_reconcile_day_never_raises_on_db_error(conn):
    with patch("ahsoka.pipeline.reconcile.settings", _settings()), \
         patch("ahsoka.pipeline.reconcile.fetch_billed_usd", new=AsyncMock(return_value=1.0)), \
         patch("ahsoka.database.save_reconciliation", new=AsyncMock(side_effect=RuntimeError("db"))):
        await reconcile_day(conn, "2026-11-04")


async def test_admin_key_never_logged_across_failures(conn, caplog):
    def handler(request):
        return httpx.Response(403, content=f"bad key {KEY}".encode())

    with caplog.at_level(logging.DEBUG):
        assert await _fetch(handler) is None
    assert KEY not in caplog.text


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
async def test_fetch_billed_non_finite_amount_returns_none_and_warns(amount, caplog):
    h = _recorder(_body([_bucket(["10", amount])]))
    with caplog.at_level(logging.WARNING):
        assert await _fetch(h) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert KEY not in caplog.text


async def test_fetch_billed_negative_total_returned_unchanged():
    h = _recorder(_body([_bucket(["-250"])]))
    assert await _fetch(h) == pytest.approx(-2.5)


async def test_fetch_billed_page_cap_stops_endless_has_more():
    h = _recorder(_body([_bucket(["1"])], has_more=True, next_page="p"))
    result = await _fetch(h)
    assert len(h.requests) <= 50
    assert result is None or isinstance(result, float)


async def test_fetch_billed_overflowing_total_returns_none_and_warns(caplog):
    h = _recorder(_body([_bucket(["1E999"])]))
    with caplog.at_level(logging.WARNING):
        assert await _fetch(h) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert KEY not in caplog.text
    assert "1E999" not in caplog.text


async def test_fetch_billed_large_finite_total_returned_exactly():
    from decimal import Decimal
    import math

    h = _recorder(_body([_bucket(["1E300"])]))
    result = await _fetch(h)
    assert math.isfinite(result)
    assert result == float(Decimal("1E300") / 100)


async def test_fetch_billed_normal_value_unchanged_by_overflow_guard():
    h = _recorder(_body([_bucket(["250"])]))
    assert await _fetch(h) == pytest.approx(2.5)
