# Cost-Management Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin-only daily/monthly/yearly cost stats with frozen dollar costs, change markers, a daily digest, and optional Admin API reconciliation.

**Architecture:** A new `ahsoka/pipeline/costs.py` owns pricing and cost math. `batch_submitter` and `embedder` write frozen dollars into SQLite (`batch_usage` extended, `embedding_usage` new). Settings diffs and population snapshots feed an append-only `cost_events` table. `/stats` and a daily digest (via the log bot) read bucketed queries; reconciliation is an optional daily Admin API pull.

**Tech Stack:** Python 3.12, aiosqlite, aiogram 3.26, httpx, pytest-asyncio (`uv run --extra dev pytest`).

**Spec:** `docs/superpowers/specs/2026-11-05-cost-management-stats-design.md`

## Global Constraints

- `aiosqlite` only; migrations additive and idempotent in `database.init_db()` (SQLite `ALTER TABLE ADD COLUMN` is not idempotent: guard with `PRAGMA table_info`).
- All timestamps/buckets are UTC (`YYYY-MM-DD` day, `YYYY-MM` month, `YYYY` year).
- Cost recording, snapshotting, digest and reconciliation fail open: log a warning, never block scoring or notifications.
- Log bot is the only observability path; the digest is a normal logger/`Bot(log_bot_token)` message, no new mechanism.
- Unknown model: WARNING, `cost_usd` NULL, excluded from totals and flagged in output.
- `anthropic_admin_key` is an optional Setting (default `""` = disabled); a GitHub Secret, never a Variable.
- Out of scope: pricing-table change events, deploy-SHA markers, budget alerts, per-user attribution.
- Commits: allowed prefixes only, no Co-Authored-By, explicit file staging.

## Review Focus

- Unknown/new model name in `batch_usage`: bucket output marks it unpriced, totals don't crash or show 0.
- First startup (no stored snapshot): records a baseline, emits no spurious `cost_events`.
- Restart with unchanged settings: no duplicate events.
- UTC day boundary: a batch recorded at 23:59:59Z and one at 00:00:00Z land in different buckets.
- `/stats day` on an empty DB: friendly "no data" reply, not a traceback.
- Reconciliation with missing key, HTTP error, or malformed body: no-op/warning, digest still sent.
- Backfill run twice: second run changes nothing.

---

## File Structure

- Create `ahsoka/pipeline/costs.py` — price table, `cost_for`, settings-diff helper, digest/bucket formatting helpers.
- Create `ahsoka/pipeline/reconcile.py` — Admin API pull (optional).
- Modify `ahsoka/database.py` — schema, migration, backfill, cost queries.
- Modify `ahsoka/pipeline/batch_submitter.py`, `ahsoka/pipeline/embedder.py`, `ahsoka/bot/commands.py`, `ahsoka/main.py`, `ahsoka/config.py`.
- Modify `.env.example`, `.github/workflows/deploy.yml` (kuiil).
- Tests: `tests/test_costs.py`, `tests/test_cost_database.py`, `tests/test_reconcile.py`, plus additions to `test_batch_submitter.py`, `test_embedder.py`, `test_commands.py`, `test_main.py`, `test_config.py`.

---

### Task 1: Pricing module

**Files:** Create `ahsoka/pipeline/costs.py`; Test `tests/test_costs.py`; Modify `ahsoka/bot/commands.py` (remove `_BATCH_PRICING`/`_pricing_for`, import from costs).

**Interfaces:**
- Produces: `PRICES: dict[str, tuple[float,float,float,float]]` (input, output, cache_write, cache_read per token — values moved verbatim from `commands.py`); `price_for(model: str) -> tuple[float,float,float,float] | None` (longest-prefix match); `cost_for(model: str, input_tokens: int, output_tokens: int, cache_write: int, cache_read: int) -> float | None`; `EMBED_PRICES: dict[str, float]` (USD/token, `voyage-4-lite`) and `embed_cost_for(model: str, tokens: int) -> float | None`.

- [ ] **Step 1:** Write tests: `test_cost_for_known_model` (haiku-5-5, 1M input + 1M output = 0.05 + 0.25 USD), `test_cost_for_unknown_returns_none`, `test_longest_prefix_wins` (sonnet-4-6 vs sonnet-4), `test_embed_cost_for_unknown_none`.
- [ ] **Step 2:** Run `uv run --extra dev pytest tests/test_costs.py -v`; expect FAIL (module missing).
- [ ] **Step 3:** Implement the functions above in `costs.py`; take the current Voyage `voyage-4-lite` per-token price from Voyage's published pricing and note the source in a comment. Update `commands.py` to import `price_for`; keep `cmd_stats` behaviour unchanged for now.
- [ ] **Step 4:** Run the new tests plus `tests/test_commands.py`; expect PASS.
- [ ] **Step 5:** Commit `feat: extract pricing into costs module`.

### Task 2: Schema, backfill and cost queries

**Files:** Modify `ahsoka/database.py`; Test `tests/test_cost_database.py`.

