import asyncio
import logging

# Pyrogram's sync.py calls asyncio.get_event_loop() at import time.
# On Python 3.12+ there is no implicit event loop, so we create one first.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import aiosqlite
from anthropic import AsyncAnthropic
from aiogram import Bot, Dispatcher

from ahsoka import database as db
from ahsoka.config import settings
from ahsoka.models import Post, Score
from ahsoka.watcher.client import build_pyrogram_client
from ahsoka.watcher.handler import register_watcher_handlers
from ahsoka.watcher.poller import channel_poller
from ahsoka.pipeline.dedup import is_duplicate
from ahsoka.pipeline.keyword_index import KeywordIndex
from ahsoka.pipeline.user_filter import matches_user
from ahsoka.pipeline.scraper import scrape_content, scrape_url
from ahsoka.pipeline.scorer import score_post
from ahsoka.bot.commands import register_bot_commands, BOT_COMMANDS
from ahsoka.bot.notifier import send_notification
from ahsoka.pipeline.tg_resolver import is_tg_link, resolve_tg_link

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _fan_out_to_users(
    conn: aiosqlite.Connection,
    bot: Bot,
    post: Post,
    score: Score,
    url: str = "",
) -> None:
    """Send notifications to all matching users."""
    configs = await db.get_all_active_configs(conn)
    for config in configs:
        if not matches_user(post, score, config):
            continue
        if await db.is_notified(conn, config.user_id, post.channel_id, post.message_id, url):
            continue
        try:
            await send_notification(bot, config.notify_chat_id, post, score, url=url or None)
            await db.mark_notified(conn, config.user_id, post.channel_id, post.message_id, url)
        except Exception:
            logger.exception(
                "Failed to notify user %d for %s/%s",
                config.user_id, post.channel_id, post.message_id,
            )


async def _process_single(
    conn: aiosqlite.Connection,
    bot: Bot,
    anthropic: AsyncAnthropic,
    post: Post,
    pyro,
) -> None:
    if await is_duplicate(conn, post):
        logger.debug("Duplicate: %s/%s", post.channel_id, post.message_id)
        return
    content = await scrape_content(post, timeout=settings.scrape_timeout_s)
    for url in post.urls:
        if is_tg_link(url):
            resolved = await resolve_tg_link(url, pyro)
            if resolved:
                content += f"\n\n--- linked from {url} ---\n{resolved}"
    score = await score_post(anthropic, post, content, settings.claude_model)
    logger.info("Scored %s — %d/10 — %s", post.link, score.score, score.reason)
    await db.mark_seen(
        conn, post.channel_id, post.message_id, score.score,
        score_reason=score.reason, apply_info=score.apply,
    )
    await _fan_out_to_users(conn, bot, post, score)


async def _process_fanout(
    conn: aiosqlite.Connection,
    bot: Bot,
    anthropic: AsyncAnthropic,
    post: Post,
    pyro,
) -> None:
    for url in post.urls:
        if await is_duplicate(conn, post, url=url):
            logger.debug("Duplicate URL: %s/%s %s", post.channel_id, post.message_id, url)
            continue
        if is_tg_link(url):
            resolved = await resolve_tg_link(url, pyro)
            content = "\n\n".join(filter(None, [post.text, resolved and f"--- linked from {url} ---\n{resolved}"]))
        else:
            content = await scrape_url(url, post.text, timeout=settings.scrape_timeout_s)
        score = await score_post(anthropic, post, content, settings.claude_model)
        logger.info("Scored %s [%s] — %d/10 — %s", post.link, url, score.score, score.reason)
        await db.mark_seen(
            conn, post.channel_id, post.message_id, score.score, url=url,
            score_reason=score.reason, apply_info=score.apply,
        )
        await _fan_out_to_users(conn, bot, post, score, url=url)


