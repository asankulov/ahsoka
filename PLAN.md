# Plan: Telegram Job-Filter Bot ("ahsoka")

## Context
Build a Telegram job-filter bot from scratch. A Pyrogram user client monitors ~20 channels (bots can't join arbitrary channels), extracts posts, scrapes any linked URLs, scores relevance via Claude Haiku, and forwards only high-scoring posts to the user via an aiogram bot. The user can configure all criteria via bot commands. Storage is SQLite.

---

## Project Structure

```
ahsoka/
├── .env / .env.example
├── pyproject.toml
├── ahsoka/
│   ├── main.py           # entry point: both clients + workers on one event loop
│   ├── config.py         # pydantic-settings Settings singleton
│   ├── database.py       # aiosqlite: schema, dedup, user_config CRUD
│   ├── models.py         # Post, UserConfig, Score dataclasses
│   ├── watcher/
│   │   ├── client.py     # Pyrogram user client setup
│   │   └── handler.py    # on_message → puts Post into asyncio.Queue
│   ├── pipeline/
│   │   ├── dedup.py      # seen_posts check
│   │   ├── keyword_filter.py  # fast pre-filter (no LLM cost)
│   │   ├── scraper.py    # httpx + trafilatura; 5s timeout; fallback to raw text
│   │   └── scorer.py     # Claude API: score 0–10 + reason; rate-limit handling
│   └── bot/
│       ├── client.py     # aiogram Bot + Dispatcher setup
│       ├── commands.py   # /set* /status /pause /resume handlers
│       └── notifier.py   # formats and sends approved posts to OWNER_CHAT_ID
└── tests/
    ├── test_keyword_filter.py
    ├── test_scraper.py
    ├── test_scorer.py
    └── test_database.py
```

---

## Runtime Channel Management

Channels are stored in a **`watched_channels` table** in SQLite and loaded into a `set[int]` at startup (seeded from `CHANNEL_IDS` env var on first run). The Pyrogram handler is registered with `filters.channel` (catch-all) and checks membership in the shared set at message time — no handler re-registration needed.

```python
# watcher/handler.py
watched_channels: set[int] = set()  # mutated by bot commands at runtime

@pyro.on_message(filters.channel)
async def on_message(client, message):
    if message.chat.id not in watched_channels:
        return
    await queue.put(Post.from_message(message))
```

Bot commands (`/addchannel`, `/removechannel`) mutate `watched_channels` and persist to DB atomically.

---

## Data Flow

```
Pyrogram user client
  └─ on_message(filters.channel)  — checks watched_channels set at runtime
       │
       ▼
  handler.py → asyncio.Queue (maxsize=100)
       │
       ▼  (3 concurrent worker coroutines)
  dedup.py       — already seen? drop
  keyword_filter — keywords configured? must match at least one; empty list = pass all
  scraper.py     — fetch URL (5s timeout), extract text; fallback: raw post text
  scorer.py      — Claude Haiku → {"score": 8, "reason": "..."}; score < threshold? drop
  database.mark_seen(channel_id, message_id, score)
  notifier.py    — bot.send_message(OWNER_CHAT_ID, formatted_message)
```

---

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS seen_posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    score       INTEGER,
    scored_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS user_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watched_channels (
    channel_id  INTEGER PRIMARY KEY,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Default `user_config` keys: `stack`, `seniority`, `remote`, `location`, `salary_min`, `salary_max`, `threshold` (default `7`), `paused` (default `false`), `keywords` (default `""` — empty string means no keyword filter, pass all).

On startup, `watched_channels` table is seeded from `CHANNEL_IDS` env var if the table is empty.

---

## Auth

All bot commands are restricted to `OWNER_CHAT_ID`. An aiogram middleware (`OwnerOnlyMiddleware`) silently drops any update where `message.from_user.id != settings.OWNER_CHAT_ID`. Registered globally via `dp.message.middleware(OwnerOnlyMiddleware(settings.OWNER_CHAT_ID))`.

---

## Bot Commands

| Command | Example | Effect |
|---------|---------|--------|
| `/setstack` | `/setstack python go` | Update desired stack |
| `/setseniority` | `/setseniority senior` | Update seniority level |
| `/setremote` | `/setremote remote` | Update work mode |
| `/setlocation` | `/setlocation Amsterdam` | Update location |
| `/setsalary` | `/setsalary 5000 10000` | Set monthly salary range |
| `/setthreshold` | `/setthreshold 8` | Set minimum score (0–10) |
| `/setkeywords` | `/setkeywords python golang backend` | Set required keywords; empty = pass all |
| `/status` | `/status` | Show current criteria |
| `/addchannel` | `/addchannel -1001234567890` | Add a channel to the watch list |
| `/removechannel` | `/removechannel -1001234567890` | Remove a channel from the watch list |
| `/channels` | `/channels` | List currently watched channels |
| `/pause` | `/pause` | Stop forwarding (still marks seen) |
| `/resume` | `/resume` | Resume forwarding |

---

## Notification Format

```
⭐ 8/10 — Strong Python/FastAPI match, remote ok
📬 hr@company.com · @recruiter_handle

<original post text truncated to ~800 chars>

— @channel_name · Apr 3
```

- `📬` line is omitted entirely if no apply/contact info is found
- Post text in the notification is truncated to ~800 chars (display only; scorer still receives ~4000 chars)
- Apply/contact info is extracted by the LLM as part of scoring — no separate step

---

## LLM Scorer Prompt (dynamic)

```
You are a job relevance scorer. The user is looking for:
- Stack: {config.stack}
- Seniority: {config.seniority}
- Work mode: {config.remote}
- Location: {config.location}
- Monthly salary: {config.salary_min}–{config.salary_max}

Score the following job posting from 0 to 10. Also extract any contact or application info (email, Telegram handle, apply link, "DM @x", etc.) into the `apply` field — leave it empty string if none found.
Return ONLY valid JSON: {"score": <int>, "reason": "<one sentence>", "apply": "<contact/apply info or empty string>"}
```

Post content is truncated to ~4 000 chars before sending to control token cost.

---

## Concurrency Model (main.py)

```python
async def main():
    conn = await aiosqlite.connect(settings.DB_PATH)
    await init_db(conn)
    queue = asyncio.Queue(maxsize=100)

    pyro = build_pyrogram_client(settings)
    watched_channels = await load_watched_channels(conn, settings)
    register_watcher_handlers(pyro, queue, watched_channels)

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels)

    workers = [asyncio.create_task(pipeline_worker(queue, conn, bot, settings))
               for _ in range(3)]
    cleanup = asyncio.create_task(cleanup_worker(conn))

    async with pyro:
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        await asyncio.gather(polling, cleanup, *workers)
```

- `async with pyro:` — Pyrogram's async context manager; handlers fire as tasks on the shared loop
- `handle_signals=False` — aiogram doesn't fight asyncio.run over SIGINT
- Single `aiosqlite` connection is safe; all access is from one event loop thread
- `cleanup_worker` sleeps 24h then deletes `seen_posts` rows older than 30 days (`delete_old_posts(conn, days=30)`)

---

## Environment Variables (.env)

```dotenv
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
SESSION_NAME=ahsoka_user
BOT_TOKEN=
OWNER_CHAT_ID=          # numeric user ID; get from @userinfobot
CHANNEL_IDS=            # comma-separated negative ints, e.g. -1001234567890,...
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-4-5-20251001
DEFAULT_SCORE_THRESHOLD=7
SCRAPE_TIMEOUT_S=5.0
DB_PATH=ahsoka.db
```

---

## Dependencies (pyproject.toml)

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "pyrogram",
    "tgcrypto",          # required by pyrogram for encryption
    "aiogram>=3",
    "httpx",
    "trafilatura",
    "anthropic",
    "aiosqlite",
    "pydantic-settings",
]
```

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Claude API rate limits | `asyncio.Semaphore(5)` in scorer; exponential backoff on `RateLimitError` |
| JS-rendered job boards (LinkedIn, etc.) | Fallback to raw post text; no Playwright |
| Pyrogram session conflicts | Never run two instances; `.session` file is single-writer |
| `OWNER_CHAT_ID` misconfigured | Startup health check: send test message; exit on failure |
| `seen_posts` growing unboundedly | Periodic cleanup task deletes rows older than 30 days |
| Paused state + post flood on resume | Mark seen even when paused (stale jobs are rarely actionable) |
| Message formatting entities confusing LLM | Use `message.text` (plain), not HTML/Markdown caption |

---

## Verification

1. **Unit tests**: `pytest tests/` — keyword filter, scraper fallback, scorer JSON parsing, DB dedup
2. **Integration smoke test**:
   - Set criteria via bot commands, check `/status`
   - Forward a test message manually to a monitored channel
   - Confirm it appears in the bot chat with correct score/reason
3. **Keyword pre-filter validation**: post with no matching keywords should never reach Claude (add a counter/log)
4. **Scraper test**: post a URL to a plain-HTML job page; confirm scraped content appears in the scorer prompt (visible via debug log)
5. **Pause/resume**: pause, trigger a post, resume, confirm post does NOT re-appear
6. **Runtime channel management**: `/addchannel` a new channel, confirm posts from it appear without restart; `/removechannel` it, confirm posts stop; `/channels` reflects current state
