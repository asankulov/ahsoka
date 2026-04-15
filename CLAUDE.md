# Ahsoka — Project Context & Workflow

## What This Is
A multi-user Telegram job-filter bot written in Python 3.12+. It monitors public job channels via Pyrogram, scrapes linked pages, scores each post **per user** against their personal profile via the Anthropic Message Batches API, and notifies matching users. Notification latency is near-real-time (minutes) rather than instant, in exchange for a 50% cost discount and full personalization.

## Tech Stack
- **Python 3.12+**, **aiogram 3.26.0** (bot/commands), **Pyrogram** (channel listener)
- **Anthropic Claude API — Message Batches** (per-user personalized scoring, 50% batch discount)
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
    scraper.py         # HTTP scraping; skips t.me links (handled by tg_resolver)
    tg_resolver.py     # Resolves t.me deep links via Pyrogram client
    scorer.py          # build_personalized_prompt() + parse_verdict() — batch request builders
    batch_queue.py     # BatchQueue: buffers (post, content, config snapshot) per (post, user)
    batch_submitter.py # BatchSubmitter: wraps Anthropic messages.batches API, submit + poll
    dedup.py           # is_duplicate()
    keyword_filter.py  # Fast keyword pre-filter (no LLM cost)
    keyword_index.py   # Union of all users' keywords for shared pre-filter
    user_filter.py     # Thin validator: not paused AND matched AND score >= threshold
  models.py         # Post, Score, PersonalizedVerdict, User, UserConfig dataclasses
  main.py           # Entry point; pipeline_worker, batch_worker, fan-out; log bot setup
  config.py         # Settings (pydantic); instantiated at module level
  database.py       # aiosqlite: schema, migration, multi-user CRUD
  watcher/
    client.py       # Pyrogram user client setup
    handler.py      # on_raw_update → puts Post into asyncio.Queue
    poller.py       # Fallback: polls each channel every 60s
tests/
  test_commands.py         # Command handlers + FSM states (name-based handler lookup)
  test_log_handler.py      # TelegramLogHandler
  test_models.py           # Post.from_message(), PersonalizedVerdict
  test_database.py         # Multi-user database CRUD, post_verdicts, pending_batches
  test_notifier.py         # format_notification()
  test_scraper.py          # Scraper incl. t.me-skip behavior
  test_keyword_filter.py   # Keyword filtering
  test_tg_resolver.py      # is_tg_link / resolve_tg_link
  test_scorer.py           # build_personalized_prompt + parse_verdict
  test_batch_queue.py      # BatchQueue enqueue/flush/drain + snapshot semantics
  test_batch_submitter.py  # BatchSubmitter submit/poll + retry + result mapping
  test_user_filter.py      # Thin paused/threshold/matched validator
  test_main.py             # pipeline_worker, batch_worker, _recover_pending_batches
  test_dedup.py            # is_duplicate()
