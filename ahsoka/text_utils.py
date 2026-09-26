"""Small text helpers with no framework dependencies (safe to import anywhere)."""

from __future__ import annotations


def slice_utf16(text: str, offset: int, length: int) -> str:
    """Slice ``text`` using Telegram-style UTF-16 code unit offsets.

    Telegram (and both Pyrogram's raw MTProto types and aiogram's entity
    types) report ``MessageEntity.offset``/``.length`` in UTF-16 code units,
    not Python ``str`` indices. Astral characters (e.g. most emoji) occupy
    two UTF-16 code units but one Python ``str`` index, so slicing a plain
    ``str`` directly with these offsets shifts or truncates the result as
    soon as such a character appears before the entity.

    This encodes to UTF-16-LE, slices by byte offset (2 bytes per code
    unit), and decodes back. Out-of-range offsets/lengths behave like
    ordinary Python slicing (clamped, never raising). A malformed slice that
    splits a surrogate pair is decoded with ``errors="ignore"``, dropping
    the unpaired surrogate rather than raising ``UnicodeDecodeError`` — this
    can only happen with bad input (e.g. a hand-rolled offset), and
    returning a slightly short string is preferable to crashing the
    watcher/handler pipeline over it. A lone surrogate already present in
    ``text`` itself (e.g. from upstream malformed input) is likewise
    tolerated rather than raising ``UnicodeEncodeError`` on encode.
    """
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    start = 2 * offset
    end = 2 * (offset + length)
    return encoded[start:end].decode("utf-16-le", errors="ignore")
