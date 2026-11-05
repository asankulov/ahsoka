# Cost-management stats — design

Date: 2026-11-05 · Status: draft for review

## Goal
Admin-only cost visibility answering "what did we spend, when, and why did it change".

Success criteria:
- `/stats day|month|year` shows cost, tokens, batches and cost per scored request per bucket, split by component (scoring vs Voyage embeddings). A per-model split is a deferred follow-up.
- Dollars are frozen at record time; later price-table edits never rewrite history.
- Config/env changes and population changes appear as markers beside the cost.
- A daily digest is sent via the existing log bot (no second logging mechanism).
- Reconciliation against the Anthropic Admin API is optional, off by default (the user may not have an organization account).

## Current state (verified 2026-11-05)
- `batch_submitter` sums per-result `usage` and stores tokens per batch in `batch_usage`.
- Dollars are computed at display time in `cmd_stats` from hardcoded `_BATCH_PRICING` (`bot/commands.py`), all-time only; an unknown model makes the whole figure "est. unknown".
- Voyage tokens live in an in-memory counter (`embedder._tokens`), logged daily, reset on restart, not priced.
- The Batches API returns tokens only; dollars come from our price table.

## Design
### Pricing (`ahsoka/pipeline/costs.py`, new)
- Owns the price table (moved out of `commands.py`, Voyage added) and `cost_for(model, usage) -> float | None`.
- `batch_submitter` writes `cost_usd` plus the per-token rates used into `batch_usage` at record time. Unknown model: WARNING via log bot, `cost_usd` NULL, excluded from totals and flagged in output.
- One-time idempotent backfill of existing rows from current prices, flagged `estimated`.

### Voyage
- `embedder` persists tokens per day into `embedding_usage` (date, model, tokens, cost_usd) instead of only the in-memory counter.

### Change tracking
- `cost_events(ts, kind, key, old_value, new_value)` append-only.
- At startup, diff cost-relevant Settings (`claude_model`, `repost_mode`, `repost_threshold`, `batch_flush_size`, `batch_flush_seconds`, `voyage_model`, `repost_dimensions`, `default_score_threshold`) against the last stored snapshot; insert one event per difference.
- Daily job snapshots population counts (active users, watched channels, union keyword count) into `daily_snapshots`; records an event when a count moves by more than a set relative delta.

### Stats surface
- `/stats` keeps today's summary; `/stats day|month|year` shows the last N buckets (cost, tokens, batches, cost per scored post) with event markers under the matching bucket.

### Digest
- Daily task via the log bot: yesterday's cost, month-to-date, changes since last digest, reconciliation drift (when enabled).

### Reconciliation
- Optional Setting `anthropic_admin_key` (GitHub Secret); no-op when empty. Daily pull of the Admin API cost report, stored next to our estimate; drift reported in the digest.
- Endpoint availability and shape must be verified against the Anthropic docs before implementation; individual accounts may not have access.

### Error handling
- Cost recording, snapshotting and reconciliation fail open: log and continue, never block scoring or notifications.

### Schema (additive, idempotent in `init_db()`)
- `batch_usage`: add `cost_usd` (frozen at record time) and `estimated` (backfilled rows).
- New tables: `embedding_usage`, `cost_events`, `daily_snapshots`, `cost_reconciliation`.

## Out of scope
Pricing-table change events, git SHA/deploy markers, budget alerts, per-user cost attribution.

## Testing (cara-dune)
Pricing math and frozen cost, unknown-model handling, backfill idempotence, settings diff, bucket queries, `/stats` handler, digest formatting, reconciliation no-op and drift (mocked HTTP), fail-open paths.

## Ownership
the-armorer: costs.py, database, batch_submitter, embedder, commands, main workers, config fields. kuiil: `.env.example`, deploy.yml for the optional secret. cara-dune: tests. ig-11: review.
