"""Tests for ahsoka.pipeline.scorer (personalized batch scoring)."""
import json
from datetime import datetime

import pytest

from ahsoka.models import PersonalizedVerdict, Post, UserConfig
from ahsoka.pipeline.scorer import build_personalized_prompt, parse_verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_post(
    channel_id: int = 111,
    message_id: int = 222,
    channel_name: str = "testchan",
    text: str = "Python backend job",
) -> Post:
    return Post(
        channel_id=channel_id,
        message_id=message_id,
        channel_name=channel_name,
        text=text,
        timestamp=datetime.now(),
    )


def make_config(
    user_id: int = 42,
    stack: str = "python go",
    seniority: str = "senior",
    remote: str = "remote",
    location: str = "Berlin",
    salary_min: str = "100000",
    salary_max: str = "150000",
    keywords: str = "python backend",
    threshold: int = 7,
) -> UserConfig:
    return UserConfig(
        user_id=user_id,
        notify_chat_id=user_id,
        stack=stack,
        seniority=seniority,
        remote=remote,
        location=location,
        salary_min=salary_min,
        salary_max=salary_max,
        keywords=keywords,
        threshold=threshold,
    )


def _make_succeeded_response(payload: dict) -> dict:
    """Build a succeeded batch result dict with JSON-encoded payload."""
    text = json.dumps(payload)[1:]  # strip leading "{" (prompt prefill adds it)
    return {
        "result": {
            "type": "succeeded",
            "message": {
                "content": [{"type": "text", "text": text}],
            },
        }
    }


# ---------------------------------------------------------------------------
# build_personalized_prompt
# ---------------------------------------------------------------------------


def test_custom_id_format():
    post = make_post(channel_id=111, message_id=222)
    config = make_config(user_id=42)
    result = build_personalized_prompt(post, "content", config)
    assert result["custom_id"] == "111_222_42"


def test_max_tokens_is_512():
    result = build_personalized_prompt(make_post(), "content", make_config())
    assert result["params"]["max_tokens"] == 512


def test_no_model_key_in_params():
    """model must NOT be present — BatchSubmitter injects it from settings."""
    result = build_personalized_prompt(make_post(), "content", make_config())
    assert "model" not in result["params"]


def test_system_prompt_present():
    result = build_personalized_prompt(make_post(), "content", make_config())
    assert "system" in result["params"]
    assert len(result["params"]["system"]) > 0


def test_messages_structure():
    result = build_personalized_prompt(make_post(), "content", make_config())
    messages = result["params"]["messages"]
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    # assistant prefill starts with "{"
    assert messages[1]["content"] == "{"


def test_user_config_fields_woven_into_prompt():
    config = make_config(
        stack="python go",
        seniority="senior",
        remote="remote",
        location="Berlin",
        salary_min="100000",
        salary_max="150000",
        keywords="python backend",
        threshold=8,
    )
    result = build_personalized_prompt(make_post(), "some content", config)
    user_msg = result["params"]["messages"][0]["content"]
    assert "python go" in user_msg
    assert "senior" in user_msg
    assert "remote" in user_msg
    assert "Berlin" in user_msg
    assert "100000" in user_msg
    assert "150000" in user_msg
    assert "python backend" in user_msg
    assert "8" in user_msg  # threshold


def test_empty_config_fields_use_placeholder_strings():
    """Empty stack/seniority/remote/location/salary use 'any' or 'unspecified'."""
    config = UserConfig(user_id=1, notify_chat_id=1, threshold=7)  # all empty strings
    result = build_personalized_prompt(make_post(), "content", config)
    user_msg = result["params"]["messages"][0]["content"]
    assert "any" in user_msg         # stack, seniority, remote, location
    assert "unspecified" in user_msg  # salary_min, salary_max
    # must NOT have empty string placeholder entries like "Stack: \n"
    assert "Stack: \n" not in user_msg
    assert "Keywords: \n" not in user_msg


def test_empty_keywords_uses_none_placeholder():
    config = UserConfig(user_id=1, notify_chat_id=1, threshold=7)
    result = build_personalized_prompt(make_post(), "content", config)
    user_msg = result["params"]["messages"][0]["content"]
    assert "none" in user_msg  # keywords placeholder


def test_content_truncated_at_4000_chars():
    long_content = "x" * 10_000
    result = build_personalized_prompt(make_post(), long_content, make_config())
    user_msg = result["params"]["messages"][0]["content"]
    assert "x" * 4000 in user_msg
    assert "x" * 4001 not in user_msg


