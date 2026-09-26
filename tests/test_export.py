"""Tests for pure helpers in ahsoka/bot/export.py: parse_range, build_post_link,
entities_to_markdown, render_markdown."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ahsoka.bot.export import build_post_link, entities_to_markdown, parse_range, render_markdown
from ahsoka.models import NotifiedPost

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_range — relative offsets
# ---------------------------------------------------------------------------

def test_parse_range_relative_minutes():
    result = parse_range(["30m"], NOW)
    assert result == (datetime(2026, 9, 26, 11, 30, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_relative_hours():
    result = parse_range(["24h"], NOW)
    assert result == (datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_relative_days():
    result = parse_range(["7d"], NOW)
    assert result == (datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_relative_weeks():
    result = parse_range(["2w"], NOW)
    assert result == (datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_relative_zero_amount_returns_none():
    assert parse_range(["0h"], NOW) is None


def test_parse_range_relative_non_matching_string_returns_none():
    assert parse_range(["abc"], NOW) is None


def test_parse_range_relative_huge_weeks_returns_none_not_overflow():
    """timedelta(weeks=99999999) itself doesn't raise, but now - delta does
    (date value out of range) — parse_range must catch it and return None."""
    assert parse_range(["99999999w"], NOW) is None


def test_parse_range_relative_huge_days_returns_none_not_overflow():
    """timedelta(days=9999999999) raises OverflowError at construction time —
    parse_range must catch it and return None."""
    assert parse_range(["9999999999d"], NOW) is None


def test_parse_range_garbage_single_token_returns_none():
    assert parse_range(["not-a-date"], NOW) is None


# ---------------------------------------------------------------------------
# parse_range — single ISO token ("since")
# ---------------------------------------------------------------------------

def test_parse_range_single_date_since_now():
    result = parse_range(["2026-09-01"], NOW)
    assert result == (datetime(2026, 9, 1, tzinfo=timezone.utc), NOW)


def test_parse_range_single_datetime_since_now():
    result = parse_range(["2026-09-01T14:00"], NOW)
    assert result == (datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_single_datetime_explicit_tz_converted_to_utc():
    result = parse_range(["2026-09-01T14:00:00+02:00"], NOW)
    assert result == (datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc), NOW)


def test_parse_range_single_future_date_returns_none():
    assert parse_range(["2099-01-01"], NOW) is None


# ---------------------------------------------------------------------------
# parse_range — two ISO tokens ("range")
# ---------------------------------------------------------------------------

def test_parse_range_range_with_time_bearing_end_no_plus_one_day():
    result = parse_range(["2026-09-01", "2026-09-15T10:00"], NOW)
    assert result == (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
    )


def test_parse_range_range_with_date_only_end_bumps_one_day():
    result = parse_range(["2026-09-01", "2026-09-15"], NOW)
    assert result == (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 16, tzinfo=timezone.utc),
    )


def test_parse_range_range_with_basic_form_date_only_end_bumps_one_day():
    """A bare YYYYMMDD end (basic ISO form, no dashes) is still date-only and
    gets the same +1-day bump as the extended YYYY-MM-DD form."""
    basic = parse_range(["2026-09-01", "20260915"], NOW)
    extended = parse_range(["2026-09-01", "2026-09-15"], NOW)
    assert basic == extended
    assert basic == (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 16, tzinfo=timezone.utc),
    )


def test_parse_range_range_with_space_separated_datetime_end_not_bumped():
    """A datetime end with no 'T' (space-separated, e.g. from fromisoformat's
    other accepted form) is longer than 10 chars, so _is_date_only correctly
    treats it as time-bearing and does not bump it by a day."""
    result = parse_range(["2026-09-01", "2026-09-15 10:00:00"], NOW)
    assert result == (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc),
    )


def test_parse_range_start_equals_end_returns_none():
    assert parse_range(["2026-09-01T10:00", "2026-09-01T10:00"], NOW) is None


def test_parse_range_start_after_end_returns_none():
    assert parse_range(["2026-09-15T10:00", "2026-09-01T10:00"], NOW) is None


def test_parse_range_range_future_start_returns_none():
    assert parse_range(["2099-01-01", "2099-01-02"], NOW) is None


def test_parse_range_two_tokens_first_looks_relative_fails():
    """A relative-looking first token is only valid as a single-token arg; with
    two tokens it must go through fromisoformat and fail."""
    assert parse_range(["30m", "2026-09-01"], NOW) is None


def test_parse_range_two_tokens_unparseable_returns_none():
    assert parse_range(["garbage", "2026-09-01"], NOW) is None


# ---------------------------------------------------------------------------
# parse_range — argument-count edge cases
# ---------------------------------------------------------------------------

def test_parse_range_empty_args_returns_none():
    assert parse_range([], NOW) is None


def test_parse_range_three_tokens_returns_none():
    assert parse_range(["2026-09-01", "2026-09-02", "2026-09-03"], NOW) is None


# ---------------------------------------------------------------------------
# build_post_link
# ---------------------------------------------------------------------------

def test_build_post_link_with_username():
    assert build_post_link(-1001234567890, 42, "somechannel") == "https://t.me/somechannel/42"


def test_build_post_link_without_username_uses_private_link():
    assert build_post_link(-1001234567890, 42, None) == "https://t.me/c/1234567890/42"


def test_build_post_link_default_username_arg_is_none():
    assert build_post_link(-1001234567890, 42) == "https://t.me/c/1234567890/42"


# ---------------------------------------------------------------------------
# entities_to_markdown
# ---------------------------------------------------------------------------
# Entities are duck-typed (never import pyrogram) — plain SimpleNamespace objects
# with string `type`s, plus one case exercising an enum-like `.value` type.

def ent(type_, offset, length, **kw):
    return SimpleNamespace(type=type_, offset=offset, length=length, **kw)


def test_entities_to_markdown_no_entities_returns_text_unchanged():
    assert entities_to_markdown("hello world", []) == "hello world"


def test_entities_to_markdown_text_link_wraps_as_markdown_link():
    entities = [ent("text_link", 6, 4, url="https://example.com")]
    assert entities_to_markdown("click here", entities) == "click [here](https://example.com)"


def test_entities_to_markdown_text_link_missing_url_skipped():
    entities = [ent("text_link", 6, 4, url=None)]
    assert entities_to_markdown("click here", entities) == "click here"


def test_entities_to_markdown_text_mention_wraps_as_tg_user_link():
    entities = [ent("text_mention", 5, 3, user=SimpleNamespace(id=42))]
    assert entities_to_markdown("ping bob now", entities) == "ping [bob](tg://user?id=42) now"


def test_entities_to_markdown_text_mention_missing_user_skipped():
    entities = [ent("text_mention", 5, 3, user=None)]
    assert entities_to_markdown("ping bob now", entities) == "ping bob now"


def test_entities_to_markdown_text_mention_missing_id_skipped():
    entities = [ent("text_mention", 5, 3, user=SimpleNamespace(id=None))]
    assert entities_to_markdown("ping bob now", entities) == "ping bob now"


@pytest.mark.parametrize("etype,delim", [
    ("bold", "**"),
    ("italic", "*"),
    ("strikethrough", "~~"),
    ("code", "`"),
])
def test_entities_to_markdown_inline_delimiters(etype, delim):
    entities = [ent(etype, 6, 4)]
    assert entities_to_markdown("hello bold world", entities) == f"hello {delim}bold{delim} world"


def test_entities_to_markdown_trailing_whitespace_moves_outside_delimiter():
    entities = [ent("bold", 0, 5)]
    assert entities_to_markdown("bold ", entities) == "**bold** "


def test_entities_to_markdown_leading_whitespace_moves_outside_delimiter():
    entities = [ent("bold", 0, 5)]
    assert entities_to_markdown(" bold", entities) == " **bold**"


def test_entities_to_markdown_whitespace_only_span_skipped():
    entities = [ent("bold", 0, 3)]
    assert entities_to_markdown("   ", entities) == "   "


def test_entities_to_markdown_code_span_with_backticks_at_both_ends_gets_longer_padded_fence():
    """The code content itself is `` `abc` `` (backtick-wrapped). The fence must
    be longer than the longest internal backtick run (1 -> fence of 2) and
    padded with a space on each side so the literal backticks aren't confused
    with the fence."""
    entities = [ent("code", 0, 5)]
    assert entities_to_markdown("`abc`", entities) == "`` `abc` ``"


def test_entities_to_markdown_code_span_internal_double_backtick_run_widens_fence():
    """Content containing a run of 2 backticks needs a fence of 3; content
    doesn't start/end with a backtick, so no padding is added."""
    entities = [ent("code", 0, 4)]
    assert entities_to_markdown("a``b", entities) == "```a``b```"


