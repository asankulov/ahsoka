# Repost Detection — Design

**Date:** 2026-11-05
**Branch:** `feat/repost-detection`
**Status:** Approved in conversation; awaiting written-spec review

## Goal

Stop the same vacancy from being scored and delivered more than once when it is reposted — in the same channel or cross-posted to other watched channels — including rewritten and Russian↔English variants.

Why it matters: **cost** (every repost is currently scored once per active user via the Batch API) and **user experience** (users receive the same vacancy multiple times).

Today `is_duplicate()` only catches the exact same `(channel_id, message_id)` pair.

## Scope

In scope: semantic repost detection using text embeddings, with a shadow-mode rollout.

Out of scope (separate sub-projects, each with its own spec later):
- B. Semantic pre-filter (embedding user profiles vs. posts) — would touch `KeywordIndex` tribal law.
- C. CV upload + retrieval into the scoring prompt.

The embedding client and vector storage built here are intended to be reused by B and C.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Similarity method | Embeddings (not SimHash/MinHash) | Repost style unknown (verbatim vs. rewritten); embeddings cover both |
| Provider | Voyage AI, `voyage-4-lite`, 512 dims, `input_type="document"` | Anthropic has no embeddings API; Voyage is its recommended partner; multilingual; cheapest tier |
| Client | Direct REST call via `httpx` (already a dependency) | No new package, async-native |
| Vector search | Pure-Python cosine over a 30-day window in SQLite | Volume is at most a few thousand vectors; no vector DB or numpy needed |
| Rollout | `shadow` by default → tune threshold from logs → `enforce` | Threshold is picked from real pairs, not guessed |
| Failure mode | Fail open | Repost detection must never block delivery |
| Template-collision guard | None in v1 | Decided from shadow data; add a targeted guard only if collisions appear |

## Architecture

### Placement in `pipeline_worker` (`ahsoka/main.py`)

```
is_duplicate → notify-target drop → empty drop → resume drop
  → short-post prefetch → KeywordIndex.passes
  → REPOST CHECK (new)
  → active configs → _enqueue_single (seen-claim → scrape → BatchQueue)
```

Runs **after** the keyword filter: only posts that would otherwise cost LLM budget are embedded. The 30-day vector window therefore contains only keyword-passing posts.

### New units

**`ahsoka/pipeline/embedder.py`**
- `async def embed(text: str) -> list[float] | None`
- POST `https://api.voyageai.com/v1/embeddings` with `model`, `input`, `input_type="document"`, `output_dimension`, bearer `VOYAGE_API_KEY`.
- Input normalization: collapse whitespace, strip, truncate to `REPOST_MAX_CHARS` (≈8000 chars).
- Returns `None` on: missing key, timeout, network error, non-2xx response, malformed body. Logs a WARNING on failure (no stack spam).
- Tracks `total_tokens` from responses in a module-level counter for daily cost logging.

**`ahsoka/pipeline/repost_detector.py`**
- `@dataclass RepostMatch(channel_id: int, message_id: int, similarity: float)`
- `async def find_nearest(conn, vector) -> RepostMatch | None`: loads vectors from `post_embeddings` created within the last 30 days, computes cosine similarity, and returns the single best match (or `None` if the table is empty).
- `async def check_and_store(conn, post, vector, threshold) -> RepostMatch | None`: under a module-level `asyncio.Lock`, calls `find_nearest`. If the best similarity is ≥ threshold, it returns the match **without storing**; otherwise it stores this post's vector and returns the best match (the caller uses its similarity for near-miss logging) or `None`.
  - The caller distinguishes "repost" from "near-miss" by comparing `similarity` to the threshold.
- Vector packing helpers: `pack(list[float]) -> bytes` (`array('f')`, float32) and `unpack(bytes) -> list[float]`.

**Database (`ahsoka/database.py`)**
```sql
CREATE TABLE IF NOT EXISTS post_embeddings (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    vector     BLOB    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_post_embeddings_created ON post_embeddings(created_at);
```
- Additive, idempotent migration via `CREATE TABLE IF NOT EXISTS` in `init_db()`.
- CRUD: `insert_post_embedding` (INSERT OR IGNORE) and `get_recent_post_embeddings(days=30)`.
- `delete_old_posts(days=30)` also prunes `post_embeddings` by `created_at`.
- `aiosqlite` only.

### What gets embedded

`post.text`, plus the prefetched link content when the short-post prefetch ran (`prefetched`). For long posts, only `post.text` is embedded; no extra scrape happens before the check.

### Settings (`ahsoka/config.py`)

| Field | Default | Source |
|---|---|---|
| `voyage_api_key: str` | `""` | GitHub **Secret** `VOYAGE_API_KEY`. Empty means the stage is fully off |
| `voyage_model: str` | `"voyage-4-lite"` | GitHub Variable (optional) |
| `repost_mode: Literal["off","shadow","enforce"]` | `"shadow"` | GitHub Variable `REPOST_MODE` |
| `repost_threshold: float` | `0.95` | GitHub Variable `REPOST_THRESHOLD` |
| `repost_near_miss_floor: float` | `0.85` | code default |
| `repost_dimensions: int` | `512` | code default |
| `repost_max_chars: int` | `8000` | code default |
| `embed_timeout_s: float` | `5.0` | code default |

