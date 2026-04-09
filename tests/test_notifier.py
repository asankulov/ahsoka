from datetime import datetime

from ahsoka.bot.notifier import format_notification
from ahsoka.models import Post, Score


def make_post(
    text: str = "We are hiring a Python developer.",
    channel_name: str = "jobschannel",
    timestamp: datetime | None = None,
    url: str | None = None,
) -> Post:
    return Post(
        channel_id=-100123,
        message_id=1,
        channel_name=channel_name,
        text=text,
        url=url,
        timestamp=timestamp or datetime(2024, 3, 5, 10, 0, 0),
    )


def make_score(score: int = 8, reason: str = "Good match", apply: str = "", **kwargs) -> Score:
    return Score(score=score, reason=reason, apply=apply, **kwargs)


# ---------------------------------------------------------------------------
# Link preview (post link first)
# ---------------------------------------------------------------------------

def test_post_link_is_first_line():
    result = format_notification(make_post(), make_score())
    first_line = result.split("\n")[0]
    assert "t.me/" in first_line


# ---------------------------------------------------------------------------
# Score line
# ---------------------------------------------------------------------------

def test_score_line_present():
    result = format_notification(make_post(), make_score(score=9, reason="Strong fit"))
    assert "\u2b50 9/10 \u2014 Strong fit" in result


def test_score_boundaries_zero_and_ten():
    assert "\u2b50 0/10" in format_notification(make_post(), make_score(score=0))
    assert "\u2b50 10/10" in format_notification(make_post(), make_score(score=10))


# ---------------------------------------------------------------------------
# Apply link
# ---------------------------------------------------------------------------

def test_apply_line_included_when_present():
    result = format_notification(make_post(), make_score(apply="https://apply.example.com"))
    assert "\U0001f4ec https://apply.example.com" in result


def test_apply_line_absent_when_empty():
    result = format_notification(make_post(), make_score(apply=""))
    assert "\U0001f4ec" not in result


# ---------------------------------------------------------------------------
# Post body
# ---------------------------------------------------------------------------

def test_post_text_included():
    result = format_notification(make_post(text="Exciting job opportunity"), make_score())
    assert "Exciting job opportunity" in result


def test_post_text_truncated_at_800_chars():
    long_text = "x" * 1000
    result = format_notification(make_post(text=long_text), make_score())
    assert "x" * 800 in result
    assert "x" * 801 not in result


def test_post_text_not_truncated_when_under_800_chars():
    text = "short text"
    result = format_notification(make_post(text=text), make_score())
    assert text in result


# ---------------------------------------------------------------------------
# Footer line
# ---------------------------------------------------------------------------

def test_footer_contains_channel_name():
    result = format_notification(make_post(channel_name="devjobs"), make_score())
    assert "@devjobs" in result


def test_footer_contains_formatted_date():
    result = format_notification(make_post(timestamp=datetime(2024, 3, 5)), make_score())
    assert "Mar 5" in result


def test_footer_with_non_datetime_timestamp():
    post = make_post()
    post.timestamp = "some-string-date"
    result = format_notification(post, make_score())
    assert "some-string-date" in result


# ---------------------------------------------------------------------------
# Overall structure
# ---------------------------------------------------------------------------

def test_result_is_string():
    assert isinstance(format_notification(make_post(), make_score()), str)


def test_full_notification_order():
    """Link comes first, then score, then apply, then body, then footer."""
    result = format_notification(
        make_post(text="Job body", channel_name="chan"),
        make_score(score=7, reason="OK match", apply="https://apply.io",
                   red_flags=["vague comp"], stack=["python"], seniority="senior", remote="remote"),
    )
    link_pos = result.index("t.me/")
    score_pos = result.index("⭐")
    apply_pos = result.index("📬")
    flags_pos = result.index("⚠️")
    tags_pos = result.index("🏷")
    body_pos = result.index("Job body")
    footer_pos = result.index("@chan")
    assert link_pos < score_pos < apply_pos < flags_pos < tags_pos < body_pos < footer_pos


def test_footer_does_not_duplicate_link():
    """Post link should only appear once (at top for preview), not in footer."""
    result = format_notification(make_post(channel_name="jobschannel"), make_score())
    assert result.count("t.me/jobschannel/1") == 1


# ---------------------------------------------------------------------------
# Red flags and tags
# ---------------------------------------------------------------------------

def test_red_flags_displayed():
    result = format_notification(make_post(), make_score(red_flags=["vague compensation", "no company name"]))
    assert "⚠️ vague compensation, no company name" in result


def test_no_red_flags_line_when_empty():
    result = format_notification(make_post(), make_score(red_flags=[]))
    assert "⚠️" not in result


def test_tags_line_with_stack_seniority_remote():
    result = format_notification(
        make_post(),
        make_score(stack=["python", "django"], seniority="senior", remote="remote"),
    )
    assert "🏷 python django · senior · remote" in result


def test_tags_line_omits_any_and_unknown():
    result = format_notification(make_post(), make_score(stack=["python"], seniority="any", remote="unknown"))
    assert "🏷 python" in result
    assert "any" not in result.split("🏷")[1]
    assert "unknown" not in result.split("🏷")[1]


def test_no_tags_line_when_all_defaults():
    result = format_notification(make_post(), make_score(stack=[], seniority="any", remote="unknown"))
    assert "🏷" not in result


def test_tags_stack_capped_at_five():
    result = format_notification(
        make_post(),
        make_score(stack=["a", "b", "c", "d", "e", "f", "g"]),
    )
    tags_part = result.split("🏷 ")[1].split("\n")[0]
    assert "f" not in tags_part
