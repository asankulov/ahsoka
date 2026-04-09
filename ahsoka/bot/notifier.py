import logging
from datetime import datetime

from aiogram import Bot

from ahsoka.models import Post, Score

logger = logging.getLogger(__name__)


def format_notification(post: Post, score: Score, url: str | None = None) -> str:
    lines = [post.link]
    lines.append("")
    lines.append(f"⭐ {score.score}/10 — {score.reason}")
    if score.apply:
        lines.append(f"📬 {score.apply}")
    if url:
        lines.append(f"🔗 {url}")
    lines.append("")
    lines.append(post.text[:800])
    if isinstance(post.timestamp, datetime):
        date_str = post.timestamp.strftime("%b %-d %H:%M")
    else:
        date_str = str(post.timestamp)
    lines.append(f"\n— @{post.channel_name} · {date_str}")
    return "\n".join(lines)


async def send_notification(
    bot: Bot, chat_id: int, post: Post, score: Score, url: str | None = None
) -> None:
    text = format_notification(post, score, url)
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Failed to send notification for %s/%s", post.channel_id, post.message_id)
