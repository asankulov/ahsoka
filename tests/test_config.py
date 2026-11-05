import pytest

from ahsoka.config import Settings


def _make(monkeypatch, mode):
    monkeypatch.delenv("REPOST_MODE", raising=False)
    return Settings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="h", bot_token="t",
        owner_chat_id=1, anthropic_api_key="k", repost_mode=mode,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("ENFORCE ", "enforce"), (" SHADOW ", "shadow"), ("Off", "off")],
)
def test_repost_mode_normalized(monkeypatch, raw, expected):
    assert _make(monkeypatch, raw).repost_mode == expected


@pytest.mark.parametrize("raw", ["enforced", "bogus", ""])
def test_repost_mode_invalid_falls_back_to_shadow_with_warning(monkeypatch, raw):
    with pytest.warns(UserWarning, match="REPOST_MODE"):
        s = _make(monkeypatch, raw)
    assert s.repost_mode == "shadow"


def test_anthropic_admin_key_defaults_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_ADMIN_KEY", raising=False)
    s = Settings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="h", bot_token="t",
        owner_chat_id=1, anthropic_api_key="k",
    )
    assert s.anthropic_admin_key == ""


def test_anthropic_admin_key_read_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-admin-xyz")
    s = Settings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="h", bot_token="t",
        owner_chat_id=1, anthropic_api_key="k",
    )
    assert s.anthropic_admin_key == "sk-ant-admin-xyz"