async def pipeline_worker(
    queue: asyncio.Queue,
    conn: aiosqlite.Connection,
    bot: Bot,
    anthropic: AsyncAnthropic,
    pyro,
    keyword_index: KeywordIndex,
) -> None:
    while True:
        post: Post = await queue.get()
        try:
            if await is_duplicate(conn, post):
                logger.debug("Duplicate: %s/%s", post.channel_id, post.message_id)
                continue

            if not keyword_index.passes(post):
                logger.debug("Keyword drop: %s/%s", post.channel_id, post.message_id)
                await db.mark_seen(conn, post.channel_id, post.message_id)
                continue

            if len(post.urls) >= 2:
                await _process_fanout(conn, bot, anthropic, post, pyro)
            else:
                await _process_single(conn, bot, anthropic, post, pyro)
        except Exception:
            logger.exception("Pipeline error for %s/%s", post.channel_id, post.message_id)
        finally:
            queue.task_done()


async def cleanup_worker(conn: aiosqlite.Connection) -> None:
    while True:
        await asyncio.sleep(86_400)  # 24 h
        deleted = await db.delete_old_posts(conn, days=30)
        logger.info("Cleanup: removed %d stale seen_posts rows", deleted)


async def main() -> None:
    conn = await aiosqlite.connect(settings.db_path)
    await db.init_db(conn, owner_chat_id=settings.owner_chat_id)
    await db.seed_channels(conn, settings.channel_ids)

    watched_channels = await db.load_watched_channels(conn)
    logger.info("Watching %d channel(s)", len(watched_channels))

    keyword_index = KeywordIndex()
    await keyword_index.rebuild(conn)

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    pyro = build_pyrogram_client(settings)
    register_watcher_handlers(pyro, queue, watched_channels)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels, pyro, keyword_index)

    anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Startup health check — fail fast if OWNER_CHAT_ID is wrong
    try:
        await bot.send_message(settings.owner_chat_id, "ahsoka started \u2713")
    except Exception as exc:
        logger.error("Health check failed: %s — verify OWNER_CHAT_ID and BOT_TOKEN", exc)
        raise SystemExit(1)

    # Dedicated log-forwarding bot (optional — only if LOG_BOT_TOKEN is set)
    log_bot: Bot | None = None
    if settings.log_bot_token:
        from ahsoka.bot.log_handler import TelegramLogHandler
        log_bot = Bot(token=settings.log_bot_token)
        try:
            await log_bot.send_message(settings.owner_chat_id, "ahsoka log bot ready \u2713")
        except Exception as exc:
            logger.error("Log bot health check failed: %s — verify LOG_BOT_TOKEN", exc)
        _tg_handler = TelegramLogHandler(log_bot, settings.owner_chat_id)
        _tg_handler.setLevel(logging.DEBUG)
        _tg_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s\n%(message)s"))
        logging.getLogger().addHandler(_tg_handler)
        logger.info("Telegram log handler registered (INFO+) via dedicated log bot")

    # Sync command menu and bot description with Telegram
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        await bot.set_my_description(
            "Ahsoka monitors Telegram job channels, scores posts with AI, "
            "and sends you only the ones that match your filters."
        )
        await bot.set_my_short_description(
            "AI-powered job filter bot for Telegram channels"
        )
        logger.info("Bot command menu updated (%d commands)", len(BOT_COMMANDS))
    except Exception as exc:
        logger.warning("Failed to update bot command menu: %s", exc)

    workers = [
        asyncio.create_task(
            pipeline_worker(queue, conn, bot, anthropic, pyro, keyword_index)
        )
        for _ in range(3)
    ]
    cleanup = asyncio.create_task(cleanup_worker(conn))

    async with pyro:
        # Log which of the watched channels the user account is actually joined to
        joined: list[int] = []
        async for dialog in pyro.get_dialogs():
            if dialog.chat.id in watched_channels:
                joined.append(dialog.chat.id)
        not_joined = watched_channels - set(joined)
        if joined:
            logger.info("Confirmed member of: %s", joined)
        if not_joined:
            logger.warning("NOT a member of (won't receive updates): %s", not_joined)

        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        poller = asyncio.create_task(channel_poller(pyro, queue, watched_channels))
        all_tasks = [polling, poller, cleanup, *workers]
        try:
            await asyncio.gather(*all_tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for task in all_tasks:
                task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
            await dp.storage.close()
            await bot.session.close()
            if log_bot is not None:
                await log_bot.session.close()
            await conn.close()
            logger.info("Shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