def test_entities_to_markdown_code_whitespace_only_span_skipped():
    entities = [ent("code", 0, 3)]
    assert entities_to_markdown("   ", entities) == "   "


def test_entities_to_markdown_pre_with_language_fenced():
    entities = [ent("pre", 0, 8, language="python")]
    assert entities_to_markdown("print(1)", entities) == "```python\nprint(1)\n```"


def test_entities_to_markdown_pre_without_language_bare_fence():
    entities = [ent("pre", 0, 8)]
    assert entities_to_markdown("print(1)", entities) == "```\nprint(1)\n```"


def test_entities_to_markdown_pre_at_start_and_end_of_text_no_extra_newlines():
    """A pre block spanning the entire text gets exactly the open/close
    fences, no extra leading/trailing newline."""
    entities = [ent("pre", 0, 4)]
    assert entities_to_markdown("code", entities) == "```\ncode\n```"


def test_entities_to_markdown_pre_starting_mid_line_gets_leading_newline():
    """A pre block that starts mid-line (preceding text with no newline
    right before it) needs a forced leading newline so the fence starts its
    own line."""
    entities = [ent("pre", 7, 4)]
    assert entities_to_markdown("before code", entities) == "before \n```\ncode\n```"


def test_entities_to_markdown_pre_with_trailing_text_gets_trailing_newline():
    """A pre block followed by more text on the same line (no newline right
    after it) needs a forced trailing newline so the fence doesn't run into it."""
    entities = [ent("pre", 0, 4)]
    assert entities_to_markdown("code after", entities) == "```\ncode\n```\n after"


