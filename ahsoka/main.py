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
from ahsoka.models import Post, PersonalizedVerdict
from ahsoka.watcher.client import build_pyrogram_client
from ahsoka.watcher.handler import register_watcher_handlers
from ahsoka.watcher.poller import channel_poller
from ahsoka.pipeline.dedup import is_duplicate
from ahsoka.pipeline.keyword_index import KeywordIndex
from ahsoka.pipeline.user_filter import matches_user
from ahsoka.pipeline.scraper import scrape_content, scrape_url
from ahsoka.pipeline.batch_queue import BatchQueue
from ahsoka.pipeline.batch_submitter import BatchSubmitter
from ahsoka.bot.commands import register_bot_commands, BOT_COMMANDS
from ahsoka.bot.notifier import send_notification
from ahsoka.pipeline.tg_resolver import is_tg_link, resolve_tg_link

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _fan_out_verdicts(
    conn: aiosqlite.Connection,
    bot: Bot,
    results: list[tuple[Post, object, PersonalizedVerdict]],
    url: str = "",
) -> None:
    """For each (post, config, verdict) triple: check matches_user, dedup, notify."""
    for post, config, verdict in results:
        logger.info(
            "verdict user_id=%d post=%s/%s matched=%s score=%d threshold=%d",
            verdict.user_id, post.channel_id, post.message_id,
            verdict.matched, verdict.score, config.threshold,
        )
        if not matches_user(verdict, config):
            continue
        if await db.is_notified(conn, config.user_id, post.channel_id, post.message_id, url):
            continue
        try:
            score = verdict.to_score()
            await send_notification(bot, config.notify_chat_id, post, score, url=url or None)
            await db.mark_notified(conn, config.user_id, post.channel_id, post.message_id, url)
        except Exception:
            logger.exception(
                "Failed to notify user %d for %s/%s",
                config.user_id, post.channel_id, post.message_id,
            )


async def _run_batch(
    conn: aiosqlite.Connection,
    bot: Bot,
    submitter: BatchSubmitter,
    batch_queue: BatchQueue,
) -> None:
    """Drain the queue, submit a batch, poll to completion, fan-out notifications."""
    requests = await batch_queue.drain()
    if not requests:
        return

    try:
        batch_id = await submitter.submit(requests)
    except Exception:
        logger.exception("Batch submission failed — %d requests dropped", len(requests))
        return

    results = await submitter.poll_and_process(batch_id, requests)
    if not results:
        return

    # Store verdicts, then fan-out
    for post, _config, verdict in results:
        await db.store_verdict(conn, verdict, post.channel_id, post.message_id)

    logger.info("batch verdicts stored n=%d", len(results))
    await _fan_out_verdicts(conn, bot, results)


async def batch_worker(
    conn: aiosqlite.Connection,
    bot: Bot,
    submitter: BatchSubmitter,
    batch_queue: BatchQueue,
) -> None:
    """Background task: flush → submit → poll → notify loop."""
    while True:
        try:
            if await batch_queue.should_flush():
                await _run_batch(conn, bot, submitter, batch_queue)
        except asyncio.CancelledError:
            # Graceful shutdown: attempt a final flush before exiting
            logger.info("batch_worker cancelled — attempting final flush")
            try:
                await _run_batch(conn, bot, submitter, batch_queue)
            except Exception:
                logger.exception("Error during shutdown flush")
            raise
        except Exception:
            logger.exception("Unexpected error in batch_worker")
        await asyncio.sleep(10)


