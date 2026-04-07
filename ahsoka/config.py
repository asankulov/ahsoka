from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.providers.env import EnvSettingsSource
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource


def _lenient(cls):
    """Mixin: fall back to the raw string when JSON decoding fails (e.g. comma-sep lists)."""
    class _Lenient(cls):
        def decode_complex_value(self, field_name: str, field_info: object, value: object) -> object:
            try:
                return super().decode_complex_value(field_name, field_info, value)
            except Exception:
                return value
    return _Lenient


_LenientEnvSource = _lenient(EnvSettingsSource)
_LenientDotEnvSource = _lenient(DotEnvSettingsSource)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_api_id: int
    telegram_api_hash: str
    session_name: str = "ahsoka_user"
    bot_token: str
    log_bot_token: str | None = None  # dedicated bot for forwarding log records
    owner_chat_id: int
    channel_ids: list[int] = []
    anthropic_api_key: str
    claude_model: str = "claude-haiku-4-5-20251001"
    default_score_threshold: int = 7
    scrape_timeout_s: float = 5.0
    db_path: str = "ahsoka.db"

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):  # type: ignore[override]
        init_settings = kwargs["init_settings"]
        secrets_settings = kwargs.get("secrets_settings") or kwargs.get("file_secret_settings")
        return (
            init_settings,
            _LenientEnvSource(settings_cls),
            _LenientDotEnvSource(settings_cls, env_file=".env", env_file_encoding="utf-8"),
            *([] if secrets_settings is None else [secrets_settings]),
        )

    @field_validator("channel_ids", mode="before")
    @classmethod
    def parse_channel_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v  # type: ignore[return-value]


settings = Settings()
