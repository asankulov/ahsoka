# Repost Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project override (ahsoka/CLAUDE.md):** work is split by owner. **the-armorer** implements Tasks 1–4 (`ahsoka/**`), **cara-dune** writes every test listed below (`tests/**`) after each backend task, **kuiil** does Task 5, and **ig-11** reviews the whole diff. Only Din Djarin (the principal) runs git; specialists don't commit. The "Tests" blocks are cara-dune's specification. the-armorer uses them as acceptance criteria.

**Goal:** Detect reposted vacancies with Voyage embeddings and cosine similarity over a 30-day window, logging them (shadow) or dropping them before scoring (enforce).

**Architecture:** A new stage in `pipeline_worker`, after `KeywordIndex.passes`, embeds the post via the Voyage REST API (httpx), then searches and stores its vector in a SQLite `post_embeddings` table under one `asyncio.Lock`. The mode is set by configuration; the stage fails open.

**Tech Stack:** Python 3.12, aiosqlite, httpx, pydantic-settings, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-05-repost-detection-design.md`

## Global Constraints

- No new dependencies: Voyage is called via `httpx`, vectors are packed with stdlib `array('f')`, cosine in pure Python.
- `aiosqlite` only; never `import sqlite3`.
- Migration is additive and idempotent (`CREATE TABLE IF NOT EXISTS` inside `_SCHEMA`).
- All logging goes through the stdlib `logging` module (reaches the owner via `bot/log_handler.py`). No new logging path.
- Defaults: `voyage_model="voyage-4-lite"`, `repost_dimensions=512`, `repost_mode="shadow"`, `repost_threshold=0.95`, `repost_near_miss_floor=0.85`, `repost_max_chars=8000`, `embed_timeout_s=5.0`, `voyage_api_key=""`.
- Endpoint: `POST https://api.voyageai.com/v1/embeddings`, body `{"model", "input": [text], "input_type": "document", "output_dimension"}`, header `Authorization: Bearer <key>`. Response: `data[0].embedding`, `usage.total_tokens`.
- Similarity `>=` threshold counts as a repost.
- Run tests with `uv run --python 3.12 --extra dev pytest`.

## Review Focus

1. **Empty or whitespace-only embed input** (e.g. a link-only post with a failed prefetch): `embed()` must return `None` without an HTTP call, and the post continues. Test in Task 2.
2. **Zero vector or mismatched dimensions in the DB** (after a model or dimension change): the cosine must not divide by zero, and mismatched rows are skipped. Test in Task 3.
3. **The same post re-delivered by handler + poller** (same channel_id/message_id): it must not be flagged as a repost of *itself*. `find_nearest` excludes rows with the same `(channel_id, message_id)`. Test in Task 3.
4. **Voyage returns 429 / 5xx under burst cross-posting:** fail open, one WARNING, no retry storm. Test in Task 2.
5. **Group chats (ids without the `-100` prefix)** in log lines: the post-reference helper falls back to `channel_id/message_id` instead of building a broken `t.me/c/` link. Test in Task 4.

---

### Task 1: `post_embeddings` table, CRUD, cleanup

**Files:**
- Modify: `ahsoka/database.py` (`_SCHEMA`, new CRUD near the cleanup section, `delete_old_posts`)
- Test: `tests/test_database.py`

**Interfaces:**
- Produces:
  - `async def insert_post_embedding(conn, channel_id: int, message_id: int, vector: bytes) -> None` (INSERT OR IGNORE, commits)
  - `async def get_recent_post_embeddings(conn, days: int = 30) -> list[tuple[int, int, bytes]]` returns `(channel_id, message_id, vector)` with `created_at >= datetime('now', '-{days} days')`
  - `delete_old_posts(conn, days)` also deletes `post_embeddings` older than `days` (return value unchanged: seen_posts rowcount)