async def pipeline_worker(
    queue: asyncio.Queue,
    conn: aiosqlite.Connection,
    bot: Bot,
    batch_queue: BatchQueue,
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

            # Snapshot active configs at receipt time
            active_configs = await db.get_all_active_configs(conn)
            if not active_configs:
                logger.debug("No active users — skipping %s/%s", post.channel_id, post.message_id)
                await db.mark_seen(conn, post.channel_id, post.message_id)
                continue

            if len(post.urls) >= 2:
                await _enqueue_fanout(conn, batch_queue, post, active_configs, pyro)
            else:
                await _enqueue_single(conn, batch_queue, post, active_configs, pyro)
        except Exception:
            logger.exception("Pipeline error for %s/%s", post.channel_id, post.message_id)
        finally:
            queue.task_done()


async def _enqueue_single(
    conn: aiosqlite.Connection,
    batch_queue: BatchQueue,
    post: Post,
    active_configs: list,
    pyro,
) -> None:
    """Scrape content for a single-URL post, mark seen, and enqueue for scoring."""
    content = await scrape_content(post, timeout=settings.scrape_timeout_s)
    for url in post.urls:
        if is_tg_link(url):
            resolved = await resolve_tg_link(url, pyro)
            if resolved:
                content += f"\n\n--- linked from {url} ---\n{resolved}"
    await db.mark_seen(conn, post.channel_id, post.message_id)
    await batch_queue.enqueue(post, content, active_configs)


async def _enqueue_fanout(
    conn: aiosqlite.Connection,
    batch_queue: BatchQueue,
    post: Post,
    active_configs: list,
    pyro,
) -> None:
    """For multi-URL posts: one enqueue per URL, mark each as seen."""
    for url in post.urls:
        if await is_duplicate(conn, post, url=url):
            logger.debug("Duplicate URL: %s/%s %s", post.channel_id, post.message_id, url)
            continue
        if is_tg_link(url):
            resolved = await resolve_tg_link(url, pyro)
            content = "\n\n".join(
                filter(None, [post.text, resolved and f"--- linked from {url} ---\n{resolved}"])
            )
        else:
            content = await scrape_url(url, post.text, timeout=settings.scrape_timeout_s)
        await db.mark_seen(conn, post.channel_id, post.message_id, url=url)
        await batch_queue.enqueue(post, content, active_configs)


async def cleanup_worker(conn: aiosqlite.Connection) -> None:
    while True:
        await asyncio.sleep(86_400)  # 24 h
        deleted = await db.delete_old_posts(conn, days=30)
        logger.info("Cleanup: removed %d stale seen_posts rows", deleted)


async def _recover_pending_batches(
    conn: aiosqlite.Connection,
    bot: Bot,
    submitter: BatchSubmitter,
) -> None:
    """On startup: resume polling any batches that were in-flight when the process died."""
    pending = await db.get_pending_batches(conn)
    if not pending:
        return
    logger.info("Recovering %d in-flight batch(es) from previous run", len(pending))
    for row in pending:
        batch_id = row["batch_id"]
        request_map = row["request_map"]
        # We no longer have the original Post/UserConfig objects in memory,
        # so we build minimal BatchRequest stubs from the stored request_map.
        # The poll_and_process loop will parse verdicts from the API response;
        # caller must fetch configs + posts from DB to fan-out.
        logger.info(
            "Recovering batch %s with %d requests — results will be stored but "
            "no notifications will be sent (restart recovery path)",
            batch_id, len(request_map),
        )
        try:
            # Poll without a request list (recovery mode): just fetch and store verdicts.
            await _recover_single_batch(submitter, batch_id, request_map)
        except Exception:
            logger.exception("Failed to recover batch %s", batch_id)


async def _recover_single_batch(
    submitter: BatchSubmitter,
    batch_id: str,
    request_map: dict,
) -> None:
    """Delegate recovery polling to BatchSubmitter.recover()."""
    await submitter.recover(batch_id, request_map)


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
    anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
    register_bot_commands(dp, conn, settings, watched_channels, pyro, keyword_index, anthropic)

    batch_queue = BatchQueue(
        flush_size=settings.batch_flush_size,
        flush_seconds=settings.batch_flush_seconds,
    )
    submitter = BatchSubmitter(
        client=anthropic,
        conn=conn,
        model=settings.claude_model,
        poll_interval_seconds=settings.batch_poll_interval_seconds,
        max_wait_seconds=settings.batch_max_wait_seconds,
    )

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

    # Recover any in-flight batches from a previous run
    await _recover_pending_batches(conn, bot, submitter)

    workers = [
        asyncio.create_task(
            pipeline_worker(queue, conn, bot, batch_queue, pyro, keyword_index)
        )
        for _ in range(3)
    ]
    batch_task = asyncio.create_task(
        batch_worker(conn, bot, submitter, batch_queue)
    )
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
        all_tasks = [polling, poller, cleanup, batch_task, *workers]
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
