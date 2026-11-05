import json
import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ahsoka.pipeline import embedder
from ahsoka.pipeline.embedder import embed, normalize, take_token_count


@pytest.fixture(autouse=True)
def _reset_usage_sink():
    embedder.set_usage_sink(None)
    yield
    embedder.set_usage_sink(None)


def _settings(key="k"):
    s = MagicMock()
    s.voyage_api_key = key
    s.voyage_model = "voyage-4-lite"
    s.repost_dimensions = 512
    s.repost_max_chars = 8000
    s.embed_timeout_s = 5.0
    return s


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(tokens=7):
    def handler(request):
        handler.requests.append(request)
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}], "usage": {"total_tokens": tokens}}
        )

    handler.requests = []
    return handler


def test_normalize_collapses_whitespace_and_truncates():
    assert normalize("  a \n\t b  ", 100) == "a b"
    assert normalize("abcdef", 3) == "abc"


async def test_embed_sends_expected_request():
    h = _ok()
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        result = await embed("hello world", client=_client(h))
    assert result == [0.1, 0.2]
    req = h.requests[0]
    assert str(req.url) == "https://api.voyageai.com/v1/embeddings"
    assert req.headers["Authorization"] == "Bearer k"
    assert json.loads(req.content) == {
        "model": "voyage-4-lite",
        "input": ["hello world"],
        "input_type": "document",
        "output_dimension": 512,
    }


async def test_embed_normalizes_and_truncates():
    h = _ok()
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        await embed("a\n\n  b" + "x" * 9000, client=_client(h))
    sent = json.loads(h.requests[0].content)["input"][0]
    assert sent.startswith("a bxxx")
    assert len(sent) == 8000


async def test_embed_returns_none_without_key():
    h = _ok()
    with patch("ahsoka.pipeline.embedder.settings", _settings(key="")):
        assert await embed("hello", client=_client(h)) is None
    assert h.requests == []


async def test_embed_returns_none_on_empty_text():
    h = _ok()
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("   \n", client=_client(h)) is None
    assert h.requests == []


@pytest.mark.parametrize("status", [429, 500])
async def test_embed_returns_none_on_http_error(status, caplog):
    before = take_token_count()  # reset
    del before

    def handler(request):
        return httpx.Response(status, json={"detail": "nope"})

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        with caplog.at_level(logging.DEBUG, logger="ahsoka.pipeline.embedder"):
            assert await embed("hello", client=_client(handler)) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert take_token_count() == 0


async def test_embed_returns_none_on_timeout(caplog):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        with caplog.at_level(logging.WARNING, logger="ahsoka.pipeline.embedder"):
            assert await embed("hello", client=_client(handler)) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_embed_returns_none_on_malformed_body(caplog):
    def handler(request):
        return httpx.Response(200, json={"data": []})

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        with caplog.at_level(logging.WARNING, logger="ahsoka.pipeline.embedder"):
            assert await embed("hello", client=_client(handler)) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_embed_returns_none_on_invalid_json_body():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(handler)) is None


async def test_embed_succeeds_without_usage_field():
    take_token_count()

    def handler(request):
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(handler)) == [1.0]
    assert take_token_count() == 0


async def test_embed_without_client_builds_its_own_client():
    h = _ok()
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        assert kwargs["timeout"] == 5.0
        return real(transport=httpx.MockTransport(h))

    with patch("ahsoka.pipeline.embedder.settings", _settings()), patch.object(
        embedder.httpx, "AsyncClient", side_effect=factory
    ):
        assert await embed("hello") == [0.1, 0.2]
    assert len(h.requests) == 1


async def test_take_token_count_accumulates_and_resets():
    take_token_count()
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        await embed("one", client=_client(_ok(7)))
        await embed("two", client=_client(_ok(3)))
    assert take_token_count() == 10
    assert take_token_count() == 0


@pytest.mark.parametrize("bad", [[], {}, [0.1, None], [True, 0.2], "abc"])
async def test_embed_invalid_embedding_returns_none_one_warning_no_tokens(bad, caplog):
    take_token_count()

    def handler(request):
        return httpx.Response(
            200, json={"data": [{"embedding": bad}], "usage": {"total_tokens": 9}}
        )

    with patch("ahsoka.pipeline.embedder.settings", _settings()), \
         caplog.at_level(logging.WARNING, logger="ahsoka.pipeline.embedder"):
        result = await embed("hello", client=_client(handler))
    assert result is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert take_token_count() == 0


async def test_usage_sink_called_with_total_tokens():
    calls = []

    async def sink(tokens):
        calls.append(tokens)

    embedder.set_usage_sink(sink)
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(_ok(17))) == [0.1, 0.2]
    assert calls == [17]


async def test_usage_sink_exception_swallowed_and_warned(caplog):
    take_token_count()

    async def sink(tokens):
        raise RuntimeError("db down")

    embedder.set_usage_sink(sink)
    with patch("ahsoka.pipeline.embedder.settings", _settings()), \
         caplog.at_level(logging.WARNING, logger="ahsoka.pipeline.embedder"):
        assert await embed("hello", client=_client(_ok(5))) == [0.1, 0.2]
    assert [r for r in caplog.records if r.levelno == logging.WARNING]
    assert take_token_count() == 5


async def test_no_sink_set_still_accumulates_tokens():
    take_token_count()
    embedder.set_usage_sink(None)
    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(_ok(4))) == [0.1, 0.2]
    assert take_token_count() == 4


async def test_usage_sink_not_called_on_http_error():
    calls = []

    async def sink(tokens):
        calls.append(tokens)

    embedder.set_usage_sink(sink)

    def handler(request):
        return httpx.Response(500, json={"detail": "nope"})

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(handler)) is None
    assert calls == []


async def test_usage_sink_not_called_on_malformed_embedding():
    calls = []

    async def sink(tokens):
        calls.append(tokens)

    embedder.set_usage_sink(sink)

    def handler(request):
        return httpx.Response(
            200, json={"data": [{"embedding": [True, 0.2]}], "usage": {"total_tokens": 9}}
        )

    with patch("ahsoka.pipeline.embedder.settings", _settings()):
        assert await embed("hello", client=_client(handler)) is None
    assert calls == []
