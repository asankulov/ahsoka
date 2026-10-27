import logging
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat
from aiogram.methods import SendMessage

from ahsoka.bot.notifier import format_notification, send_notification
from ahsoka.models import Post, Score


def make_post(
    text: str = "We are hiring a Python developer.",
    channel_name: str = "jobschannel",
    timestamp: datetime | None = None,
    url: str | None = None,
    channel_id: int = -100123,
) -> Post:
    return Post(
        channel_id=channel_id,
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


# ---------------------------------------------------------------------------
# Group chat support: link / footer
# ---------------------------------------------------------------------------

def test_link_first_line_followed_by_blank_when_present():
    lines = format_notification(make_post(), make_score()).split("\n")
    assert lines[0] == "https://t.me/jobschannel/1"
    assert lines[1] == ""
    assert lines[2].startswith("\u2b50")


def test_no_leading_blank_line_when_link_is_none():
    # basic group: negative id without -100 prefix and numeric name -> Post.link is None
    post = make_post(channel_name="-4567", channel_id=-4567)
    assert post.link is None
    result = format_notification(post, make_score(score=7, reason="ok"))
    assert result.split("\n")[0] == "\u2b50 7/10 \u2014 ok"


def test_footer_shows_chat_id_for_numeric_channel_name():
    result = format_notification(make_post(channel_name="-100123"), make_score())
    footer = result.split("\n")[-1]
    assert footer.startswith("\u2014 chat -100123 \u00b7 ")
    assert "@-100123" not in result


def test_footer_shows_at_username_for_named_channel():
    footer = format_notification(make_post(channel_name="jobschannel"), make_score()).split("\n")[-1]
    assert footer.startswith("\u2014 @jobschannel \u00b7 ")


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------

def _migrate_exc(new_id=-100999):
    return TelegramMigrateToChat(method=SendMessage(chat_id=-5, text="x"), message="migrated", migrate_to_chat_id=new_id)


def _forbidden_exc():
    return TelegramForbiddenError(method=SendMessage(chat_id=-5, text="x"), message="bot was kicked")


async def test_send_notification_happy_path_sends_once():
    bot = AsyncMock()
    await send_notification(bot, 42, make_post(), make_score())
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 42


async def test_send_notification_migrate_updates_target_and_resends_once():
    bot = AsyncMock()
    bot.send_message.side_effect = [_migrate_exc(-100999), None]
    conn = object()
    with patch("ahsoka.bot.notifier.db.set_notify_target", new_callable=AsyncMock) as set_target:
        await send_notification(bot, -5, make_post(), make_score(), conn=conn, user_id=7)
    set_target.assert_awaited_once_with(conn, 7, -100999)
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[0].args[0] == -5
    assert bot.send_message.await_args_list[1].args[0] == -100999
    assert bot.send_message.await_args_list[0].args[1] == bot.send_message.await_args_list[1].args[1]


@pytest.mark.parametrize("conn,user_id", [(None, None), (object(), None), (None, 7)])
async def test_send_notification_migrate_without_conn_or_user_retries_without_db_update(conn, user_id):
    bot = AsyncMock()
    bot.send_message.side_effect = [_migrate_exc(-100999), None]
    with patch("ahsoka.bot.notifier.db.set_notify_target", new_callable=AsyncMock) as set_target:
        await send_notification(bot, -5, make_post(), make_score(), conn=conn, user_id=user_id)
    set_target.assert_not_awaited()
    assert bot.send_message.await_args_list[1].args[0] == -100999


async def test_send_notification_forbidden_logs_warning_without_raising(caplog):
    bot = AsyncMock()
    bot.send_message.side_effect = _forbidden_exc()
    with caplog.at_level(logging.DEBUG, logger="ahsoka.bot.notifier"):
        await send_notification(bot, -5, make_post(), make_score())
    recs = [r for r in caplog.records if r.name == "ahsoka.bot.notifier"]
    assert any(r.levelno == logging.WARNING for r in recs)
    assert not any(r.exc_info for r in recs)
    assert not any(r.levelno >= logging.ERROR for r in recs)


async def test_send_notification_generic_error_uses_logger_exception():
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("boom")
    with patch("ahsoka.bot.notifier.logger") as lg:
        await send_notification(bot, 42, make_post(), make_score())
    lg.exception.assert_called_once()
    lg.warning.assert_not_called()


async def test_send_notification_forbidden_does_not_call_logger_exception():
    bot = AsyncMock()
    bot.send_message.side_effect = _forbidden_exc()
    with patch("ahsoka.bot.notifier.logger") as lg:
        await send_notification(bot, 42, make_post(), make_score())
    lg.warning.assert_called_once()
    lg.exception.assert_not_called()


async def test_send_notification_forbidden_on_resend_after_migrate_is_warning():
    bot = AsyncMock()
    bot.send_message.side_effect = [_migrate_exc(), _forbidden_exc()]
    with patch("ahsoka.bot.notifier.logger") as lg:
        await send_notification(bot, -5, make_post(), make_score())
    lg.warning.assert_called_once()
    lg.exception.assert_not_called()