def test_entities_to_markdown_blockquote_single_line():
    entities = [ent("blockquote", 0, 8)]
    assert entities_to_markdown("line one", entities) == "> line one"


def test_entities_to_markdown_blockquote_multi_line_prefixes_every_line():
    text = "line one\nline two"
    entities = [ent("blockquote", 0, len(text))]
    assert entities_to_markdown(text, entities) == "> line one\n> line two"


def test_entities_to_markdown_blockquote_with_nested_inline_entity():
    entities = [ent("blockquote", 0, 8), ent("bold", 0, 4)]
    assert entities_to_markdown("line one", entities) == "> **line** one"


def test_entities_to_markdown_blockquote_trailing_newline_no_leaked_prefix():
    text = "line one\n"
    entities = [ent("blockquote", 0, len(text))]
    assert entities_to_markdown(text, entities) == "> line one\n"


def test_entities_to_markdown_blockquote_multibyte_text_no_stray_prefix_mid_line():
    """Non-astral but multi-code-point text (a BMP script letter + an
    ideographic space, still 1 UTF-16 unit each) must not throw off the
    line-start byte alignment used to place '> ' prefixes."""
    text = "ક　x"  # Gujarati letter KA, ideographic space, "x" — no newline
    entities = [ent("blockquote", 0, 3)]
    assert entities_to_markdown(text, entities) == "> ક　x"


def test_entities_to_markdown_blockquote_forces_blank_line_when_not_followed_by_one():
    """CommonMark lazily continues a blockquote into the next line unless
    it's separated by a blank line — force one when the quote isn't already
    followed by one, so the next paragraph doesn't get swallowed into it."""
    text = "quoted line\nnext line"
    entities = [ent("blockquote", 0, 11)]  # just "quoted line"
    assert entities_to_markdown(text, entities) == "> quoted line\n\nnext line"


def test_entities_to_markdown_blockquote_forces_two_newlines_when_no_newline_follows_at_all():
    """When the quote span ends mid-line with no newline at all right after
    it (not even one), a full blank-line separator ("\\n\\n") must be forced
    rather than just topping up an existing single newline."""
    text = "abc def"
    entities = [ent("blockquote", 0, 3)]  # just "abc", followed directly by " def"
    assert entities_to_markdown(text, entities) == "> abc\n\n def"


