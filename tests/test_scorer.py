import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ahsoka.models import Post
from ahsoka.pipeline.scorer import score_post


def make_post() -> Post:
    return Post(channel_id=1, message_id=1, channel_name="test", text="Python backend job", timestamp=datetime.now())


def _mock_client(data: dict) -> AsyncMock:
    # The scorer prefills the assistant turn with "{", so the API returns
    # the continuation only — the opening brace is not part of the response.
    client = AsyncMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(data)[1:])]
    client.messages.create = AsyncMock(return_value=response)
    return client


async def test_score_parsed_correctly():
    client = _mock_client({
        "score": 8, "reason": "Good match", "apply": "hr@co.com",
        "stack": ["python", "django"], "seniority": "senior",
        "remote": "remote", "red_flags": ["no salary info"],
    })
    score = await score_post(client, make_post(), "content", "model")
    assert score.score == 8
    assert score.reason == "Good match"
    assert score.apply == "hr@co.com"
    assert score.stack == ["python", "django"]
    assert score.seniority == "senior"
    assert score.remote == "remote"
    assert score.red_flags == ["no salary info"]


async def test_apply_defaults_to_empty_string():
    client = _mock_client({"score": 5, "reason": "Partial match"})
    score = await score_post(client, make_post(), "content", "model")
    assert score.apply == ""


async def test_malformed_json_returns_zero():
    client = AsyncMock()
    response = MagicMock()
    response.content = [MagicMock(text="not json at all")]
    client.messages.create = AsyncMock(return_value=response)
    score = await score_post(client, make_post(), "content", "model")
    assert score.score == 0
    assert score.reason == "parse error"
    assert score.stack == []
    assert score.seniority == "any"
    assert score.remote == "unknown"
    assert score.red_flags == []


async def test_content_truncated_to_4000_chars():
    client = _mock_client({"score": 7, "reason": "ok", "apply": ""})
    long_content = "x" * 10_000
    await score_post(client, make_post(), long_content, "model")
    call_kwargs = client.messages.create.call_args
    prompt = call_kwargs.kwargs["messages"][0]["content"]
    assert long_content[:4000] in prompt
    assert long_content[4001:] not in prompt


async def test_invalid_seniority_falls_back_to_any():
    client = _mock_client({
        "score": 6, "reason": "ok", "seniority": "expert",
    })
    score = await score_post(client, make_post(), "content", "model")
    assert score.seniority == "any"


async def test_invalid_remote_falls_back_to_unknown():
    client = _mock_client({
        "score": 6, "reason": "ok", "remote": "flexible",
    })
    score = await score_post(client, make_post(), "content", "model")
    assert score.remote == "unknown"


async def test_stack_string_wrapped_in_list():
    client = _mock_client({
        "score": 7, "reason": "ok", "stack": "python",
    })
    score = await score_post(client, make_post(), "content", "model")
    assert score.stack == ["python"]


async def test_missing_new_fields_get_defaults():
    client = _mock_client({"score": 5, "reason": "ok"})
    score = await score_post(client, make_post(), "content", "model")
    assert score.stack == []
    assert score.seniority == "any"
    assert score.remote == "unknown"
    assert score.red_flags == []