def test_content_under_4000_not_truncated():
    content = "y" * 3000
    result = build_personalized_prompt(make_post(), content, make_config())
    user_msg = result["params"]["messages"][0]["content"]
    assert "y" * 3000 in user_msg


# ---------------------------------------------------------------------------
# parse_verdict — happy path
# ---------------------------------------------------------------------------


def test_parse_verdict_well_formed():
    payload = {
        "score": 8,
        "reason": "Strong Python fit",
        "matched": True,
        "apply": "hr@company.com",
        "red_flags": ["no salary"],
    }
    response = _make_succeeded_response(payload)
    verdict = parse_verdict(response, user_id=42)
    assert isinstance(verdict, PersonalizedVerdict)
    assert verdict.user_id == 42
    assert verdict.score == 8
    assert verdict.reason == "Strong Python fit"
    assert verdict.matched is True
    assert verdict.apply == "hr@company.com"
    assert verdict.red_flags == ["no salary"]


def test_parse_verdict_user_id_threaded_through():
    response = _make_succeeded_response({"score": 5, "reason": "ok", "matched": False})
    verdict = parse_verdict(response, user_id=999)
    assert verdict.user_id == 999


def test_parse_verdict_empty_red_flags():
    response = _make_succeeded_response({"score": 7, "reason": "ok", "matched": True, "red_flags": []})
    verdict = parse_verdict(response, user_id=1)
    assert verdict.red_flags == []


def test_parse_verdict_red_flags_string_wrapped():
    """A single string red_flag (not a list) should be wrapped."""
    response = _make_succeeded_response(
        {"score": 5, "reason": "ok", "matched": False, "red_flags": "vague comp"}
    )
    verdict = parse_verdict(response, user_id=1)
    assert verdict.red_flags == ["vague comp"]


def test_parse_verdict_apply_defaults_empty():
    response = _make_succeeded_response({"score": 6, "reason": "ok", "matched": False})
    verdict = parse_verdict(response, user_id=1)
    assert verdict.apply == ""


# ---------------------------------------------------------------------------
# parse_verdict — SDK object with .text attribute (not plain dict block)
# ---------------------------------------------------------------------------


def test_parse_verdict_sdk_object_content_block():
    """Content block is an object with .text attribute instead of a plain dict."""
    class FakeBlock:
        text = json.dumps({"score": 9, "reason": "sdk path", "matched": True})[1:]

    response = {
        "result": {
            "type": "succeeded",
            "message": {"content": [FakeBlock()]},
        }
    }
    verdict = parse_verdict(response, user_id=7)
    assert verdict.score == 9
    assert verdict.reason == "sdk path"
    assert verdict.matched is True


# ---------------------------------------------------------------------------
# parse_verdict — error / malformed paths
# ---------------------------------------------------------------------------


def test_parse_verdict_errored_type_returns_safe_defaults():
    response = {
        "result": {
            "type": "errored",
            "error": {"message": "rate limit exceeded"},
        }
    }
    verdict = parse_verdict(response, user_id=42)
    assert verdict.score == 0
    assert verdict.matched is False
    assert "rate limit exceeded" in verdict.reason
    assert verdict.user_id == 42


def test_parse_verdict_errored_user_id_passthrough():
    response = {"result": {"type": "errored", "error": {"message": "err"}}}
    assert parse_verdict(response, user_id=77).user_id == 77


def test_parse_verdict_malformed_json_returns_parse_error():
    response = {
        "result": {
            "type": "succeeded",
            "message": {"content": [{"type": "text", "text": "not valid json at all"}]},
        }
    }
    verdict = parse_verdict(response, user_id=42)
    assert verdict.score == 0
    assert verdict.matched is False
    assert verdict.reason == "parse error"


def test_parse_verdict_missing_result_key_returns_safe_defaults():
    verdict = parse_verdict({}, user_id=5)
    assert verdict.score == 0
    assert verdict.matched is False
    assert verdict.user_id == 5


def test_parse_verdict_missing_result_key_user_id_passthrough():
    assert parse_verdict({}, user_id=123).user_id == 123


def test_parse_verdict_none_result_type_returns_safe_defaults():
    """result present but type is None (unexpected API response)."""
    response = {"result": {"type": None}}
    verdict = parse_verdict(response, user_id=1)
    assert verdict.score == 0
    assert verdict.matched is False


def test_parse_verdict_missing_score_key_raises_handled():
    """JSON parses but 'score' key missing — KeyError should be caught."""
    response = {
        "result": {
            "type": "succeeded",
            "message": {"content": [{"type": "text", "text": '"reason": "no score field"}'}]},
        }
    }
    verdict = parse_verdict(response, user_id=1)
    assert verdict.score == 0
    assert verdict.reason == "parse error"
