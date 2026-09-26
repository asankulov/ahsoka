"""Tests for pure helpers in ahsoka/bot/export.py: parse_range, build_post_link, render_markdown."""
from datetime import datetime, timezone

from ahsoka.bot.export import build_post_link, parse_range, render_markdown
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
        score=8,
        reason="Great fit",
        apply="hr@example.com",
        red_flags=["vague comp"],
    )
    defaults.update(kwargs)
    return NotifiedPost(**defaults)


def test_render_markdown_verdict_present_shows_score_fields():
    md = render_markdown({1: [_post()]}, START, END, {100: None}, retention_note=False)
    assert "Score: 8/10" in md
    assert "(no verdict stored)" not in md


def test_render_markdown_verdict_absent_shows_no_verdict_stored():
    post = _post(score=None, reason="", apply="", red_flags=[])
    md = render_markdown({1: [post]}, START, END, {100: None}, retention_note=False)
    assert "(no verdict stored)" in md
    assert "Score:" not in md
    assert "Apply:" not in md
    assert "Reason:" not in md
    assert "Red flags:" not in md


def test_render_markdown_empty_fields_rendered_as_dash():
    post = _post(reason="", apply="", red_flags=[], urls=[])
    md = render_markdown({1: [post]}, START, END, {100: None}, retention_note=False)
    assert "Apply: -" in md
    assert "Reason: -" in md
    assert "Red flags: -" in md
    assert "Job URL(s): -" in md


def test_render_markdown_multiple_users_sections_in_insertion_order():
    sections = {
        2: [_post(user_id=2)],
        1: [_post(user_id=1)],
    }
    md = render_markdown(sections, START, END, {100: None}, retention_note=False)
    idx_user2 = md.index("## User 2")
    idx_user1 = md.index("## User 1")
    assert idx_user2 < idx_user1


def test_render_markdown_retention_note_present_when_true():
    md = render_markdown({1: [_post()]}, START, END, {100: None}, retention_note=True)
    assert "only retained for 30 days" in md


def test_render_markdown_retention_note_absent_when_false():
    md = render_markdown({1: [_post()]}, START, END, {100: None}, retention_note=False)
    assert "only retained for 30 days" not in md


def test_render_markdown_entry_count_in_header():
    sections = {1: [_post(message_id=1), _post(message_id=2)], 2: [_post(user_id=2, message_id=3)]}
    md = render_markdown(sections, START, END, {100: None}, retention_note=False)
    assert "Total entries: 3" in md


def test_render_markdown_field_order_link_score_apply_reason_flags_sent_urls():
    md = render_markdown({1: [_post()]}, START, END, {100: "chan"}, retention_note=False)
    order = [
        md.index("[100/5](https://t.me/chan/5)"),
        md.index("Score: 8/10"),
        md.index("Apply: hr@example.com"),
        md.index("Reason: Great fit"),
        md.index("Red flags: vague comp"),
        md.index("Sent: 2026-09-01 10:00:00 UTC"),
        md.index("Job URL(s): https://example.com/job"),
    ]
    assert order == sorted(order)


def test_render_markdown_uses_links_mapping_for_channel():
    md = render_markdown({1: [_post()]}, START, END, {100: "publicname"}, retention_note=False)
    assert "https://t.me/publicname/5" in md


def test_render_markdown_falls_back_to_private_link_when_no_username():
    md = render_markdown({1: [_post()]}, START, END, {100: None}, retention_note=False)
    assert "https://t.me/c/" in md
