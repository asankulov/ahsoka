import logging
from datetime import datetime

from aiogram import Bot

from ahsoka.models import Post, Score

logger = logging.getLogger(__name__)


def _post_link(post: Post) -> str:
    name = post.channel_name
    if name.lstrip("-").isdigit():
        raw_id = str(abs(post.channel_id))[3:]  # strip leading "100" from e.g. 1001234567890
        return f"https://t.me/c/{raw_id}/{post.message_id}"
    return f"https://t.me/{name}/{post.message_id}"


def format_notification(post: Post, score: Score, url: str | None = None) -> str:
    lines = [f"⭐ {score.score}/10 — {score.reason}"]
    if score.apply:
        lines.append(f"📬 {score.apply}")
    if url:
        lines.append(f"🔗 {url}")
    lines.append("")
    lines.append(post.text[:800])
    if isinstance(post.timestamp, datetime):
        date_str = post.timestamp.strftime("%b %-d")
    else:
        date_str = str(post.timestamp)
    lines.append(f"\n— @{post.channel_name} · {date_str} · {_post_link(post)}")
    return "\n".join(lines)


async def send_notification(
    bot: Bot, chat_id: int, post: Post, score: Score, url: str | None = None
) -> None:
    text = format_notification(post, score, url)
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Failed to send notification for %s/%s", post.channel_id, post.message_id)