**Interfaces:**
- Consumes: `costs.cost_for`.
- Produces (all `async`, first arg `conn: aiosqlite.Connection`):
  - `save_batch_usage(conn, batch_id, model, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, succeeded, cost_usd: float | None = None)` — existing signature plus optional `cost_usd`.
  - `record_embedding_usage(conn, day: str, model: str, tokens: int, cost_usd: float | None) -> None` — upserts/accumulates per (day, model).
  - `add_cost_event(conn, kind: str, key: str, old_value: str | None, new_value: str | None) -> None`; `list_cost_events(conn, since: str, until: str) -> list[dict]` (keys `ts, kind, key, old_value, new_value`).
  - `get_setting_snapshot(conn) -> dict[str, str] | None` / `save_setting_snapshot(conn, values: dict[str, str]) -> None`.
  - `save_population_snapshot(conn, day: str, users: int, channels: int, keywords: int) -> None`; `get_last_population_snapshot(conn) -> dict | None`.
  - `get_cost_buckets(conn, granularity: Literal["day","month","year"], limit: int) -> list[dict]` — newest first; keys `bucket, scoring_usd, embedding_usd, input_tokens, output_tokens, cache_read_tokens, embedding_tokens, batches, succeeded, unpriced_batches`.
  - `save_reconciliation(conn, day: str, billed_usd: float, estimated_usd: float) -> None`; `get_reconciliation(conn, day: str) -> dict | None`.

- [ ] **Step 1:** Write tests: columns `cost_usd`, `estimated` exist after `init_db`; `init_db` twice is a no-op; `test_backfill_idempotent` (pre-existing rows get cost + `estimated=1` once, second run unchanged); `test_buckets_utc_boundary` (23:59:59Z vs 00:00:00Z rows land in different days); `test_unpriced_batch_counted_not_summed`; embedding upsert accumulates; events round-trip; snapshot round-trip.
- [ ] **Step 2:** Run the file; expect FAIL.
- [ ] **Step 3:** Add `ALTER TABLE batch_usage ADD COLUMN cost_usd REAL` and `estimated INTEGER NOT NULL DEFAULT 0` guarded by `PRAGMA table_info`; create tables `embedding_usage(day, model, tokens, cost_usd, PRIMARY KEY(day, model))`, `cost_events(id, ts, kind, key, old_value, new_value)`, `setting_snapshot(key PRIMARY KEY, value)`, `daily_snapshots(day PRIMARY KEY, users, channels, keywords)`, `cost_reconciliation(day PRIMARY KEY, billed_usd, estimated_usd)`. Backfill only rows where `cost_usd IS NULL`, using `cost_for`; rows for unknown models stay NULL. Bucket via `substr(recorded_at, 1, 10|7|4)`.
- [ ] **Step 4:** Run the file plus `tests/test_database.py`; expect PASS.
- [ ] **Step 5:** Commit `feat: add cost tracking schema and queries`.

### Task 3: Record frozen cost in batch_submitter

**Files:** Modify `ahsoka/pipeline/batch_submitter.py` (both `save_batch_usage` call sites, ~lines 171 and 234); Test `tests/test_batch_submitter.py`.

**Interfaces:** Consumes `costs.cost_for`, `db.save_batch_usage(..., cost_usd=)`.

- [ ] **Step 1:** Add tests: `test_poll_saves_cost_usd` (known model, assert stored `cost_usd` equals `cost_for` result), `test_recover_saves_cost_usd`, `test_unknown_model_saves_null_and_warns`, `test_cost_failure_does_not_break_results` (patch `cost_for` to raise; verdicts still returned).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Compute cost in a small private helper `_batch_cost(model, totals) -> float | None` wrapped in try/except (fail open); log WARNING when the model is unpriced; pass to both save calls.
- [ ] **Step 4:** Run `tests/test_batch_submitter.py`; expect PASS.
- [ ] **Step 5:** Commit `feat: freeze batch cost at record time`.

### Task 4: Persist Voyage usage

**Files:** Modify `ahsoka/pipeline/embedder.py`, `ahsoka/main.py` (cleanup_worker log line); Test `tests/test_embedder.py`.

**Interfaces:** Consumes `db.record_embedding_usage`, `costs.embed_cost_for`. Produces `embedder.set_usage_sink(sink: Callable[[int], Awaitable[None]] | None)` — optional async callback invoked with tokens after each successful `_post`; `main.py` wires it to `record_embedding_usage` with today's UTC date. Keep `take_token_count` for compatibility.

- [ ] **Step 1:** Tests: sink called with the response's `usage.total_tokens`; sink exception is swallowed (embedding still returned); no sink set = unchanged behaviour.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement the sink in `_post` after token accumulation (try/except, warning on failure); wire in `main()`.
- [ ] **Step 4:** Run `tests/test_embedder.py tests/test_main.py`; expect PASS.
- [ ] **Step 5:** Commit `feat: persist Voyage embedding usage per day`.

### Task 5: Change tracking

**Files:** Modify `ahsoka/pipeline/costs.py`, `ahsoka/main.py`; Test `tests/test_costs.py`, `tests/test_main.py`.

