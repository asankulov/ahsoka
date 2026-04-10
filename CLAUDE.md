# Ahsoka — Project Context

**Workflow:** When working in this directory, follow the principal engineer workflow in [.claude/PRINCIPAL.md](.claude/PRINCIPAL.md).

## What This Is
A multi-user Telegram job-filter bot written in Python 3.12+. It monitors public job channels via Pyrogram, scrapes linked pages, scores posts with Claude AI **once per post**, and fans out matching notifications to each user based on their personal filters.

## Tech Stack
- **Python 3.12+**, **aiogram 3.26.0** (bot/commands), **Pyrogram** (channel listener)
- **Anthropic Claude API** (job post scoring — generic, not per-user)
- **aiosqlite** (multi-user config, dedup, notification tracking)
- **httpx + trafilatura** (HTTP scraping)
- **pytest + pytest-asyncio** (`asyncio_mode = "auto"`) for async tests
- **uv** for dependency management — always run tests with `uv run --extra dev pytest`

## Project Structure
```
ahsoka/
  bot/
    commands.py     # User + admin command handlers, two routers, FSM states
    log_handler.py  # TelegramLogHandler — forwards INFO+ logs to a dedicated Telegram bot
    notifier.py     # format_notification() + send_notification()
  pipeline/
    scraper.py      # HTTP scraping; skips t.me links (handled by tg_resolver)
    tg_resolver.py  # Resolves t.me deep links via Pyrogram client
    scorer.py       # Claude API: generic score 0–10 + reason; rate-limit handling
    dedup.py        # is_duplicate()
    keyword_filter.py  # Fast keyword pre-filter (no LLM cost)
    keyword_index.py   # Union of all users' keywords for shared pre-filter
    user_filter.py     # Per-user post-scoring filter (threshold, keywords, paused)
  models.py         # Post, Score, User, UserConfig dataclasses
  main.py           # Entry point; pipeline_worker; fan-out; log bot setup
  config.py         # Settings (pydantic); instantiated at module level
  database.py       # aiosqlite: schema, migration, multi-user CRUD
  watcher/
    client.py       # Pyrogram user client setup
    handler.py      # on_raw_update → puts Post into asyncio.Queue
    poller.py       # Fallback: polls each channel every 60s
tests/
  test_commands.py      # Command handlers + FSM states (name-based handler lookup)
  test_log_handler.py   # TelegramLogHandler
  test_models.py        # Post.from_message()
  test_database.py      # Multi-user database CRUD
  test_notifier.py      # format_notification()
  test_scraper.py       # Scraper incl. t.me-skip behavior
  test_keyword_filter.py # Keyword filtering
  test_tg_resolver.py   # is_tg_link / resolve_tg_link
  test_scorer.py        # Claude scorer (generic, no UserConfig)
  test_dedup.py         # is_duplicate()
```

## Key Design Decisions

### Multi-User Architecture
- **Score once, fan out**: each post is scored by Claude once globally, then per-user filters (keywords, threshold, paused) determine who receives notifications. API cost scales with post volume, not user count.
- **Union keyword pre-filter**: `KeywordIndex` maintains a union of all users' keywords. Posts are only sent to Claude if they match at least one user's keyword (or any user has empty keywords = match all).
- **Per-user config**: each user has their own `UserConfig` (stack, seniority, keywords, threshold, etc.) stored in a columnar `user_config` table.

### Bot Commands (commands.py)
- Two routers: `user_router` (open to all, checks not banned) and `admin_router` (admin-only)
- `BOT_COMMANDS` lists user-visible commands registered via `bot.set_my_commands()`. Admin commands are hidden.
- **FSM**: `WaitingForInput(StatesGroup)` with states for all input-requiring commands
- Channel discovery: `/watch` then forward a message → bot joins channel and adds to watchlist
- Notification target: `/notify` then forward a message → bot verifies access, sets target; `/notify dm` resets to DM

### Testing aiogram Handlers
Handlers are closures registered on Routers. Tests use name-based lookup:
```python
def get_handler_map(dp: Dispatcher) -> dict[str, object]:
    handlers = {}
    for router in dp.sub_routers:
        for h in router.message.handlers:
            handlers[h.callback.__name__] = h.callback
    return handlers
```

### Database Schema
Five tables: `users` (registry), `user_config` (per-user settings), `seen_posts` (dedup+scoring), `watched_channels` (shared watchlist), `user_notified` (per-user notification tracking).

Migration from old single-user key-value `user_config` is handled automatically in `init_db()`.

### Notification Format
Post link is placed first in the message to trigger Telegram's rich link preview for easy bookmarking. Format: link → score → apply → body → footer.

### Telegram Deep Link Resolution (tg_resolver.py)
Job channels use a two-post pattern: summary post with a `text_link` entity pointing to a full description post. HTTP fetching of `t.me` URLs fails; the Pyrogram client fetches the linked message directly.

### Log Forwarding (log_handler.py)
A dedicated Telegram bot (optional, via `LOG_BOT_TOKEN`) forwards DEBUG+ log records to the owner. Noisy loggers are filtered out. Messages truncated to 4096 chars.

### config.py Import Side Effect
`config.py` calls `Settings()` at module level. Tests that need `Settings` use `MagicMock(spec=Settings)` to avoid the import side effect.

## Running Tests
```bash
uv run --extra dev pytest
```
Do NOT use `uv run pytest` — it picks up system pytest without project deps.

Note: `test_scraper.py` and `test_tg_resolver.py` may fail at collection time on Python 3.12+ due to Pyrogram's `asyncio.get_event_loop()` call — this is a pre-existing Pyrogram issue.
