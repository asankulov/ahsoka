from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Post:
    channel_id: int
    message_id: int
    channel_name: str
    text: str
    url: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_message(cls, message: object) -> Post:
        text: str = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        chat = getattr(message, "chat", None)
        channel_name: str = (
            getattr(chat, "username", None) or str(getattr(chat, "id", 0))
        )

        url: str | None = None
        entities = getattr(message, "entities", None) or []
        for entity in entities:
            etype = getattr(entity, "type", None)
            etype_val = getattr(etype, "value", etype)
            if etype_val == "text_link":
                url = getattr(entity, "url", None)
                break
            if etype_val == "url":
                offset = getattr(entity, "offset", 0)
                length = getattr(entity, "length", 0)
                url = text[offset : offset + length]
                break

        return cls(
            channel_id=getattr(chat, "id", 0),
            message_id=getattr(message, "id", 0),
            channel_name=channel_name,
            text=text,
            url=url,
            timestamp=getattr(message, "date", datetime.now()),
        )


@dataclass
class Score:
    score: int
    reason: str
    apply: str = ""


@dataclass
class UserConfig:
    stack: str = ""
    seniority: str = ""
    remote: str = ""
    location: str = ""
    salary_min: str = ""
    salary_max: str = ""
    threshold: int = 7
    paused: bool = False
    keywords: str = ""
