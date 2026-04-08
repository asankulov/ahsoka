# Ahsoka — Project Context

## What This Is
A personal Telegram job-filter bot written in Python 3.12+. It monitors job channels via Pyrogram, scrapes linked pages, scores posts with Claude AI, and forwards matching ones to the owner via a bot.

## Tech Stack
- **Python 3.12+**, **aiogram 3.26.0** (bot/commands), **Pyrogram** (channel listener)
- **Anthropic Claude API** (job post scoring)
- **aiosqlite** (dedup store)
- **httpx + trafilatura** (HTTP scraping)
- **pytest + pytest-asyncio** (`asyncio_mode = "auto"`) for async tests
- **uv** for dependency management — always run tests with `uv run --extra dev pytest`

## Project Structure
```
ahsoka/
  bot/
    commands.py     # All aiogram command handlers + FSM states
    log_handler.py  # TelegramLogHandler — forwards INFO+ logs to a dedicated Telegram bot
    notifier.py     # format_notification() + send_notification()
  pipeline/
    scraper.py      # HTTP scraping; skips t.me links (handled by tg_resolver)
    tg_resolver.py  # Resolves t.me deep links via Pyrogram client
    scorer.py       # Claude API: score 0–10 + reason; rate-limit handling
    dedup.py        # is_duplicate()
    keyword_filter.py # Fast keyword pre-filter (no LLM cost)
  models.py         # Post (with .link property), Score, UserConfig dataclasses
  main.py           # Entry point; pipeline_worker; log bot setup; calls set_my_commands()
  config.py         # Settings (pydantic); instantiated at module level — needs env vars at import time
  database.py       # aiosqlite: schema, dedup, user_config CRUD
  watcher/
    client.py       # Pyrogram user client setup
    handler.py      # on_raw_update → puts Post into asyncio.Queue
    poller.py       # Fallback: polls each channel every 60s
tests/
  test_commands.py      # 65 tests for command handlers + FSM states
  test_log_handler.py   # 21 tests for TelegramLogHandler
  test_models.py        # 15 tests for Post.from_message()
  test_database.py      # 13 tests for database CRUD
  test_notifier.py      # 12 tests for format_notification()
  test_scraper.py       # 11 tests for scraper incl. t.me-skip behavior
  test_keyword_filter.py # 7 tests for keyword filtering
  test_tg_resolver.py   # 15 tests for is_tg_link / resolve_tg_link
  test_scorer.py        # 4 tests for Claude scorer
  test_dedup.py         # 4 tests for is_duplicate()
```

## Key Design Decisions

### Bot Commands (commands.py)
- `BOT_COMMANDS` constant lists all 15 user-facing commands; registered via `bot.set_my_commands()` at startup in `main.py`
- Keywords managed by three commands: `/setkeywords` (replace all), `/addkeyword` (append, dedup), `/resetkeywords` (clear)
- **FSM**: `WaitingForInput(StatesGroup)` with 10 states — when a command requiring input is invoked with no args, the handler prompts the user and sets FSM state; the next message is handled by a state-specific handler
- All command handlers call `await state.clear()` first to reset any in-progress FSM

### Testing aiogram Handlers
Handlers are closures registered on a Router. To access them in tests:
```python
def get_handlers(dp: Dispatcher) -> list:
    router = dp.sub_routers[0]
    return [h.callback for h in router.message.handlers]
```
Handler indices are positional — update tests if handler order changes.

### Telegram Deep Link Resolution (tg_resolver.py)
Job channels use a two-post pattern: summary post with a `text_link` entity pointing to a full description post (which contains recruiter contact). HTTP fetching of `t.me` URLs fails; instead, the authenticated Pyrogram client fetches the linked message directly.
- `is_tg_link(url)` — matches `t.me/<username>/<id>` and `t.me/c/<channel_id>/<id>`
- `resolve_tg_link(url, client)` — returns `msg.text or msg.caption or None`
- `scraper.py` filters out t.me URLs before HTTP fetching; `main.py` resolves them post-scrape and appends content

### Log Forwarding (log_handler.py)
A dedicated Telegram bot (optional, via `LOG_BOT_TOKEN`) forwards INFO+ log records to the owner in real time. Noisy loggers (`aiogram`, `pyrogram`, `httpx`, `httpcore`) are filtered out. Messages are truncated to Telegram's 4096-char limit (tail preserved). The handler is fire-and-forget via `loop.create_task()`.

### config.py Import Side Effect
`config.py` calls `Settings()` at module level. Any test file that imports from `ahsoka.*` will fail unless the required env vars are set first. Tests that need `Settings` use `MagicMock(spec=Settings)` to avoid the import side effect.

## Running Tests
```bash
uv run --extra dev pytest
```
Do NOT use `uv run pytest` — it picks up system pytest (Python 3.11) without project deps.
