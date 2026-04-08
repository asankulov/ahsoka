# ahsoka

A personal Telegram job-filter bot. A Pyrogram user client monitors job-posting channels, scores each post for relevance via Claude Haiku, and forwards only high-scoring ones to you through an aiogram bot. All criteria are configurable via bot commands — no config files to edit.

## How it works

```
Pyrogram user client
  └─ raw update handler + 60s poller
       │
       ▼  asyncio.Queue
  dedup          — already seen? drop
  keyword filter — fast pre-filter, no LLM cost
  scraper        — httpx + trafilatura; fallback to raw post text
  scorer         — Claude Haiku → score 0–10 + reason + apply info
  notifier       — sends approved posts to your Telegram
```

Posts below the score threshold are silently dropped. Everything above it lands in your Telegram:

```
⭐ 8/10 — Strong Python/FastAPI match, remote ok
📬 hr@company.com · @recruiter_handle

<original post text, truncated to ~800 chars>

— @channel_name · Apr 3
```

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram account (for the Pyrogram user client)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

```bash
git clone https://github.com/asankulov/ahsoka
cd ahsoka
uv sync
cp .env.example .env
# fill in .env
uv run python -m ahsoka.main
```

On first run Pyrogram will prompt for your phone number and a login code to create the session file.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```dotenv
TELEGRAM_API_ID=          # from https://my.telegram.org
TELEGRAM_API_HASH=        # from https://my.telegram.org
SESSION_NAME=ahsoka_user
BOT_TOKEN=                # from @BotFather
LOG_BOT_TOKEN=            # optional — separate bot for log forwarding (from @BotFather)
OWNER_CHAT_ID=            # your numeric user ID — get from @userinfobot
CHANNEL_IDS=              # comma-separated channel IDs, e.g. -1001234567890,-1009876543210
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-4-5-20251001
DEFAULT_SCORE_THRESHOLD=7
SCRAPE_TIMEOUT_S=5.0
DB_PATH=ahsoka.db
```

## Bot commands

| Command | Example | Effect |
|---|---|---|
| `/setstack` | `/setstack python go` | Desired tech stack |
| `/setseniority` | `/setseniority senior` | Seniority level |
| `/setremote` | `/setremote remote` | Work mode |
| `/setlocation` | `/setlocation Amsterdam` | Location |
| `/setsalary` | `/setsalary 5000 10000` | Monthly salary range |
| `/setthreshold` | `/setthreshold 8` | Minimum score (0–10) |
| `/setkeywords` | `/setkeywords python backend` | Replace the entire keyword list (≥1 keyword required) |
| `/addkeyword` | `/addkeyword fastapi` | Append keyword(s); duplicates ignored |
| `/resetkeywords` | `/resetkeywords` | Clear all keywords; all posts will pass the filter |
| `/addchannel` | `/addchannel -1001234567890` | Add a channel to the watch list |
| `/removechannel` | `/removechannel -1001234567890` | Remove a channel |
| `/channels` | `/channels` | List watched channels |
| `/status` | `/status` | Show current criteria |
| `/pause` | `/pause` | Stop forwarding (still marks seen) |
| `/resume` | `/resume` | Resume forwarding |

## Storage

SQLite (`ahsoka.db`). Three tables:

- `seen_posts` — dedup + scores; rows older than 30 days are deleted automatically
- `watched_channels` — seeded from `CHANNEL_IDS` on first run, then managed via bot commands
- `user_config` — all criteria set via bot commands

## Running tests

```bash
uv run --extra dev pytest
```

## Deployment

See [`PLAN.md`](PLAN.md) for the full Hetzner + systemd deployment guide.
