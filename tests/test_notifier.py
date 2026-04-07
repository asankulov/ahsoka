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


def make_score(score: int = 8, reason: str = "Good match", apply: str = "") -> Score:
    return Score(score=score, reason=reason, apply=apply)


# ---------------------------------------------------------------------------
# Score line
# ---------------------------------------------------------------------------

def test_score_line_present():
    result = format_notification(make_post(), make_score(score=9, reason="Strong fit"))
    assert "⭐ 9/10 — Strong fit" in result


def test_score_boundaries_zero_and_ten():
    assert "⭐ 0/10" in format_notification(make_post(), make_score(score=0))
    assert "⭐ 10/10" in format_notification(make_post(), make_score(score=10))


# ---------------------------------------------------------------------------
# Apply link
# ---------------------------------------------------------------------------

def test_apply_line_included_when_present():
    result = format_notification(make_post(), make_score(apply="https://apply.example.com"))
    assert "📬 https://apply.example.com" in result


def test_apply_line_absent_when_empty():
    result = format_notification(make_post(), make_score(apply=""))
    assert "📬" not in result


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
    # datetime(2024, 3, 5) → "Mar 5"
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
    """Score line appears before post text, footer comes last."""
    result = format_notification(
        make_post(text="Job body", channel_name="chan"),
        make_score(score=7, reason="OK match", apply="https://apply.io"),
    )
    score_pos = result.index("⭐")
    apply_pos = result.index("📬")
    body_pos = result.index("Job body")
    footer_pos = result.index("@chan")
    assert score_pos < apply_pos < body_pos < footer_pos