- [ ] **Step 1: Tests (cara-dune)**
  - `test_post_embeddings_table_created_idempotently`: `init_db` twice, table exists.
  - `test_insert_and_get_recent_post_embeddings`: insert `(-100123, 5, b"\x00"*8)`; get returns `[(-100123, 5, b"\x00"*8)]`.
  - `test_insert_post_embedding_ignores_duplicate`: insert the same key twice with different bytes; one row, first bytes kept.
  - `test_get_recent_excludes_older_than_window`: row with `created_at = datetime('now','-31 days')` (manual UPDATE) is not returned.
  - `test_delete_old_posts_prunes_embeddings`: old row deleted, fresh row kept.
- [ ] **Step 2: Implement** schema (exact DDL from the spec, including `idx_post_embeddings_created`), the two CRUD functions, and the extra DELETE in `delete_old_posts`.
- [ ] **Step 3: Run** `uv run --python 3.12 --extra dev pytest tests/test_database.py -q`. Expected: all pass.

### Task 2: Voyage embedder

**Files:**
- Modify: `ahsoka/config.py` (add `voyage_api_key: str = ""`, `voyage_model: str = "voyage-4-lite"`, `repost_dimensions: int = 512`, `repost_max_chars: int = 8000`, `embed_timeout_s: float = 5.0`)
- Create: `ahsoka/pipeline/embedder.py`
- Test: `tests/test_embedder.py`

**Interfaces:**
- Produces:
  - `def normalize(text: str, max_chars: int) -> str`: collapses all whitespace runs to a single space, strips, truncates to `max_chars`.
  - `async def embed(text: str, *, client: httpx.AsyncClient | None = None) -> list[float] | None`: reads `settings` at call time. Returns `None` (no request) if the key is empty or the normalized text is empty. Returns `None` plus one `logger.warning` on `httpx.HTTPError`, non-2xx, or a missing `data[0].embedding`. `client` is an injection point for tests; when omitted, a short-lived `httpx.AsyncClient(timeout=settings.embed_timeout_s)` is used.
  - `def take_token_count() -> int`: returns tokens accumulated from `usage.total_tokens` since the last call and resets to 0.

- [ ] **Step 1: Tests (cara-dune)**, using `httpx.MockTransport` and `patch("ahsoka.pipeline.embedder.settings")`:
  - `test_embed_sends_expected_request`: URL is `https://api.voyageai.com/v1/embeddings`; JSON is `{"model": "voyage-4-lite", "input": ["hello world"], "input_type": "document", "output_dimension": 512}`; the header `Authorization` is `Bearer k`; returns `[0.1, 0.2]` from `{"data":[{"embedding":[0.1,0.2]}],"usage":{"total_tokens":7}}`.
  - `test_embed_normalizes_and_truncates`: input `"a\n\n  b" + "x"*9000`, so the sent input starts with `"a b"` and has length 8000.
  - `test_embed_returns_none_without_key`: key `""`, so the transport is never called.
  - `test_embed_returns_none_on_empty_text`: `"   \n"`, so the transport is never called. *(Review Focus 1)*
  - `test_embed_returns_none_on_http_error` (parametrize 429, 500): `None`, exactly one WARNING record. *(Review Focus 4)*
  - `test_embed_returns_none_on_timeout`: transport raises `httpx.ReadTimeout`, so `None`.
  - `test_embed_returns_none_on_malformed_body`: `{"data": []}`, so `None`.
  - `test_take_token_count_accumulates_and_resets`: two successful calls with 7 and 3 tokens, so `take_token_count() == 10`, then `== 0`.
- [ ] **Step 2: Implement** the config fields and `embedder.py` per Interfaces.
- [ ] **Step 3: Run** `uv run --python 3.12 --extra dev pytest tests/test_embedder.py -q`. Expected: all pass.

### Task 3: Repost detector

**Files:**
- Create: `ahsoka/pipeline/repost_detector.py`
- Test: `tests/test_repost_detector.py`