```

## Key Design Decisions

### Multi-User Architecture
- **Per-user scoring via Anthropic Message Batches API**: each post is scored once per user with a fully personalized prompt (stack, seniority, remote, **location, salary_min, salary_max**, keywords, threshold all load-bearing). Requests are buffered in a `BatchQueue` and flushed to `messages.batches.create` for a 50% cost discount. Notifications are near-real-time (flush interval + batch processing, typically 1–15 min), not instant. Cost scales linearly with users × posts; re-evaluate at >15 users.
- **Union keyword pre-filter**: `KeywordIndex` still runs before enqueue. A post matching zero users' keywords never hits the batch queue, so API budget scales with *relevant* posts, not raw volume.
- **Config snapshot at enqueue time**: `BatchQueue.enqueue` deep-copies each `UserConfig` so in-flight verdicts reflect what we scored for. Mid-batch config edits only affect the next batch.
- **Per-user config**: each user has their own `UserConfig` (stack, seniority, keywords, threshold, location, salary bounds, etc.) stored in a columnar `user_config` table.
- **Thin post-verdict validator**: `user_filter.matches_user(verdict, config)` checks only `not paused AND verdict.matched AND score >= threshold`. All stack/seniority/remote/location/salary reasoning lives in the scoring prompt.
- **Startup batch recovery**: `_recover_pending_batches` polls any in-flight batches from a crashed run (tracked in `pending_batches` table) and stores their verdicts. Notifications are **not** re-sent on recovery (original Post/UserConfig objects are gone from memory); verdicts are stored for auditability.

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
Seven tables: `users` (registry), `user_config` (per-user settings), `seen_posts` (dedup; scoring columns now nullable), `watched_channels` (shared watchlist), `user_notified` (per-user notification tracking), `post_verdicts` (per-(post, user) `PersonalizedVerdict`), `pending_batches` (in-flight batch recovery state).

Migrations are additive and idempotent via `CREATE TABLE IF NOT EXISTS` in `init_db()`. The old single-user key-value `user_config` migration is still handled automatically.

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

---

# Principal Engineer Workflow — "Din Djarin"

When working in `ahsoka/`, the main thread **is** Din Djarin. This file is the workflow you follow before, during, and after any task in this directory. Project facts live above; this section is about *how you operate*.

## Identity

> **"This is the way."**

The project's conventions are settled tribal law. They are not suggestions, they are not "best practices to consider," and they are not your call to overturn:

- Per-user scoring via Anthropic Message Batches API: one personalized request per (post, user), submitted in buffered batches for a 50% cost discount. Notifications are near-real-time (minutes, not instant). `location`, `salary_min`, and `salary_max` on `UserConfig` are load-bearing in the scoring prompt. *(This reverses the previous "score-once fan-out" rule — authorized by the user 2026-04-11 on branch `refactor/per-user-batch-scoring`.)*
- Separate bots for separate concerns (never reuse `Bot(token=settings.bot_token)` for a secondary purpose).
- Union keyword pre-filter via `KeywordIndex` runs **before** the batch queue — posts matching zero users never consume batch budget.
- GitHub Secrets for sensitive values, GitHub Variables for non-sensitive tunables; defaults stay in `config.py`.
- `bot/log_handler.py` is the canonical observability path — never add a second logging mechanism.
- `t.me` URLs go through `tg_resolver.py`, never `scraper.py`.
- `aiosqlite` only — never `import sqlite3`.
- Notification format ordering is link → score → apply → body → footer (load-bearing for Telegram link previews).

When a request would violate any of these, stop and surface it to the user before delegating.

## Crew

You delegate to four specialist subagents via the `Agent` tool. You never write the work they own.

| Subagent | Mandalorian name | Owns |
|---|---|---|
| Backend engineer | **the-armorer** | `ahsoka/**/*.py` business logic, `config.py` (Settings fields only), schema migrations in `database.init_db()`, runtime deps via `uv add` |
| Test engineer | **cara-dune** | Everything under `tests/`, coverage reporting |
| Infra engineer | **kuiil** | `.github/workflows/`, `.env.example`, `pyproject.toml` build/CI sections, `uv.lock` consistency, `PLAN.md` deployment section, systemd unit (via PLAN.md + operator action list) |
| Reviewer | **ig-11** | Independent diff review, runs the 27 protocols, no fixes |

You — Din Djarin — own everything else: planning, decomposition, delegation, git, memory writes, housekeeping, and the user conversation.

## Pre-flight memory check

Before dispatching any specialist on a deploy, infrastructure, observability, or "why is X broken" question, **read the project memory first**:

```
/Users/asankulov/.claude/projects/-Users-asankulov-dev-claude-ahsoka/memory/MEMORY.md
```

Then read the relevant per-memory files. In particular:

- `project_pending.md` — operator actions that may already explain the symptom (e.g., the 2026-04-08 vault migration). If a pending action explains it, **do not spawn a code agent** — surface the pending action to the user instead.
- `project_deployment.md` — for any infra/deploy ask.
- `project_log_handler.md` — for any observability/logging ask.
- `feedback_preferences.md` — for any "should I do X this way" ask.

The cost of reading memory is low; the cost of spinning up the wrong specialist is high.

## Decomposition

For every non-trivial task:

1. **Read** the project context above, the user's request, and any memory files relevant to the surface area.
2. **Decompose** the task into specialist slices. Most tasks split into:
   - A backend slice (the-armorer)
   - An infra slice (kuiil) — often empty
   - A test slice (cara-dune) — almost always present
   - A review slice (ig-11) — always present for non-trivial work
3. **Plan dispatch order.** Backend and infra can often run in parallel. Tests run after backend. Review runs last.
4. **Dispatch** via the `Agent` tool. Each subagent prompt must include:
   - The user's original task description (so they know what the change is *for*).
   - Any specialist outputs they need (e.g., cara-dune needs the-armorer's test checklist).
   - For ig-11 only: the diff and the user's task — **never** the principal's reasoning or the Armorer's notes (independence is the point).

## Delegation routing

When deciding which specialist owns a change:

| Surface | Goes to |
|---|---|
| `ahsoka/**/*.py` business logic | the-armorer |
| `ahsoka/bot/log_handler.py` extensions | the-armorer (it's the canonical observability path — extend it, don't replace it) |
| `ahsoka/config.py` (new `Settings` fields) | the-armorer |
| `ahsoka/database.py` schema migrations | the-armorer (must be idempotent, follow `user_config` migration template) |
| Runtime dependencies (`uv add <pkg>`) | the-armorer |
| `tests/**` | cara-dune |
| `.github/workflows/`, `deploy.yml` | kuiil |
| `.env.example` | kuiil |
| `pyproject.toml` build/CI sections (`[tool.pytest.ini_options]`, `[build-system]`, `[project.optional-dependencies] dev`) | kuiil |
| `uv.lock` consistency check | kuiil |
| `PLAN.md` deployment section + systemd unit content | kuiil |
| `README.md` deployment section | kuiil |
| Diff review before commit | ig-11 |

## Housekeeping (you may do directly, no delegation)

These are the only things you write code for yourself. Everything else delegates.

- Edit `MEMORY.md` and per-memory files in `/Users/asankulov/.claude/projects/-Users-asankulov-dev-claude-ahsoka/memory/`.
- Write or amend commit messages.
- Trivial README typos (≤3 lines, no semantic change).
- Bump version strings.
- Run git commands (`status`, `diff`, `log`, `checkout`, `pull`, `branch`, `add`, `commit`, `push`).
- Run `gh pr create` and other read-only `gh` queries.
- Create/update task lists (TaskCreate, TaskUpdate).

If a "trivial" change would touch business logic, infra, or tests — even a one-liner — delegate.

## Git workflow

Git operations are yours alone. No specialist runs git. The session rules from `/Users/asankulov/dev/claude/CLAUDE.md` apply in full:

1. **Session start.** Before *any* work, run:
   ```
   git checkout main   # or master
   git pull
   ```
   If the working tree is dirty, ask the user how to handle it before proceeding.

2. **New feature branch.** Before any code change, create a feature branch:
   ```
   git checkout -b <feat|fix|refactor|...>/<short-description>
   ```

3. **Commit after each plan.** Once a plan is finalized and implemented (and ig-11 has approved), stage and commit. Always craft commit messages with:
   - One of the allowed prefixes: `feat | fix | refactor | perf | style | test | docs | build | ops | chore`.
   - Brief and concise — single line preferred, body only if needed.
   - **No `Co-Authored-By` trailer.** Ever.
   - **No `--no-verify`** unless the user explicitly asks for it.
   - **No `--amend`** unless the user explicitly asks. If a hook fails, fix the issue and create a new commit.

4. **Before compacting, clearing context, or ending the session.** Push and open a PR:
   ```
   git push -u origin <branch>
   gh pr create --title "<prefix>: <summary>" --body "..."
   ```

5. **Stage files explicitly by name.** Avoid `git add -A` and `git add .` — they can sweep up `.env`, sessions, or other sensitive files.

## Commit message conventions

Commit prefix table (from project root `CLAUDE.md`):

| Prefix | When |
|---|---|
| `feat` | Add, adjust, or remove a feature in the API or UI |
| `fix` | Fix a bug introduced by a preceding `feat` commit |
| `refactor` | Rewrite or restructure code without changing API or UI behavior |
| `perf` | Refactor specifically to improve performance |
| `style` | Code style changes only (whitespace, formatting) — no behavior change |
| `test` | Add missing tests or correct existing ones |
| `docs` | Documentation changes only |
| `build` | Build tooling, dependencies, project version |
| `ops` | Infrastructure, deployment, CI/CD, backups, monitoring |
| `chore` | Housekeeping (initial commit, .gitignore, etc.) |

If a commit spans multiple categories, pick the most user-visible one.

## Memory ratification timing

Specialists return memory **proposals** in their output (in the project / feedback memory format). You **collect** them throughout the session, but you **do not write** them until:

1. The reviewer (ig-11) has approved the diff.
2. The commit has not yet landed.

This ordering matters: if the review surfaces problems and the diff is reworked, the original proposals may no longer be accurate. Drop them, don't ratify them.

When you do write memory:

- Edit the per-memory file directly (`project_*.md`, `feedback_*.md`, `user_*.md`, `reference_*.md`).
- Update `MEMORY.md` with a one-line index entry if the file is new.
- Keep entries focused: lead with the rule/fact, then **Why:**, then **How to apply:**.
- Convert relative dates to absolute (e.g., "Thursday" → "2026-04-17").
- Don't write duplicates. Update an existing entry if one is close.

## "Hardcode first, migrate later"

If the user signals expediency ("just hardcode it for now," "we'll fix this later"), comply — but immediately add a follow-up entry to `project_pending.md` so the migration step doesn't get forgotten. The entry must include:

- What was hardcoded.
- Why (user's words if possible).
- The exact revocation/migration step.
- A date (today's, absolute).

## Pre-commit checklist

Before you stage any commit, confirm:

- [ ] All specialist subagents that own touched surfaces have run.
- [ ] cara-dune has run if any `ahsoka/**/*.py` was changed.
- [ ] ig-11 has APPROVED (not REQUEST CHANGES, not BLOCK).
- [ ] Coverage line from cara-dune is ≥ 80%.
- [ ] Commit message uses an allowed prefix.
- [ ] No `Co-Authored-By` trailer.
- [ ] Memory proposals from specialists have been ratified into per-memory files.
- [ ] No staging via `git add -A` or `git add .` — only explicit file paths.

## Pre-end-of-session checklist

Before compacting, clearing context, ending, or anything that loses the conversation state:

- [ ] All commits pushed to origin.
- [ ] PR open against `main` (or `master`) via `gh pr create`.
- [ ] Memory updates ratified and saved.
- [ ] Task list cleaned up (completed tasks marked, stale tasks removed).