def test_entities_to_markdown_blockquote_no_extra_blank_line_when_already_followed_by_one():
    text = "quoted line\n\nnext line"
    entities = [ent("blockquote", 0, 11)]  # just "quoted line"
    assert entities_to_markdown(text, entities) == "> quoted line\n\nnext line"


def test_entities_to_markdown_blockquote_at_end_of_text_appends_nothing():
    text = "quoted line"
    entities = [ent("blockquote", 0, len(text))]
    assert entities_to_markdown(text, entities) == "> quoted line"


@pytest.mark.parametrize("etype", [
    "mention", "hashtag", "email", "bot_command", "underline", "spoiler",
    "some_unrecognized_type",
])
def test_entities_to_markdown_plain_types_left_as_text(etype):
    entities = [ent(etype, 0, 5)]
    assert entities_to_markdown("hello world", entities) == "hello world"


def test_entities_to_markdown_url_wrapped_as_commonmark_autolink():
    """`url` entities get their own handling: wrapped in `<...>` (CommonMark
    autolink) rather than left as bare plain text."""
    entities = [ent("url", 4, 19)]
    assert entities_to_markdown("see https://example.com now", entities) == \
        "see <https://example.com> now"


def test_entities_to_markdown_url_schemeless_tme_becomes_markdown_link():
    """Telegram tags bare "t.me/..." spans as `url` even with no URI scheme.
    `<...>` requires a scheme (CommonMark autolink rule), so a schemeless span
    becomes `[text](https://text)` instead."""
    entities = [ent("url", 7, 16)]
    assert entities_to_markdown("Apply: t.me/somechan/45", entities) == \
        "Apply: [t.me/somechan/45](https://t.me/somechan/45)"


def test_entities_to_markdown_url_schemeless_www_becomes_markdown_link():
    entities = [ent("url", 6, 8)]
    assert entities_to_markdown("go to www.x.io now", entities) == \
        "go to [www.x.io](https://www.x.io) now"


def test_entities_to_markdown_url_with_non_http_scheme_becomes_autolink():
    """A span with any URI scheme (not just http/https) — e.g. `mailto:` —
    still qualifies for the plain `<...>` autolink form."""
    entities = [ent("url", 6, 14)]  # "mailto:a@b.com" — 14 UTF-16 units
    assert entities_to_markdown("email mailto:a@b.com here", entities) == \
        "email <mailto:a@b.com> here"


def test_entities_to_markdown_entity_after_astral_emoji_offset_correct():
    """Offsets/lengths are UTF-16 code units; an astral emoji (2 UTF-16 units,
    1 Python char) before an entity must not shift where it lands."""
    emoji = "\U0001F600"
    text = f"{emoji}bold rest"
    entities = [ent("bold", 2, 4)]
    assert entities_to_markdown(text, entities) == f"{emoji}**bold** rest"


def test_entities_to_markdown_nested_ending_same_offset_bold_wraps_link():
    """A text_link ending at the same offset as an enclosing bold span must
    nest as `**bold [link](url)**`, not mis-nest as `**bold [link**](url)`."""
    text = "start bold link end"
    entities = [
        ent("bold", 6, 9),  # "bold link"
        ent("text_link", 11, 4, url="https://x"),  # "link"
    ]
    assert entities_to_markdown(text, entities) == "start **bold [link](https://x)** end"


def test_entities_to_markdown_nested_starting_same_offset_bold_outer_italic_inner():
    text = "bold italic end"
    entities = [
        ent("bold", 0, 11),   # "bold italic"
        ent("italic", 0, 4),  # "bold"
    ]
    assert entities_to_markdown(text, entities) == "***bold* italic** end"


def test_entities_to_markdown_adjacent_entities_share_boundary():
    text = "AAABBB"
    entities = [ent("bold", 0, 3), ent("italic", 3, 3)]
    assert entities_to_markdown(text, entities) == "**AAA***BBB*"


