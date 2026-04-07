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
    commands.py     # All 25 aiogram command handlers + FSM states
  pipeline/
    scraper.py      # HTTP scraping; skips t.me links (handled by tg_resolver)
    tg_resolver.py  # Resolves t.me deep links via Pyrogram client
    models.py       # Post dataclass + Post.from_message()
    notifier.py     # format_notification()
    dedup.py        # is_duplicate()
  main.py           # Entry point; pipeline_worker; calls set_my_commands() on startup
  config.py         # Settings (pydantic); instantiated at module level — needs env vars at import time
tests/
  conftest.py       # Injects dummy env vars before collection (needed by config.py)
  test_commands.py  # 31+ tests for all command handlers + FSM states
  test_models.py    # 15 tests for Post.from_message()
  test_notifier.py  # 12 tests for format_notification()
  test_dedup.py     # 4 tests for is_duplicate()
  test_tg_resolver.py # 15 tests for is_tg_link / resolve_tg_link
  test_scraper.py   # scraper tests incl. t.me-skip behavior
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

### config.py Import Side Effect
`config.py` calls `Settings()` at module level. Any test file that imports from `ahsoka.*` will fail unless the required env vars are set first. `tests/conftest.py` handles this with `os.environ.setdefault(...)` for all 5 required vars.

## Git Branches (PRs)
- PR #2: Test coverage expansion + keyword command split
- PR #3: Bot command menu sync on startup (`set_my_commands`)
- PR #4: FSM wait-for-input behavior
- PR #5: Telegram deep link resolution via Pyrogram (`claude/resolve-tg-deep-links`)

## Running Tests
```bash
uv run --extra dev pytest
```
Do NOT use `uv run pytest` — it picks up system pytest (Python 3.11) without project deps.
