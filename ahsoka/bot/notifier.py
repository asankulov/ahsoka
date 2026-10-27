import logging
from datetime import datetime

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat

from ahsoka import database as db
from ahsoka.models import Post, Score

logger = logging.getLogger(__name__)


def format_notification(post: Post, score: Score, url: str | None = None) -> str:
    lines = []
    link = post.link
    if link:
        lines.append(link)
        lines.append("")
    lines.append(f"⭐ {score.score}/10 — {score.reason}")
    if score.apply:
        lines.append(f"📬 {score.apply}")
    if score.red_flags:
        lines.append(f"⚠️ {', '.join(score.red_flags)}")
    tags = []
    if score.stack:
        tags.append(" ".join(score.stack[:5]))
    if score.seniority != "any":
        tags.append(score.seniority)
    if score.remote != "unknown":
        tags.append(score.remote)
    if tags:
        lines.append(f"🏷 {' · '.join(tags)}")
    if url:
        lines.append(f"🔗 {url}")
    lines.append("")
    lines.append(post.text[:800])
    if isinstance(post.timestamp, datetime):
        date_str = post.timestamp.strftime("%b %-d %H:%M")
    else:
        date_str = str(post.timestamp)
    if post.channel_name.lstrip("-").isdigit():
        source = f"chat {post.channel_name}"  # no username: show the ID instead of "@-100…"
    else:
        source = f"@{post.channel_name}"
    lines.append(f"\n— {source} · {date_str}")
    return "\n".join(lines)


async def send_notification(
    bot: Bot,
    chat_id: int,
    post: Post,
    score: Score,
    url: str | None = None,
    conn: aiosqlite.Connection | None = None,
    user_id: int | None = None,
) -> None:
    text = format_notification(post, score, url)
    try:
        try:
            await bot.send_message(chat_id, text)
        except TelegramMigrateToChat as exc:
            # Basic group upgraded to a supergroup: the chat ID changed.
            new_id = exc.migrate_to_chat_id
            logger.info("Notify target %s migrated to %s", chat_id, new_id)
            if conn is not None and user_id is not None:
                await db.set_notify_target(conn, user_id, new_id)
            await bot.send_message(new_id, text)
    except TelegramForbiddenError as exc:
        logger.warning(
            "Cannot send notification to %s for %s/%s: %s",
            chat_id, post.channel_id, post.message_id, exc,
        )
    except Exception:
        logger.exception("Failed to send notification for %s/%s", post.channel_id, post.message_id)