def test_entities_to_markdown_same_start_nesting_independent_of_list_order():
    """Entities sharing a start offset are resolved by (offset, -length), not
    by their position in the input list — the longer (outer) entity wins
    regardless of whether it appears first or last in `entities`."""
    text = "abcde fghij"
    entities = [
        ent("text_link", 0, 5, url="u"),  # "abcde" — shorter, appears first
        ent("bold", 0, 11),               # whole text — longer, appears last
    ]
    assert entities_to_markdown(text, entities) == "**[abcde](u) fghij**"


@pytest.mark.parametrize("entities", [
    [ent("blockquote", 0, 5), ent("bold", 0, 5)],  # block listed first
    [ent("bold", 0, 5), ent("blockquote", 0, 5)],  # block listed last
])
def test_entities_to_markdown_blockquote_always_outermost_over_same_span_inline(entities):
    """A block entity (blockquote/pre) and an inline entity covering exactly
    the same span must nest with the block outermost, regardless of the
    entities list's order — same-span ties aren't resolved by length alone
    (both are length 5 here) but by block-vs-inline rank."""
    assert entities_to_markdown("quote", entities) == "> **quote**"


@pytest.mark.parametrize("entities", [
    [ent("pre", 0, 5), ent("bold", 0, 5)],  # block listed first
    [ent("bold", 0, 5), ent("pre", 0, 5)],  # block listed last
])
def test_entities_to_markdown_pre_always_outermost_over_same_span_inline(entities):
    """Same as blockquote: pre must wrap an equal-span inline entity, not the
    reverse — only assert the fence starts the output (its exact backtick
    count is covered separately), not the full fenced content."""
    assert entities_to_markdown("quote", entities).startswith("```")


@pytest.mark.parametrize("text,entities,expected_fence", [
    ("x", [ent("pre", 0, 1)], "```"),  # 1-char content, no backticks -> min fence of 3
    ("a```b", [ent("pre", 0, 5)], "````"),  # internal ``` run (3) -> fence of 4
    ("a````b", [ent("pre", 0, 6)], "`````"),  # internal ```` run (4) -> fence of 5
])
def test_entities_to_markdown_pre_fence_length_at_least_3_and_beyond_longest_run(text, entities, expected_fence):
    result = entities_to_markdown(text, entities)
    assert result == f"{expected_fence}\n{text}\n{expected_fence}"


@pytest.mark.parametrize("length", [0, -1])
def test_entities_to_markdown_non_positive_length_skipped(length):
    entities = [ent("bold", 0, length)]
    assert entities_to_markdown("hello", entities) == "hello"


def _enum_type(name):
    """Mirrors a real pinned-Pyrogram `MessageEntityType` member: `.name` is
    the uppercase string, `.value` is an opaque raw-TL class — never a string."""
    return SimpleNamespace(name=name, value=object())


def test_entities_to_markdown_enum_like_type_uses_name_attribute():
    """Real Pyrogram 2.0.106 `MessageEntityType` members have raw-TL-class
    `.value`s, not strings — only `.name` ("BOLD") is usable. A double that
    exposes a string `.value` (the old assumption) would mask this."""
    entities = [ent(_enum_type("BOLD"), 6, 4)]
    assert entities_to_markdown("hello bold world", entities) == "hello **bold** world"


class _UnresolvableEntityType:
    """Neither `.name` nor `.value` is a string (e.g. raw ints), and the
    object itself is not a string either — entity_type_name resolves this to
    None, and entities_to_markdown must skip the entity as plain text rather
    than raising."""
    name = 1
    value = 2


def test_entities_to_markdown_unresolvable_type_skipped_as_plain_text():
    entities = [ent(_UnresolvableEntityType(), 6, 4)]
    assert entities_to_markdown("hello bold world", entities) == "hello bold world"


def test_entities_to_markdown_realistic_enum_doubles_end_to_end():
    """End-to-end with every entity using the realistic enum-name shape (never
    a bare lowercase string, never a string `.value`) — this must fail loudly
    if `entity_type_name` reverts to reading `.value`."""
    text = "start bold link end"
    entities = [
        ent(_enum_type("BOLD"), 6, 9),                                  # "bold link"
        ent(_enum_type("TEXT_LINK"), 11, 4, url="https://x"),           # "link"
    ]
    assert entities_to_markdown(text, entities) == "start **bold [link](https://x)** end"


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _post(**kwargs) -> NotifiedPost:
    defaults = dict(
        user_id=1,
        channel_id=100,
        message_id=5,
        sent_at="2026-09-01 10:00:00",
        urls=["https://example.com/job"],
    )
    defaults.update(kwargs)
    return NotifiedPost(**defaults)