Note: changing `voyage_model` or `repost_dimensions` makes existing vectors incomparable. The detector skips stored vectors whose length differs from the query vector, and the window refills within 30 days.

## Behaviour

Stage logic in `pipeline_worker`, after the keyword filter passes:

1. If `repost_mode == "off"` or `voyage_api_key` is empty, skip the stage.
2. `vector = await embed(text)`. If it is `None`, continue (fail open).
3. `match = await check_and_store(conn, post, vector, threshold)`.
4. If `match` and `match.similarity >= threshold`:
   - `shadow`: INFO `Repost candidate (shadow): <ch>/<msg> ≈ <orig ch>/<orig msg>, sim=0.971`. Continue to scoring.
   - `enforce`: INFO `Repost drop: <ch>/<msg> ≈ <orig ch>/<orig msg>, sim=0.971`; `db.mark_seen(...)`; `continue`.
5. Else, if `match` and `match.similarity >= repost_near_miss_floor`: INFO `Repost near-miss: … sim=0.91`. Continue.
6. Otherwise continue silently (DEBUG log of the best similarity).

INFO lines reach the owner through the existing `bot/log_handler.py`; no new logging path. Log lines reference posts as clickable `https://t.me/c/<id>/<msg>` links for `-100…` ids, otherwise raw `channel_id/message_id`.

**Storage rule:** a post's vector is stored only when it is *not* a repost (similarity < threshold). Repost chains therefore resolve to the original, and copies do not fill the window. This applies in shadow mode too, so shadow logs predict enforce behaviour exactly.

**Concurrency:** three `pipeline_worker` tasks run concurrently, and cross-posts often arrive within seconds of each other. `find_nearest` + insert run under one `asyncio.Lock`, so the second of two simultaneous copies always sees the first one's vector. The Voyage HTTP call stays outside the lock. A single process means an in-process lock is enough.

**Daily cost log:** `cleanup_worker` logs INFO `Voyage usage: N tokens in last 24h` from the embedder counter, then resets the counter.

## Known limits (accepted)

- In `enforce` mode, a user who registered between the original and the repost does not receive the repost.
- A user whose config changed since the original is not re-scored on the repost.
- If the original's batch failed, the repost is still skipped. This is the same category as the existing claimed-but-never-scored risk.
- Template collisions (one company posting two roles from the same template) may exceed the threshold. Mitigated by the high default threshold and shadow-mode review. A guard is added later only if the data shows collisions.
- The in-memory token counter resets on restart, so the daily cost log is approximate.

## Testing (cara-dune)

All offline; no real Voyage calls.

- `tests/test_embedder.py`: request shape (URL, model, dims, input_type, auth header) via a mocked httpx transport; `None` on timeout, 4xx, 5xx, malformed JSON, or missing key; normalization and truncation; token counter.
- `tests/test_repost_detector.py`: cosine correctness; best-match selection; threshold boundary (equal counts as a repost); 30-day window; dimension-mismatch skip; pack/unpack round-trip; store-only-if-not-repost; two concurrent `check_and_store` calls with identical vectors yield exactly one stored row and one detected repost.
- `tests/test_database.py`: `post_embeddings` migration is idempotent; insert and range query; `delete_old_posts` prunes embeddings.
- `tests/test_main.py`: `pipeline_worker` in each mode. `off` never calls embed; `shadow` logs and still enqueues; `enforce` drops, calls `mark_seen` and does not enqueue; embed failure fails open; near-miss logging; empty key disables the stage.
- Overall coverage ≥ 80%.

## Infra (kuiil)

- `.github/workflows/deploy.yml`: inject `VOYAGE_API_KEY` (Secret), and `REPOST_MODE`, `REPOST_THRESHOLD` and `VOYAGE_MODEL` (Variables) into `.env`, following the existing pattern (`grep -v '=$'` keeps `config.py` defaults).
- `.env.example`: document the new keys.
- No dependency change; `uv.lock` unchanged.

## Rollout

1. Merge. The default is `shadow`, but with no `VOYAGE_API_KEY` the stage is off, so production behaviour is unchanged.
2. Operator adds `VOYAGE_API_KEY` to GitHub Secrets and redeploys. Shadow mode starts. (Tracked in `project_pending.md`.)
3. Review about 1–2 weeks of `Repost candidate` and `Repost near-miss` lines in the log bot.
4. Set the `REPOST_THRESHOLD` Variable from the observed pairs, then set `REPOST_MODE=enforce` and redeploy. No code change needed.

## Ownership

| Slice | Owner |
|---|---|
| embedder, repost_detector, database migration/CRUD, config fields, pipeline_worker stage, cleanup_worker logging | the-armorer |
| tests | cara-dune |
| deploy.yml, .env.example | kuiil |
| diff review | ig-11 |
