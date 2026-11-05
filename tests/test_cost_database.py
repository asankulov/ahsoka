"""Tests for cost-tracking schema, backfill and cost queries in ahsoka.database."""

import aiosqlite
import pytest

from ahsoka.database import (
    add_cost_event,
    backfill_batch_costs,
    get_cost_buckets,
    get_last_population_snapshot,
    get_reconciliation,
    get_setting_snapshot,
    init_db,
    list_cost_events,
    record_embedding_usage,
    save_batch_usage,
    save_population_snapshot,
    save_reconciliation,
    save_setting_snapshot,
)
from ahsoka.pipeline.costs import cost_for

OWNER_ID = 12345
KNOWN = "claude-haiku-5-5"
UNKNOWN = "claude-mystery-9"


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c, owner_chat_id=OWNER_ID)
        yield c


async def _raw_batch(
    conn, batch_id, recorded_at, model=KNOWN, inp=1000, out=200, cw=0, cr=0,
    succeeded=5, cost=None, estimated=0,
):
    await conn.execute(
        """INSERT INTO batch_usage
           (batch_id, model, input_tokens, output_tokens,
            cache_creation_input_tokens, cache_read_input_tokens,
            succeeded, recorded_at, cost_usd, estimated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (batch_id, model, inp, out, cw, cr, succeeded, recorded_at, cost, estimated),
    )
    await conn.commit()


async def _row(conn, batch_id):
    async with conn.execute(
        "SELECT cost_usd, estimated FROM batch_usage WHERE batch_id = ?", (batch_id,)
    ) as cur:
        return await cur.fetchone()


# --- Schema ---


async def test_init_db_batch_usage_has_cost_columns(conn):
    async with conn.execute("PRAGMA table_info(batch_usage)") as cur:
        cols = {r[1] for r in await cur.fetchall()}
    assert {"cost_usd", "estimated"} <= cols


async def test_init_db_twice_is_noop(conn):
    await save_batch_usage(conn, "b1", KNOWN, 10, 5, 0, 0, 1, cost_usd=0.5)
    await init_db(conn, owner_chat_id=OWNER_ID)
    async with conn.execute("PRAGMA table_info(batch_usage)") as cur:
        names = [r[1] for r in await cur.fetchall()]
    assert names.count("cost_usd") == 1
    assert names.count("estimated") == 1
    assert await _row(conn, "b1") == (0.5, 0)


# --- save_batch_usage ---


async def test_save_batch_usage_stores_cost_and_not_estimated(conn):
    await save_batch_usage(conn, "b1", KNOWN, 10, 5, 0, 0, 1, cost_usd=1.25)
    assert await _row(conn, "b1") == (1.25, 0)


async def test_save_batch_usage_without_cost_stores_null(conn):
    await save_batch_usage(conn, "b1", KNOWN, 10, 5, 0, 0, 1)
    assert await _row(conn, "b1") == (None, 0)


# --- Backfill ---


async def test_backfill_idempotent(conn):
    await _raw_batch(conn, "old", "2026-10-01T10:00:00Z", model=KNOWN,
                     inp=1000, out=200, cw=100, cr=300)
    expected = cost_for(KNOWN, 1000, 200, 100, 300)

    assert await backfill_batch_costs(conn) == 1
    cost, est = await _row(conn, "old")
    assert cost == pytest.approx(expected)
    assert est == 1

    assert await backfill_batch_costs(conn) == 0
    cost2, est2 = await _row(conn, "old")
    assert cost2 == pytest.approx(expected)
    assert est2 == 1


async def test_backfill_unknown_model_stays_null(conn):
    await _raw_batch(conn, "mystery", "2026-10-01T10:00:00Z", model=UNKNOWN)
    assert await backfill_batch_costs(conn) == 0
    assert await _row(conn, "mystery") == (None, 0)


async def test_backfill_does_not_touch_priced_rows(conn):
    await save_batch_usage(conn, "exact", KNOWN, 1000, 200, 0, 0, 1, cost_usd=9.99)
    await backfill_batch_costs(conn)
    assert await _row(conn, "exact") == (9.99, 0)


async def test_init_db_runs_backfill(tmp_path):
    path = str(tmp_path / "t.db")
    async with aiosqlite.connect(path) as c:
        await init_db(c, owner_chat_id=OWNER_ID)
        await _raw_batch(c, "old", "2026-10-01T10:00:00Z", inp=1000, out=200)
    async with aiosqlite.connect(path) as c:
        await init_db(c, owner_chat_id=OWNER_ID)
        cost, est = await _row(c, "old")
    assert cost == pytest.approx(cost_for(KNOWN, 1000, 200, 0, 0))
    assert est == 1


# --- Buckets ---


async def test_buckets_utc_boundary(conn):
    await _raw_batch(conn, "late", "2026-10-01T23:59:59Z", cost=1.0)
    await _raw_batch(conn, "early", "2026-10-02T00:00:00Z", cost=2.0)
    buckets = await get_cost_buckets(conn, "day", 10)
    by_day = {b["bucket"]: b for b in buckets}
    assert set(by_day) == {"2026-10-01", "2026-10-02"}
    assert by_day["2026-10-01"]["scoring_usd"] == pytest.approx(1.0)
    assert by_day["2026-10-02"]["scoring_usd"] == pytest.approx(2.0)


async def test_buckets_aggregate_fields(conn):
    await _raw_batch(conn, "a", "2026-10-01T01:00:00Z", inp=100, out=10, cr=7,
                     succeeded=3, cost=1.0)
    await _raw_batch(conn, "b", "2026-10-01T02:00:00Z", inp=200, out=20, cr=3,
                     succeeded=4, cost=0.5)
    (b,) = await get_cost_buckets(conn, "day", 10)
    assert b["bucket"] == "2026-10-01"
    assert b["scoring_usd"] == pytest.approx(1.5)
    assert b["input_tokens"] == 300
    assert b["output_tokens"] == 30
    assert b["cache_read_tokens"] == 10
    assert b["batches"] == 2
    assert b["succeeded"] == 7
    assert b["unpriced_batches"] == 0
    assert b["embedding_usd"] == 0
    assert b["embedding_tokens"] == 0


async def test_unpriced_batch_counted_not_summed(conn):
    await _raw_batch(conn, "priced", "2026-10-01T01:00:00Z", cost=1.0)
    await _raw_batch(conn, "unpriced", "2026-10-01T02:00:00Z", model=UNKNOWN,
                     cost=None)
    (b,) = await get_cost_buckets(conn, "day", 10)
    assert b["batches"] == 2
    assert b["unpriced_batches"] == 1
    assert b["scoring_usd"] == pytest.approx(1.0)


async def test_buckets_month_granularity(conn):
    await _raw_batch(conn, "a", "2026-09-30T12:00:00Z", cost=1.0)
    await _raw_batch(conn, "b", "2026-10-01T12:00:00Z", cost=2.0)
    await _raw_batch(conn, "c", "2026-10-20T12:00:00Z", cost=3.0)
    buckets = await get_cost_buckets(conn, "month", 10)
    assert [b["bucket"] for b in buckets] == ["2026-10", "2026-09"]
    assert buckets[0]["scoring_usd"] == pytest.approx(5.0)
    assert buckets[1]["scoring_usd"] == pytest.approx(1.0)


async def test_buckets_year_granularity(conn):
    await _raw_batch(conn, "a", "2025-12-31T23:59:59Z", cost=1.0)
    await _raw_batch(conn, "b", "2026-01-01T00:00:00Z", cost=2.0)
    await _raw_batch(conn, "c", "2026-06-01T00:00:00Z", cost=3.0)
    buckets = await get_cost_buckets(conn, "year", 10)
    assert [b["bucket"] for b in buckets] == ["2026", "2025"]
    assert buckets[0]["scoring_usd"] == pytest.approx(5.0)


async def test_buckets_limit_and_newest_first(conn):
    for i in range(1, 6):
        await _raw_batch(conn, f"b{i}", f"2026-10-0{i}T10:00:00Z", cost=float(i))
    buckets = await get_cost_buckets(conn, "day", 3)
    assert [b["bucket"] for b in buckets] == ["2026-10-05", "2026-10-04", "2026-10-03"]


async def test_buckets_include_embedding_costs(conn):
    await _raw_batch(conn, "a", "2026-10-01T10:00:00Z", cost=1.0)
    await record_embedding_usage(conn, "2026-10-01", "voyage-4-lite", 1000, 0.25)
    (b,) = await get_cost_buckets(conn, "day", 10)
    assert b["embedding_usd"] == pytest.approx(0.25)
    assert b["embedding_tokens"] == 1000
    assert b["scoring_usd"] == pytest.approx(1.0)


async def test_buckets_empty(conn):
    assert await get_cost_buckets(conn, "day", 10) == []


# --- Embedding usage ---


async def test_record_embedding_usage_accumulates_per_day_model(conn):
    await record_embedding_usage(conn, "2026-10-01", "voyage-4-lite", 100, 0.1)
    await record_embedding_usage(conn, "2026-10-01", "voyage-4-lite", 50, 0.05)
    await record_embedding_usage(conn, "2026-10-02", "voyage-4-lite", 7, 0.01)
    buckets = {b["bucket"]: b for b in await get_cost_buckets(conn, "day", 10)}
    assert buckets["2026-10-01"]["embedding_tokens"] == 150
    assert buckets["2026-10-01"]["embedding_usd"] == pytest.approx(0.15)
    assert buckets["2026-10-02"]["embedding_tokens"] == 7


async def test_record_embedding_usage_sums_models_in_same_day(conn):
    await record_embedding_usage(conn, "2026-10-01", "voyage-4-lite", 100, 0.1)
    await record_embedding_usage(conn, "2026-10-01", "voyage-other", 200, 0.2)
    (b,) = await get_cost_buckets(conn, "day", 10)
    assert b["embedding_tokens"] == 300
    assert b["embedding_usd"] == pytest.approx(0.3)


# --- Cost events ---


async def test_cost_event_round_trip(conn):
    await add_cost_event(conn, "setting", "CLAUDE_MODEL", "old", "new")
    await add_cost_event(conn, "setting", "FLUSH_INTERVAL", None, "30")
    events = await list_cost_events(conn, "2000-01-01T00:00:00Z", "2100-01-01T00:00:00Z")
    assert len(events) == 2
    assert set(events[0]) >= {"ts", "kind", "key", "old_value", "new_value"}
    by_key = {e["key"]: e for e in events}
    assert by_key["CLAUDE_MODEL"]["kind"] == "setting"
    assert by_key["CLAUDE_MODEL"]["old_value"] == "old"
    assert by_key["CLAUDE_MODEL"]["new_value"] == "new"
    assert by_key["FLUSH_INTERVAL"]["old_value"] is None
    assert by_key["FLUSH_INTERVAL"]["new_value"] == "30"


async def test_list_cost_events_filters_by_window(conn):
    await add_cost_event(conn, "setting", "K", "a", "b")
    past = await list_cost_events(conn, "2000-01-01T00:00:00Z", "2001-01-01T00:00:00Z")
    future = await list_cost_events(conn, "2099-01-01T00:00:00Z", "2100-01-01T00:00:00Z")
    inside = await list_cost_events(conn, "2000-01-01T00:00:00Z", "2100-01-01T00:00:00Z")
    assert past == []
    assert future == []
    assert [e["key"] for e in inside] == ["K"]


# --- Setting snapshot ---


async def test_get_setting_snapshot_none_when_empty(conn):
    assert await get_setting_snapshot(conn) is None


async def test_setting_snapshot_round_trip(conn):
    await save_setting_snapshot(conn, {"CLAUDE_MODEL": "m1", "FLUSH": "30"})
    assert await get_setting_snapshot(conn) == {"CLAUDE_MODEL": "m1", "FLUSH": "30"}


async def test_save_setting_snapshot_upserts_and_keeps_other_keys(conn):
    # Another feature key shares the table; saving must never delete it.
    await conn.execute(
        "INSERT INTO setting_snapshot (key, value) VALUES ('digest_last_sent', '2026-10-01')"
    )
    await conn.commit()
    await save_setting_snapshot(conn, {"CLAUDE_MODEL": "m1", "FLUSH": "30"})
    await save_setting_snapshot(conn, {"CLAUDE_MODEL": "m2"})
    snap = await get_setting_snapshot(conn)
    assert snap == {
        "digest_last_sent": "2026-10-01",
        "CLAUDE_MODEL": "m2",
        "FLUSH": "30",
    }


# --- Population snapshot ---


async def test_population_snapshot_none_when_empty(conn):
    assert await get_last_population_snapshot(conn) is None


async def test_population_snapshot_round_trip(conn):
    await save_population_snapshot(conn, "2026-10-01", 3, 10, 40)
    snap = await get_last_population_snapshot(conn)
    assert snap["day"] == "2026-10-01"
    assert snap["users"] == 3
    assert snap["channels"] == 10
    assert snap["keywords"] == 40


async def test_get_last_population_snapshot_returns_most_recent_day(conn):
    await save_population_snapshot(conn, "2026-10-03", 5, 12, 50)
    await save_population_snapshot(conn, "2026-10-01", 3, 10, 40)
    await save_population_snapshot(conn, "2026-10-02", 4, 11, 45)
    snap = await get_last_population_snapshot(conn)
    assert snap["day"] == "2026-10-03"
    assert snap["users"] == 5


async def test_save_population_snapshot_same_day_overwrites(conn):
    await save_population_snapshot(conn, "2026-10-01", 3, 10, 40)
    await save_population_snapshot(conn, "2026-10-01", 4, 11, 41)
    snap = await get_last_population_snapshot(conn)
    assert (snap["users"], snap["channels"], snap["keywords"]) == (4, 11, 41)


# --- Reconciliation ---


async def test_reconciliation_none_when_missing(conn):
    assert await get_reconciliation(conn, "2026-10-01") is None


async def test_reconciliation_round_trip_and_overwrite(conn):
    await save_reconciliation(conn, "2026-10-01", 1.5, 1.4)
    rec = await get_reconciliation(conn, "2026-10-01")
    assert rec["billed_usd"] == pytest.approx(1.5)
    assert rec["estimated_usd"] == pytest.approx(1.4)
    await save_reconciliation(conn, "2026-10-01", 2.0, 1.9)
    rec = await get_reconciliation(conn, "2026-10-01")
    assert rec["billed_usd"] == pytest.approx(2.0)
    assert await get_reconciliation(conn, "2026-10-02") is None


# --- Migration from the pre-cost schema ---


async def test_init_db_migrates_old_schema_batch_usage(tmp_path):
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as c:
        await c.execute(
            """CREATE TABLE batch_usage (
                batch_id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL
            )"""
        )
        for bid, model in (("old_known", KNOWN), ("old_unknown", UNKNOWN)):
            await c.execute(
                "INSERT INTO batch_usage VALUES (?, ?, 1000, 200, 0, 0, 5, '2026-01-01T00:00:00Z')",
                (bid, model),
            )
        await c.commit()

        await init_db(c, owner_chat_id=OWNER_ID)

        async with c.execute("PRAGMA table_info(batch_usage)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        assert {"cost_usd", "estimated"} <= cols

        known = await _row(c, "old_known")
        assert known[0] == pytest.approx(cost_for(KNOWN, 1000, 200, 0, 0))
        assert known[1] == 1
        unknown = await _row(c, "old_unknown")
        assert unknown[0] is None

        await init_db(c, owner_chat_id=OWNER_ID)
        assert await _row(c, "old_known") == known
        assert await _row(c, "old_unknown") == unknown
