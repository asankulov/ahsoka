# Plan: Telegram Job-Filter Bot ("ahsoka")

## Context
Build a Telegram job-filter bot from scratch. A Pyrogram user client monitors ~20 channels (bots can't join arbitrary channels), extracts posts, scrapes any linked URLs, scores relevance via Claude Haiku, and forwards only high-scoring posts to the user via an aiogram bot. The user can configure all criteria via bot commands. Storage is SQLite.

---

## Project Structure

```
ahsoka/
├── .env / .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
├── .github/
│   └── workflows/
│       └── deploy.yml        # CI/CD: test on PR, deploy to Hetzner on main
├── ahsoka/
│   ├── main.py               # entry point: both clients + workers on one event loop
│   ├── config.py             # pydantic-settings Settings singleton
│   ├── database.py           # aiosqlite: schema, dedup, user_config CRUD
│   ├── models.py             # Post, UserConfig, Score dataclasses
│   ├── watcher/
│   │   ├── client.py         # Pyrogram user client setup
│   │   ├── handler.py        # on_raw_update → puts Post into asyncio.Queue
│   │   └── poller.py         # fallback: polls each channel every 60s
│   ├── pipeline/
│   │   ├── dedup.py          # seen_posts check
│   │   ├── keyword_filter.py # fast pre-filter (no LLM cost)
│   │   ├── scraper.py        # httpx + trafilatura; 5s timeout; fallback to raw text
│   │   └── scorer.py         # Claude API: score 0–10 + reason; rate-limit handling
│   └── bot/
│       ├── commands.py       # /set* /status /pause /resume handlers
│       └── notifier.py       # formats and sends approved posts to OWNER_CHAT_ID
└── tests/
    ├── test_keyword_filter.py
    ├── test_scraper.py
    ├── test_scorer.py
    └── test_database.py
```

---

## Runtime Channel Management

Channels are stored in a **`watched_channels` table** in SQLite and loaded into a `set[int]` at startup (seeded from `CHANNEL_IDS` env var on first run). The raw update handler checks membership in the shared set at message time — no handler re-registration needed.

```python
# watcher/handler.py
watched_channels: set[int] = set()  # mutated by bot commands at runtime

@pyro.on_raw_update()
async def on_raw(c, update, users, chats):
    if not isinstance(update, (UpdateNewChannelMessage, UpdateNewMessage)):
        return
    # compute chat_id from peer, check watched_channels set
    if chat_id not in watched_channels:
        return
    await queue.put(post)
```

Bot commands (`/addchannel`, `/removechannel`) mutate `watched_channels` and persist to DB atomically.

---

## Data Flow

```
Pyrogram user client
  ├─ on_raw_update handler     — immediate, fires on push updates
  └─ channel_poller            — fallback sweep every 60s, 20 msgs/channel
       │
       ▼
  handler.py / poller.py → asyncio.Queue (maxsize=100)
       │
       ▼  (3 concurrent worker coroutines)
  dedup.py       — already seen? drop
  keyword_filter — keywords configured? must match at least one; empty list = pass all
  scraper.py     — fetch URL (5s timeout), extract text; fallback: raw post text
  scorer.py      — Claude Haiku → {"score": 8, "reason": "..."}; score < threshold? drop
  database.mark_seen(channel_id, message_id, score)
  notifier.py    — bot.send_message(OWNER_CHAT_ID, formatted_message)
```

**Note on Pyrogram push updates**: Telegram only delivers `UpdateNewChannelMessage` push events for channels the client has recently interacted with. The 60s poller exists as a reliable fallback for channels that go quiet on push.

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

**JSON extraction**: the API call uses assistant-turn prefilling (`{"role": "assistant", "content": "{"}`) to force the model to continue from an open brace, guaranteeing raw JSON output without markdown fences. The `{` is prepended back before `json.loads()`.

---

## Concurrency Model (main.py)

```python
async def main():
    conn = await aiosqlite.connect(settings.DB_PATH)
    await init_db(conn)
    queue = asyncio.Queue(maxsize=100)

    pyro = build_pyrogram_client(settings)
    watched_channels = await load_watched_channels(conn)
    register_watcher_handlers(pyro, queue, watched_channels)

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    register_bot_commands(dp, conn, settings, watched_channels)

    workers = [asyncio.create_task(pipeline_worker(queue, conn, bot, anthropic))
               for _ in range(3)]
    cleanup = asyncio.create_task(cleanup_worker(conn))

    async with pyro:
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        poller  = asyncio.create_task(channel_poller(pyro, queue, watched_channels))
        await asyncio.gather(polling, poller, cleanup, *workers)
```

- `async with pyro:` — Pyrogram's async context manager; handlers fire as tasks on the shared loop
- `handle_signals=False` — aiogram doesn't fight asyncio.run over SIGINT
- Single `aiosqlite` connection is safe; all access is from one event loop thread
- `cleanup_worker` sleeps 24h then deletes `seen_posts` rows older than 30 days (`delete_old_posts(conn, days=30)`)
- `channel_poller` wakes every 60s and fetches the 20 most recent messages per channel

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

## Deployment (Hetzner Cloud + systemd)

**Provider**: Hetzner CX22 (~€4/mo) — persistent local disk, no volume addons needed.

**Persistent files** (never touched by deploys):
- `ahsoka.db` — SQLite database
- `ahsoka/ahsoka_user.session` — Pyrogram auth session (single-writer; losing it requires phone re-auth)

**systemd unit** (`/etc/systemd/system/ahsoka.service`):
```ini
[Unit]
Description=Ahsoka Telegram Job-Filter Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ahsoka
Group=ahsoka
WorkingDirectory=/home/ahsoka/ahsoka
ExecStart=/home/ahsoka/ahsoka/.venv/bin/python -m ahsoka.main
Restart=always
RestartSec=5s
EnvironmentFile=/home/ahsoka/ahsoka/.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/ahsoka/ahsoka
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ahsoka

[Install]
WantedBy=multi-user.target
```

**Logs**: `journalctl -u ahsoka -f`

---

## Batch Scoring — Operator Runbook

Scoring goes through Anthropic's **Message Batches API** (per-user personalized prompts, 50% flat cost discount). Notifications are **near-real-time, not instant**.

**Typical notification latency**

`BATCH_FLUSH_SECONDS` (default 10 min) + Anthropic batch processing time (typically 1–15 min) = **10–25 min end-to-end**. This is expected behaviour — not a bug. Do not page anyone for the first 30 minutes after a post lands in a watched channel.

**Serialization and worst-case starvation**

`batch_worker` processes one batch at a time. While a batch is in-flight, every subsequent post waits to be scored — a slow-responding batch blocks the entire scoring pipeline for its full duration. This is **by design**: the architecture deliberately accepts near-real-time (minutes) latency in exchange for the 50% batch discount and per-user personalized scoring.

The starvation window is bounded by `BATCH_MAX_WAIT_SECONDS` (default 30 min). When that ceiling is reached:
- The in-flight batch is marked failed in `pending_batches`.
- `batch_worker` moves on immediately; the next flush proceeds.
- Posts already submitted in the failed batch are **lost** — no retry, no notification. This is consistent with the plan's "if batch fails, requests drop" rollback contract.

**Tunables** (non-sensitive — set as GitHub Variables or omit to use config.py defaults):

| Variable | Default | Effect |
|---|---|---|
| `BATCH_FLUSH_SIZE` | `50` | Flush immediately when this many per-user scoring requests are queued |
| `BATCH_FLUSH_SECONDS` | `600` | Flush after this many seconds even if size threshold not reached |
| `BATCH_POLL_INTERVAL_SECONDS` | `60` | How often the poller checks Anthropic for batch completion |
| `BATCH_MAX_WAIT_SECONDS` | `1800` | Max seconds to wait for a single batch before marking it failed and moving on |

**Observability** — watch the admin log bot for these lifecycle lines:
- `batch submitted batch_id=... size=...` — batch sent to Anthropic
- `batch complete batch_id=... duration_s=...` — Anthropic finished processing
- `batch verdicts stored n=...` — results written to DB, fan-out begins
- `batch exceeded max_wait_seconds batch_id=...` — batch timed out; affected posts are dropped

If you see `batch exceeded max_wait_seconds` warnings, the Anthropic batch queue is backed up. Mitigations in order:
1. Reduce `BATCH_FLUSH_SIZE` — fewer requests per batch means faster Anthropic processing.
2. Increase `BATCH_FLUSH_SECONDS` — less frequent flushes while the queue is slow.
3. Raise `BATCH_MAX_WAIT_SECONDS` temporarily if the backlog is known-transient.

**Rollback** — revert the batch-scoring commit; generic synchronous scoring resumes immediately on the next deploy. Orphan rows in `post_verdicts` left by the batch pipeline are harmless and do not affect bot operation.

---

## CI/CD (.github/workflows/deploy.yml)

Triggers on every push to `main`:
1. **Test job**: `uv sync --locked` → `pytest`
2. **Deploy job** (only on `main` push, after tests pass):
   - rsync source to server (excludes `.env`, `*.session`, `ahsoka.db`, `.venv`)
   - `uv sync --locked` on server
   - `systemctl restart ahsoka`

**Required GitHub secrets**:
| Secret | Value |
|--------|-------|
| `DEPLOY_HOST` | Hetzner server IP |
| `DEPLOY_SSH_KEY` | Private half of a deploy ed25519 key (public half in root's `authorized_keys`) |

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Claude API rate limits | `asyncio.Semaphore(5)` in scorer; exponential backoff on `RateLimitError` |
| Model returning JSON wrapped in markdown fences | Assistant-turn prefilling forces raw JSON; no fence stripping needed |
| Pyrogram push updates not delivered for quiet channels | 60s poller fallback via `get_chat_history()`; dedup prevents double-processing |
| JS-rendered job boards (LinkedIn, etc.) | Fallback to raw post text; no Playwright |
| Pyrogram session conflicts | Never run two instances; `.session` file is single-writer |
| `OWNER_CHAT_ID` misconfigured | Startup health check: send test message; exit on failure |
| `seen_posts` growing unboundedly | Periodic cleanup task deletes rows older than 30 days |
| Paused state + post flood on resume | Mark seen even when paused (stale jobs are rarely actionable) |

---

## Verification

1. **Unit tests**: `pytest tests/` — keyword filter, scraper fallback, scorer JSON parsing, DB dedup
2. **Integration smoke test**:
   - Set criteria via bot commands, check `/status`
   - Wait for a post in a watched channel
   - Confirm it appears in the bot chat with correct score/reason
3. **Keyword pre-filter validation**: post with no matching keywords should never reach Claude (visible in logs as `Keyword drop`)
4. **Pause/resume**: pause, wait for a post, resume, confirm post does NOT re-appear
5. **Runtime channel management**: `/addchannel` a new channel, confirm posts from it appear without restart; `/removechannel` it, confirm posts stop
6. **Crash recovery**: `kill -9 <pid>` → systemd restarts within 5s → "ahsoka started ✓" in Telegram