**Interfaces:**
- Produces in `costs.py`: `TRACKED_SETTINGS: tuple[str, ...]` = `claude_model, repost_mode, repost_threshold, batch_flush_size, batch_flush_seconds, voyage_model, repost_dimensions, default_score_threshold`; `async def record_setting_changes(conn, settings) -> int` (returns events written); `async def record_population(conn, day: str, users: int, channels: int, keywords: int) -> int`.
- Population event rule: relative change ≥ 20% **and** absolute change ≥ 2 versus the last snapshot, per metric, kind `population`.

- [ ] **Step 1:** Tests: first run saves snapshot and writes 0 events; unchanged second run writes 0; changed `claude_model` writes one event with old/new values; population move 10→13 (30%, abs 3) writes an event, 10→11 does not; first population snapshot writes no event.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; stringify values for storage and compare as strings. In `main()` call `record_setting_changes` after `init_db` (fail open); add a daily `population_worker(conn, keyword_index, watched_channels)` task gathering counts and calling `record_population`.
- [ ] **Step 4:** Run the two test files; expect PASS.
- [ ] **Step 5:** Commit `feat: track cost-relevant config and population changes`.

### Task 6: `/stats day|month|year`

**Files:** Modify `ahsoka/bot/commands.py` (`cmd_stats`), `ahsoka/pipeline/costs.py` (`format_buckets`); Test `tests/test_commands.py`, `tests/test_costs.py`.

**Interfaces:** Consumes `db.get_cost_buckets`, `db.list_cost_events`. Produces `format_buckets(buckets: list[dict], events: list[dict], granularity: str) -> str`.

- [ ] **Step 1:** Tests: `/stats` with no args keeps the existing lines (now using frozen costs); `/stats day` replies with per-day lines containing cost and cost-per-scored-post, event markers under the matching day; `/stats month`, `/stats year`; invalid arg replies with usage; empty DB replies "no data"; unpriced batches shown as "N unpriced".
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Parse the argument from the command text; limits: 14 days, 12 months, 5 years. Keep summary line using SUM of stored `cost_usd`. Use name-based handler lookup as in existing tests; reply stays under 4096 chars.
- [ ] **Step 4:** Run `tests/test_commands.py tests/test_costs.py`; expect PASS.
- [ ] **Step 5:** Commit `feat: add day/month/year cost stats`.

### Task 7: Daily digest

**Files:** Modify `ahsoka/pipeline/costs.py` (`build_digest`), `ahsoka/main.py` (`digest_worker`); Test `tests/test_costs.py`, `tests/test_main.py`.

**Interfaces:** Produces `async def build_digest(conn, today: str) -> str` (yesterday's cost, month-to-date, events since the last digest, reconciliation drift line if present) and `digest_worker(conn, log_bot: Bot | None, chat_id: int)` sending once per UTC day shortly after 00:05Z; stores last-sent date in `setting_snapshot` under key `digest_last_sent` to avoid resends on restart.

- [ ] **Step 1:** Tests: digest text for a seeded DB (yesterday cost, MTD, one event); no batches yesterday → "no activity" line, still sent; second call same day does not resend; send failure logged, no crash.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; when `log_bot` is None the worker only logs the digest at INFO.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: add daily cost digest`.

### Task 8: Optional Admin API reconciliation

**Files:** Create `ahsoka/pipeline/reconcile.py`; Modify `ahsoka/config.py` (`anthropic_admin_key: str = ""`), `ahsoka/main.py`; Test `tests/test_reconcile.py`, `tests/test_config.py`; infra via kuiil: `.env.example`, `.github/workflows/deploy.yml` (secret `ANTHROPIC_ADMIN_KEY`).

**Interfaces:** Produces `async def fetch_billed_usd(day: str, *, client: httpx.AsyncClient | None = None) -> float | None` and `async def reconcile_day(conn, day: str) -> None` (no-op when key empty; stores via `save_reconciliation`).

- [ ] **Step 1:** **Verify first:** read Anthropic's current Admin API docs for the cost report endpoint, auth header and response shape; record the findings in a comment at the top of `reconcile.py`. If unavailable for this account type, stop and report to the user.
- [ ] **Step 2:** Tests with mocked HTTP: no key → no HTTP call; success stores billed vs estimated; HTTP 4xx/5xx → warning, nothing stored; malformed body → None.
- [ ] **Step 3:** Run; expect FAIL.
- [ ] **Step 4:** Implement; run daily from `digest_worker` just before building the digest (yesterday's date). Drift line format: `billed $X vs est. $Y (Δ Z%)`.
- [ ] **Step 5:** Run tests; expect PASS. Commit `feat: add optional Admin API cost reconciliation`.

### Task 9: Full verification and review

- [ ] **Step 1:** `uv run --extra dev pytest --cov=ahsoka`; expect all pass (ignoring the known Pyrogram collection issue) and coverage ≥ 80%.
- [ ] **Step 2:** ig-11 reviews the diff with the original user request only.
- [ ] **Step 3:** Ratify memory proposals, update `project_pending.md` (create admin key, set `ANTHROPIC_ADMIN_KEY` secret), then push and open a PR.