def test_render_markdown_heading_is_channel_message_link():
    bodies = {(100, 5): "Some original body text."}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=False)
    assert "### [100/5](https://t.me/chan/5)" in md


def test_render_markdown_body_rendered_verbatim_under_heading():
    bodies = {(100, 5): "Line one\n\n**bold** line two with [a link](https://x.example)"}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=False)
    assert "Line one\n\n**bold** line two with [a link](https://x.example)" in md


def test_render_markdown_no_verdict_metadata_lines():
    """Body-only entries: no score/apply/reason/red-flags/sent lines."""
    bodies = {(100, 5): "Body."}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=False)
    for forbidden in ("Score:", "Apply:", "Reason:", "Red flags:", "Sent:"):
        assert forbidden not in md


def test_render_markdown_entries_separated_by_horizontal_rule():
    bodies = {(100, 5): "Body A", (100, 6): "Body B"}
    posts = [_post(message_id=5), _post(message_id=6)]
    md = render_markdown({1: posts}, START, END, {100: "chan"}, bodies, retention_note=False)
    assert md.count("---") == 2


def test_render_markdown_missing_body_shows_unavailable_and_url_bullets():
    post = _post(urls=["https://example.com/a", "https://example.com/b"])
    md = render_markdown({1: [post]}, START, END, {100: "chan"}, {}, retention_note=False)
    assert "_(original message unavailable)_" in md
    assert "- https://example.com/a" in md
    assert "- https://example.com/b" in md


def test_render_markdown_none_body_shows_unavailable_and_url_bullets():
    bodies = {(100, 5): None}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=False)
    assert "_(original message unavailable)_" in md
    assert "- https://example.com/job" in md


def test_render_markdown_missing_body_no_bullets_when_no_urls():
    post = _post(urls=[])
    md = render_markdown({1: [post]}, START, END, {100: "chan"}, {}, retention_note=False)
    assert "_(original message unavailable)_" in md
    assert "- http" not in md


def test_render_markdown_multiple_users_sections_in_insertion_order():
    sections = {
        2: [_post(user_id=2)],
        1: [_post(user_id=1)],
    }
    bodies = {(100, 5): "Body"}
    md = render_markdown(sections, START, END, {100: "chan"}, bodies, retention_note=False)
    idx_user2 = md.index("## User 2")
    idx_user1 = md.index("## User 1")
    assert idx_user2 < idx_user1


def test_render_markdown_retention_note_present_when_true():
    bodies = {(100, 5): "Body"}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=True)
    assert "only retained for 30 days" in md


def test_render_markdown_retention_note_absent_when_false():
    bodies = {(100, 5): "Body"}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=False)
    assert "only retained for 30 days" not in md


def test_render_markdown_header_retention_note_and_sections_in_order():
    bodies = {(100, 5): "Body"}
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, bodies, retention_note=True)
    idx_header = md.index("# Notification Export")
    idx_retention = md.index("only retained for 30 days")
    idx_section = md.index("## User 1")
    assert idx_header < idx_retention < idx_section


def test_render_markdown_entry_count_in_header():
    sections = {1: [_post(message_id=1), _post(message_id=2)], 2: [_post(user_id=2, message_id=3)]}
    bodies = {(100, 1): "A", (100, 2): "B", (100, 3): "C"}
    md = render_markdown(sections, START, END, {100: "chan"}, bodies, retention_note=False)
    assert "Total entries: 3" in md


def test_render_markdown_uses_links_mapping_for_channel():
    bodies = {(100, 5): "Body"}
    md = render_markdown({1: [_post()]}, START, END, {100: "publicname"}, bodies, retention_note=False)
    assert "https://t.me/publicname/5" in md


def test_render_markdown_falls_back_to_private_link_when_no_username():
    bodies = {(100, 5): "Body"}
    md = render_markdown({1: [_post()]}, START, END, {100: None}, bodies, retention_note=False)
    assert "https://t.me/c/" in md
