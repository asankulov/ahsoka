# ahsoka

A multi-user Telegram job-filter bot. A Pyrogram user client monitors public job-posting channels, scores each post **once** via Claude, and fans out matching notifications to each user based on their personal filters. API cost scales with post volume, not user count.

## How it works

```
Pyrogram user client
  └─ raw update handler + 60s poller
       │
       ▼  asyncio.Queue
  dedup            — already seen? drop
  keyword index    — union of all users' keywords; no match = skip Claude
  scraper          — httpx + trafilatura; fallback to raw post text
  scorer           — Claude → generic score 0–10 + reason + apply info
  fan-out          — per-user filter (keywords, threshold, paused)
  notifier         — sends to each matching user's DM or channel
```

Notifications include a link preview for easy bookmarking:

```
https://t.me/channel_name/123

⭐ 8/10 — Strong Python/FastAPI match, remote ok
📬 hr@company.com · @recruiter_handle

<original post text, truncated to ~800 chars>

— @channel_name · Apr 3
```

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram account (for the Pyrogram user client that monitors channels)
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
LOG_BOT_TOKEN=            # optional — separate bot for log forwarding
OWNER_CHAT_ID=            # admin's numeric user ID — get from @userinfobot
CHANNEL_IDS=              # comma-separated channel IDs (seed list)
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-4-5-20251001
DEFAULT_SCORE_THRESHOLD=7
SCRAPE_TIMEOUT_S=5.0
DB_PATH=ahsoka.db
```

## Bot commands

### User commands (visible in menu)

Any user can `/start` the bot and configure their own filters:

| Command | Example | Effect |
|---|---|---|
| `/start` | `/start` | Register and get started |
| `/setstack` | `/setstack python go` | Desired tech stack |
| `/setseniority` | `/setseniority senior` | Seniority level |
| `/setremote` | `/setremote remote` | Work mode |
| `/setlocation` | `/setlocation Amsterdam` | Location |
| `/setsalary` | `/setsalary 5000 10000` | Monthly salary range |
| `/setthreshold` | `/setthreshold 8` | Minimum score (0-10) |
| `/setkeywords` | `/setkeywords python backend` | Replace keyword list |
| `/addkeyword` | `/addkeyword fastapi` | Append keyword(s) |
| `/resetkeywords` | `/resetkeywords` | Clear all keywords |
| `/watch` | `/watch` + forward a message | Add a channel to the watchlist |
| `/notify` | `/notify` + forward a message | Set notification channel |
| `/notify dm` | `/notify dm` | Reset notifications to DM (default) |
| `/pause` | `/pause` | Pause notifications |
| `/resume` | `/resume` | Resume notifications |
| `/status` | `/status` | Show current settings |
| `/channels` | `/channels` | List watched channels |
| `/help` | `/help` | Show all commands |

### Admin commands (hidden from menu)

| Command | Example | Effect |
|---|---|---|
| `/removechannel` | `/removechannel -100123` | Remove a channel from watchlist |
| `/users` | `/users` | List registered users |
| `/ban` | `/ban 123456` | Ban a user |
| `/unban` | `/unban 123456` | Unban a user |
| `/stats` | `/stats` | Usage statistics |

## Storage

SQLite (`ahsoka.db`). Five tables:

- `users` — registered users with admin/ban status and notification target
- `user_config` — per-user filter settings (stack, keywords, threshold, etc.)
- `seen_posts` — dedup + global scores; rows older than 30 days are deleted automatically
- `watched_channels` — shared watchlist, seeded from `CHANNEL_IDS` on first run
- `user_notified` — tracks which posts were sent to which users

## Running tests

```bash
uv run --extra dev pytest
```

## Deployment

See [`PLAN.md`](PLAN.md) for the full Hetzner + systemd deployment guide.