**Interfaces:**
- Consumes: Task 1 `insert_post_embedding`, `get_recent_post_embeddings`.
- Produces:
  - `@dataclass(frozen=True) class RepostMatch: channel_id: int; message_id: int; similarity: float`
  - `def pack(vector: list[float]) -> bytes` / `def unpack(blob: bytes) -> list[float]` (float32 via `array('f')`)
  - `def cosine(a: list[float], b: list[float]) -> float`: returns `0.0` if either norm is 0.
  - `async def find_nearest(conn, channel_id: int, message_id: int, vector: list[float]) -> RepostMatch | None`: best cosine over recent rows, skipping rows whose length differs from `len(vector)` and the row with the same `(channel_id, message_id)`.
  - `async def check_and_store(conn, channel_id: int, message_id: int, vector: list[float], threshold: float) -> RepostMatch | None`: under module-level `_lock = asyncio.Lock()`, runs `find_nearest`. If `match and match.similarity >= threshold`, returns the match and **does not** store. Otherwise it stores `pack(vector)` and returns the match (possibly `None`).

- [ ] **Step 1: Tests (cara-dune)**, real in-memory DB via `init_db`:
  - `test_pack_unpack_roundtrip`: `[0.5, -1.0, 0.25]` round-trips (float32 values exactly representable).
  - `test_cosine_identical_orthogonal_opposite`: `1.0`, `0.0`, `-1.0` (`pytest.approx`).
  - `test_cosine_zero_vector_returns_zero`. *(Review Focus 2)*
  - `test_find_nearest_picks_best_match`: stored `[1,0]` @ (1,1) and `[0.9,0.1]` @ (1,2); query `[0.9,0.1]` from (2,9), so the match is (1,2), similarity ≈ 1.0.
  - `test_find_nearest_skips_dimension_mismatch`: stored a 3-dim vector, query 2-dim, so `None`. *(Review Focus 2)*
  - `test_find_nearest_excludes_self`: stored `[1,0]` @ (1,1); query the same vector from (1,1), so `None`. *(Review Focus 3)*
  - `test_check_and_store_stores_when_below_threshold`: empty DB, so it returns `None` and one row is stored.
  - `test_check_and_store_threshold_is_inclusive`: stored `[1,0]`; query `[1,0]` from another post with threshold `1.0`, so the match is returned and the row count stays 1.
  - `test_check_and_store_concurrent_copies`: `asyncio.gather` two `check_and_store` calls with identical vectors from different posts, threshold 0.95, so exactly one row is stored and exactly one call returns a match ≥ 0.95.
- [ ] **Step 2: Implement** `repost_detector.py` per Interfaces.
- [ ] **Step 3: Run** `uv run --python 3.12 --extra dev pytest tests/test_repost_detector.py -q`. Expected: all pass.

### Task 4: Pipeline stage and daily usage log

