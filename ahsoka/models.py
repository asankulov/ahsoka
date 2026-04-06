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
    urls: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_message(cls, message: object) -> Post:
        text: str = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        chat = getattr(message, "chat", None)
        channel_name: str = (
            getattr(chat, "username", None) or str(getattr(chat, "id", 0))
        )

        seen: set[str] = set()
        urls: list[str] = []
        entities = getattr(message, "entities", None) or []
        for entity in entities:
            if len(urls) >= 3:
                break
            etype = getattr(entity, "type", None)
            etype_val = getattr(etype, "value", etype)
            if etype_val == "text_link":
                u = getattr(entity, "url", None)
            elif etype_val == "url":
                offset = getattr(entity, "offset", 0)
                length = getattr(entity, "length", 0)
                u = text[offset : offset + length]
            else:
                continue
            if u and u not in seen:
                seen.add(u)
                urls.append(u)

        return cls(
            channel_id=getattr(chat, "id", 0),
            message_id=getattr(message, "id", 0),
            channel_name=channel_name,
            text=text,
            url=urls[0] if urls else None,
            urls=urls,
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
