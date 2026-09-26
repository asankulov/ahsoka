"""Pure helpers for the admin /export command: offset parsing and Markdown rendering.

No DB or aiogram imports here — commands.py owns fetching rows and sending the file.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ahsoka.models import NotifiedPost

_RELATIVE_RE = re.compile(r"^(\d+)([mhdw])$")
_UNIT_TO_KWARG = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_date_only(token: str) -> bool:
    """True for a bare ISO date with no time part — extended (YYYY-MM-DD, len 10)
    or basic (YYYYMMDD, len 8) form. `fromisoformat` accepts both."""
    return "T" not in token and len(token) in (8, 10)


def parse_range(args: list[str], now: datetime) -> tuple[datetime, datetime] | None:
    """Parse /export's offset argument(s) into a UTC (start, end) pair.

    start is inclusive, end is exclusive. Accepts three forms:
      - relative: a single token like "30m", "24h", "7d", "2w" -> (now - delta, now)
      - since: a single ISO date/datetime -> (that instant, now)
      - range: two ISO date/datetime tokens -> (first, second); a date-only end
        token is bumped by one day so the whole end day is included.
    Returns None on any malformed or nonsensical input (empty, >2 tokens,
    unparseable, zero relative amount, start in the future, start >= end).
    """
    if not args or len(args) > 2:
        return None

    if len(args) == 1:
        match = _RELATIVE_RE.match(args[0])
        if match:
            amount = int(match.group(1))
            if amount <= 0:
                return None
            try:
                delta = timedelta(**{_UNIT_TO_KWARG[match.group(2)]: amount})
                start = now - delta
            except OverflowError:
                return None
            return start, now

        try:
            start = _to_utc(datetime.fromisoformat(args[0]))
        except (ValueError, OverflowError):
            return None
        if start >= now:
            return None
        return start, now

    try:
        start = _to_utc(datetime.fromisoformat(args[0]))
        end = _to_utc(datetime.fromisoformat(args[1]))
    except (ValueError, OverflowError):
        return None
    if _is_date_only(args[1]):
        end = end + timedelta(days=1)
    if start >= now or start >= end:
        return None
    return start, end


def build_post_link(channel_id: int, message_id: int, username: str | None = None) -> str:
    """Fallback link builder for a (channel_id, message_id), mirroring Post.link."""
    if username:
        return f"https://t.me/{username}/{message_id}"
    cid = str(abs(channel_id))[3:]  # strip leading "100" from e.g. 1001234567890
    return f"https://t.me/c/{cid}/{message_id}"


def render_markdown(
    sections: dict[int, list[NotifiedPost]],
    start: datetime,
    end: datetime,
    links: dict[int, str | None],
    retention_note: bool,
) -> str:
    """Render a Markdown export document.

    sections: user_id -> ordered list of NotifiedPost (one `## User <id>` block each).
    links: channel_id -> resolved Telegram username (or None to fall back to a
    t.me/c/... link) — the caller resolves this once per distinct channel.
    """
    total = sum(len(posts) for posts in sections.values())
    lines = [
        "# Notification Export",
        "",
        f"Range: {start:%Y-%m-%d %H:%M:%S} to {end:%Y-%m-%d %H:%M:%S} UTC",
        f"Total entries: {total}",
    ]
    if retention_note:
        lines.append(
            "Note: notification history is only retained for 30 days — "
            "entries older than that are not available."
        )
    lines.append("")

    for user_id, posts in sections.items():
        lines.append(f"## User {user_id}")
        lines.append("")
        for post in posts:
            link = build_post_link(post.channel_id, post.message_id, links.get(post.channel_id))
            lines.append(f"- [{post.channel_id}/{post.message_id}]({link})")
            if post.score is None:
                lines.append("  - (no verdict stored)")
            else:
                lines.append(f"  - Score: {post.score}/10")
                lines.append(f"  - Apply: {post.apply or '-'}")
                lines.append(f"  - Reason: {post.reason or '-'}")
                lines.append(f"  - Red flags: {', '.join(post.red_flags) if post.red_flags else '-'}")
            lines.append(f"  - Sent: {post.sent_at} UTC")
            lines.append(f"  - Job URL(s): {', '.join(post.urls) if post.urls else '-'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
