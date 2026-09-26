"""Pure helpers for the admin /export command: offset parsing, entity-to-Markdown
conversion, and Markdown rendering.

No DB or aiogram imports here — commands.py owns fetching rows and sending the file.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ahsoka.models import NotifiedPost
from ahsoka.text_utils import entity_type_name

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


# ---------------------------------------------------------------------------
# Entity -> Markdown conversion
# ---------------------------------------------------------------------------

# Whitespace-trimmed symmetric delimiters (CommonMark can't open/close emphasis
# on whitespace, so the span's edge whitespace is pushed outside these).
_INLINE_DELIMS = {
    "bold": "**",
    "italic": "*",
    "strikethrough": "~~",
}

_NL = "\n".encode("utf-16-le")
_BLOCK_TYPES = {"blockquote", "pre"}
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]{1,31}:")


def _u16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def entities_to_markdown(text: str, entities: list) -> str:
    """Convert a Telegram message's plain text + entities into standard CommonMark.

    Entities are duck-typed: `type`, `offset`/`length` (UTF-16 code units, hence
    all math below runs on `text.encode("utf-16-le")`), and optionally `url`,
    `user.id`, `language`. Markers are inserted back-to-front (Pyrogram's
    `Markdown.unparse` strategy) highest-offset-first; same-offset ties are
    broken by marker kind — not plain insertion order, which mis-nests when e.g.
    a link ends where its enclosing bold span does — so opens sort outer-first,
    closes sort inner-first, and closes as a group precede opens as a group.
    """
    if not entities:
        return text

    utf16 = text.encode("utf-16-le")
    total_units = len(utf16) // 2
    # (marker, offset, is_closing)
    insertions: list[tuple[str, int, bool]] = []

    # Outer-before-inner at a shared span: longer first, block types before
    # inline ones (a same-span blockquote/pre must wrap, not be wrapped).
    ordered_entities = sorted(
        entities,
        key=lambda e: (
            getattr(e, "offset", 0),
            -getattr(e, "length", 0),
            0 if entity_type_name(getattr(e, "type", None)) in _BLOCK_TYPES else 1,
        ),
    )

    for entity in ordered_entities:
        etype_val = entity_type_name(getattr(entity, "type", None))
        offset = getattr(entity, "offset", 0)
        length = getattr(entity, "length", 0)
        if length <= 0:
            continue
        start, end = offset, offset + length
        span_bytes = utf16[start * 2 : end * 2]

        if etype_val == "text_link":
            url = getattr(entity, "url", None)
            if not url:
                continue
            insertions.append(("[", start, False))
            insertions.append((f"]({url})", end, True))
        elif etype_val == "text_mention":
            user = getattr(entity, "user", None)
            uid = getattr(user, "id", None) if user is not None else None
            if uid is None:
                continue
            insertions.append(("[", start, False))
            insertions.append((f"](tg://user?id={uid})", end, True))
        elif etype_val == "url":
            span_text = span_bytes.decode("utf-16-le")
            if _SCHEME_RE.match(span_text):
                insertions.append(("<", start, False))
                insertions.append((">", end, True))
            else:
                # schemeless (Telegram still tags "t.me/x", "www.x.io" as url):
                # <...> needs a scheme, so link instead of bare-autolinking
                insertions.append(("[", start, False))
                insertions.append((f"](https://{span_text})", end, True))
        elif etype_val in _INLINE_DELIMS:
            span_text = span_bytes.decode("utf-16-le")
            if not span_text.strip():
                continue  # whitespace-only span: CommonMark can't emphasize it
            delim = _INLINE_DELIMS[etype_val]
            lead = len(span_text) - len(span_text.lstrip())
            trail = len(span_text) - len(span_text.rstrip())
            inner_start = start + (_u16_len(span_text[:lead]) if lead else 0)
            inner_end = end - (_u16_len(span_text[len(span_text) - trail :]) if trail else 0)
            insertions.append((delim, inner_start, False))
            insertions.append((delim, inner_end, True))
        elif etype_val == "code":
            span_text = span_bytes.decode("utf-16-le")
            if not span_text.strip():
                continue
            lead = len(span_text) - len(span_text.lstrip())
            trail = len(span_text) - len(span_text.rstrip())
            inner = span_text[lead : len(span_text) - trail] if trail else span_text[lead:]
            inner_start = start + (_u16_len(span_text[:lead]) if lead else 0)
            inner_end = end - (_u16_len(span_text[len(span_text) - trail :]) if trail else 0)
            # fence longer than any internal backtick run, padded if content
            # itself starts/ends with a backtick, so the span stays unambiguous
            longest = max((len(run) for run in re.findall(r"`+", inner)), default=0)
            fence = "`" * (longest + 1)
            pad = " " if inner[:1] == "`" or inner[-1:] == "`" else ""
            insertions.append((f"{fence}{pad}", inner_start, False))
            insertions.append((f"{pad}{fence}", inner_end, True))
        elif etype_val == "pre":
            language = getattr(entity, "language", "") or ""
            content = span_bytes.decode("utf-16-le")
            longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
            fence = "`" * max(3, longest + 1)  # a ``` inside content can't close early
            open_marker, close_marker = f"{fence}{language}\n", f"\n{fence}"
            if start > 0 and utf16[start * 2 - 2 : start * 2] != _NL:
                open_marker = "\n" + open_marker  # fence must start its own line
            if end < total_units and utf16[end * 2 : end * 2 + 2] != _NL:
                close_marker += "\n"  # and not run into whatever follows
            insertions.append((open_marker, start, False))
            insertions.append((close_marker, end, True))
        elif etype_val == "blockquote":
            insertions.append(("> ", start, False))
            span_text = span_bytes.decode("utf-16-le")
            pos = span_text.find("\n")
            while pos != -1:
                line_start = start + _u16_len(span_text[: pos + 1])
                if line_start < end:
                    insertions.append(("> ", line_start, False))
                pos = span_text.find("\n", pos + 1)
            # A quote not followed by a blank line lazily swallows the next
            # line in CommonMark — force one unless already at end of text.
            if end < total_units:
                nxt = utf16[end * 2 : end * 2 + 4]
                if nxt[:2] != _NL:
                    insertions.append(("\n\n", end, True))
                elif nxt[2:4] != _NL:
                    insertions.append(("\n", end + 1, True))
        else:
            # mention, hashtag, email, bot_command, underline, spoiler, and any
            # unrecognized type: left as plain text, no markers inserted.
            continue

    if not insertions:
        return text

    def _sort_key(item: tuple[int, tuple[str, int, bool]]) -> tuple[int, int, int]:
        i, (_marker, offset, is_closing) = item
        category = 1 if is_closing else 0
        # Opens: higher original index (more nested) processed first, so the
        # outer open ends up leftmost. Closes: lower original index (the outer
        # entity, added first) processed first, so the inner close ends up
        # leftmost — inner-first closing.
        tiebreak = i if is_closing else -i
        return (-offset, category, tiebreak)

    for _, (marker, offset, _is_closing) in sorted(enumerate(insertions), key=_sort_key):
        pos = offset * 2
        utf16 = utf16[:pos] + marker.encode("utf-16-le") + utf16[pos:]

    return utf16.decode("utf-16-le")


def render_markdown(
    sections: dict[int, list[NotifiedPost]],
    start: datetime,
    end: datetime,
    links: dict[int, str | None],
    bodies: dict[tuple[int, int], str | None],
    retention_note: bool,
) -> str:
    """Render a Markdown export document.

    sections: user_id -> ordered list of NotifiedPost (one `## User <id>` block each).
    links: channel_id -> resolved Telegram username (or None to fall back to a
    t.me/c/... link) — the caller resolves this once per distinct channel.
    bodies: (channel_id, message_id) -> the original message body, already
    converted to Markdown via `entities_to_markdown` — or None/missing if the
    original message could no longer be fetched.
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
            lines.append(f"### [{post.channel_id}/{post.message_id}]({link})")
            lines.append("")
            body = bodies.get((post.channel_id, post.message_id))
            if body:
                lines.append(body)
            else:
                lines.append("_(original message unavailable)_")
                if post.urls:
                    lines.append("")
                    lines.extend(f"- {url}" for url in post.urls)
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