**Files:**
- Modify: `ahsoka/config.py` (add `repost_mode: Literal["off", "shadow", "enforce"] = "shadow"`, `repost_threshold: float = 0.95`, `repost_near_miss_floor: float = 0.85`)
- Modify: `ahsoka/main.py` (`pipeline_worker` after the keyword-drop block at line ~176; new helpers; `cleanup_worker`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: Task 2 `embed`, `take_token_count`; Task 3 `check_and_store`, `RepostMatch`.
- Produces (in `main.py`):
  - `def _post_ref(channel_id: int, message_id: int) -> str`: for ids starting with `-100`, `f"https://t.me/c/{str(channel_id)[4:]}/{message_id}"`; otherwise `f"{channel_id}/{message_id}"`.
  - `async def _repost_check(conn, post: Post, prefetched: str | None) -> bool`: returns `True` if the post should be **dropped**. Logic is exactly the spec's "Behaviour" steps 1–6. The embed text is `post.text + "\n" + prefetched` when prefetched, else `post.text`. Log messages:
    - `"Repost candidate (shadow): %s ≈ %s, sim=%.3f"`
    - `"Repost drop: %s ≈ %s, sim=%.3f"`
    - `"Repost near-miss: %s ≈ %s, sim=%.3f"`
    - Arguments are `_post_ref(new)`, `_post_ref(orig)`, similarity.
  - In `pipeline_worker`: `if await _repost_check(conn, post, prefetched): await db.mark_seen(...); continue`.
  - In `cleanup_worker`, after the existing log: `logger.info("Voyage usage: %d tokens in last 24h", take_token_count())`.
- Spec note: log lines use `t.me/c` links (clickable for the owner) instead of DB channel names. This refines the spec's "channel names where available".

- [ ] **Step 1: Tests (cara-dune)**, patching `ahsoka.main.embed`, `ahsoka.main.check_and_store` and `ahsoka.main.settings` (key `"k"`, threshold 0.95, floor 0.85) and following the existing `pipeline_worker` test pattern at `tests/test_main.py:343`:
  - `test_repost_off_mode_skips_embed`: `repost_mode="off"`, so `embed` is not awaited and the post is enqueued.
  - `test_repost_disabled_without_key`: key `""`, so `embed` is not awaited.
  - `test_repost_shadow_logs_and_enqueues`: match sim 0.97, so the INFO record contains `"Repost candidate (shadow)"` and `batch_queue.enqueue` is awaited.
  - `test_repost_enforce_drops`: match sim 0.97, so `mark_seen` is awaited, `enqueue` is not, and the record contains `"Repost drop"`.
  - `test_repost_near_miss_logged`: sim 0.90, so the record contains `"Repost near-miss"` and the post is enqueued (both modes).
  - `test_repost_embed_failure_fails_open`: `embed` returns `None`, so `check_and_store` is not awaited and the post is enqueued.
  - `test_repost_embeds_prefetched_content`: short post with a URL and `_fetch_content` patched to `"LINKED"`, so the `embed` argument contains both `post.text` and `"LINKED"`.
  - `test_post_ref_formats` (parametrize): `(-1001458440404, 7) → "https://t.me/c/1458440404/7"`; `(-4123, 7) → "-4123/7"`. *(Review Focus 5)*
  - `test_cleanup_worker_logs_voyage_usage`: patch `asyncio.sleep` to run once then raise `CancelledError`, and `take_token_count` to return 42, so the record contains `"Voyage usage: 42 tokens"`.
- [ ] **Step 2: Implement** the config fields, `_post_ref`, `_repost_check`, the `pipeline_worker` hook, and the `cleanup_worker` log line.
- [ ] **Step 3: Run the full suite** `uv run --python 3.12 --extra dev pytest --cov=ahsoka -q`. Expected: all pass, total coverage ≥ 80%.

### Task 5: Deploy wiring (kuiil)

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Modify: `.env.example`

- [ ] **Step 1:** In `deploy.yml`, write `VOYAGE_API_KEY=${{ secrets.VOYAGE_API_KEY }}`, `REPOST_MODE=${{ vars.REPOST_MODE }}`, `REPOST_THRESHOLD=${{ vars.REPOST_THRESHOLD }}` and `VOYAGE_MODEL=${{ vars.VOYAGE_MODEL }}` into `.env`, following the existing pattern so the `grep -v '=$'` stripping keeps `config.py` defaults when unset.
- [ ] **Step 2:** Add the same four keys to `.env.example` with comments (key: "Voyage AI; empty disables repost detection"; mode: `off|shadow|enforce`, default shadow; threshold default 0.95).
- [ ] **Step 3: Verify** with `uv lock --check` (lock unchanged) and a YAML lint of `deploy.yml` (e.g. `uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml'))"`).

### Task 6: Review, memory, PR (Din Djarin)

- [ ] **Step 1:** Dispatch ig-11 with the full `git diff main...` and the user's original request only.
- [ ] **Step 2:** On APPROVED, ratify memory: new `project_repost_detection.md` (design + rollout state) and a `project_pending.md` entry: "Add `VOYAGE_API_KEY` to GitHub Secrets (2026-11-05); after 1–2 weeks of shadow logs set `REPOST_THRESHOLD`, then `REPOST_MODE=enforce`". Update `MEMORY.md`.
- [ ] **Step 3:** Stage files by name and commit (`feat: add embedding-based repost detection (shadow mode)`). No Co-Authored-By.
- [ ] **Step 4:** `git push -u origin feat/repost-detection`; `gh pr create`.
