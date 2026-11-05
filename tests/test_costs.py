import aiosqlite
import pytest
from unittest.mock import MagicMock

from ahsoka import database as db
from ahsoka.config import Settings

from ahsoka.pipeline import costs
from ahsoka.pipeline.costs import cost_for, embed_cost_for, price_for


def test_cost_for_known_model():
    # haiku-5-5: $0.05/M input + $0.25/M output
    cost = cost_for("claude-haiku-5-5", 1_000_000, 1_000_000, 0, 0)
    assert cost == pytest.approx(0.05 + 0.25)


def test_cost_for_unknown_returns_none():
    assert price_for("gpt-99") is None
    assert cost_for("gpt-99", 1000, 1000, 0, 0) is None


def test_longest_prefix_wins(monkeypatch):
    # Real sonnet-4 / sonnet-4-6 rates are identical today, so give them
    # distinct prices to prove the longer prefix is selected.
    monkeypatch.setitem(costs.PRICES, "claude-sonnet-4", (1.0, 2.0, 3.0, 4.0))
    monkeypatch.setitem(costs.PRICES, "claude-sonnet-4-6", (9.0, 8.0, 7.0, 6.0))
    assert price_for("claude-sonnet-4-6-20260101") == (9.0, 8.0, 7.0, 6.0)
    assert price_for("claude-sonnet-4-20250514") == (1.0, 2.0, 3.0, 4.0)


def test_embed_cost_for_unknown_none():
    assert embed_cost_for("not-a-voyage-model", 1000) is None


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await db.init_db(c)
        yield c


def make_settings(**overrides):
    s = MagicMock(spec=Settings)
    values = dict(
        claude_model="claude-haiku-5-5",
        repost_mode="shadow",
        repost_threshold=0.95,
        batch_flush_size=50,
        batch_flush_seconds=300,
        voyage_model="voyage-4-lite",
        repost_dimensions=512,
        default_score_threshold=7,
        bot_token="secret-token",
    )
    values.update(overrides)
    for k, v in values.items():
        setattr(s, k, v)
    return s


def test_tracked_settings_exact():
    assert costs.TRACKED_SETTINGS == (
        "claude_model", "repost_mode", "repost_threshold", "batch_flush_size",
        "batch_flush_seconds", "voyage_model", "repost_dimensions",
        "default_score_threshold",
    )


async def test_first_run_saves_snapshot_writes_no_events(conn):
    n = await costs.record_setting_changes(conn, make_settings())
    assert n == 0
    assert await db.list_cost_events(conn, "0000-01-01", "9999-12-31") == []
    snap = await db.get_setting_snapshot(conn)
    assert snap["claude_model"] == "claude-haiku-5-5"
    assert snap["repost_threshold"] == "0.95"


async def test_unchanged_second_run_writes_no_events(conn):
    await costs.record_setting_changes(conn, make_settings())
    assert await costs.record_setting_changes(conn, make_settings()) == 0
    assert await db.list_cost_events(conn, "0000-01-01", "9999-12-31") == []


async def test_changed_claude_model_writes_one_event_with_old_new(conn):
    await costs.record_setting_changes(conn, make_settings())
    n = await costs.record_setting_changes(conn, make_settings(claude_model="claude-sonnet-4-6"))
    assert n == 1
    evs = await db.list_cost_events(conn, "0000-01-01", "9999-12-31")
    assert len(evs) == 1
    e = evs[0]
    assert e["kind"] == "setting"
    assert e["key"] == "claude_model"
    assert e["old_value"] == "claude-haiku-5-5"
    assert e["new_value"] == "claude-sonnet-4-6"
    # no duplicate after restart with same new value
    assert await costs.record_setting_changes(conn, make_settings(claude_model="claude-sonnet-4-6")) == 0


async def test_float_setting_change_stored_as_string(conn):
    await costs.record_setting_changes(conn, make_settings())
    n = await costs.record_setting_changes(conn, make_settings(repost_threshold=0.9))
    assert n == 1
    e = (await db.list_cost_events(conn, "0000-01-01", "9999-12-31"))[0]
    assert (e["key"], e["old_value"], e["new_value"]) == ("repost_threshold", "0.95", "0.9")
    assert (await db.get_setting_snapshot(conn))["repost_threshold"] == "0.9"


async def test_untracked_setting_change_ignored(conn):
    await costs.record_setting_changes(conn, make_settings())
    n = await costs.record_setting_changes(conn, make_settings(bot_token="other"))
    assert n == 0
    assert await db.list_cost_events(conn, "0000-01-01", "9999-12-31") == []
    assert "bot_token" not in await db.get_setting_snapshot(conn)


async def test_other_snapshot_keys_preserved(conn):
    await db.save_setting_snapshot(conn, {"digest_last_sent": "2026-11-01"})
    await costs.record_setting_changes(conn, make_settings())
    await costs.record_setting_changes(conn, make_settings(claude_model="x"))
    snap = await db.get_setting_snapshot(conn)
    assert snap["digest_last_sent"] == "2026-11-01"
    assert snap["claude_model"] == "x"


async def test_first_run_with_only_digest_key_writes_no_events(conn):
    """Table non-empty (digest_last_sent) must not make first run look like a diff against nothing."""
    await db.save_setting_snapshot(conn, {"digest_last_sent": "2026-11-01"})
    assert await costs.record_setting_changes(conn, make_settings()) == 0
    assert await db.list_cost_events(conn, "0000-01-01", "9999-12-31") == []


async def test_first_population_snapshot_writes_no_events(conn):
    assert await costs.record_population(conn, "2026-11-01", 10, 5, 20) == 0
    snap = await db.get_last_population_snapshot(conn)
    assert (snap["users"], snap["channels"], snap["keywords"]) == (10, 5, 20)


@pytest.mark.parametrize(
    "new,expected",
    [(13, 1), (11, 0), (12, 1), (10, 0)],
)
async def test_population_threshold_users_from_10(conn, new, expected):
    await costs.record_population(conn, "2026-11-01", 10, 5, 20)
    n = await costs.record_population(conn, "2026-11-02", new, 5, 20)
    assert n == expected
    evs = await db.list_cost_events(conn, "0000-01-01", "9999-12-31")
    assert len(evs) == expected
    if expected:
        assert evs[0]["kind"] == "population"
        assert evs[0]["key"] == "users"
        assert evs[0]["old_value"] == "10"
        assert evs[0]["new_value"] == str(new)
    assert (await db.get_last_population_snapshot(conn))["users"] == new


async def test_population_large_base_small_change_ignored(conn):
    await costs.record_population(conn, "2026-11-01", 100, 5, 20)
    assert await costs.record_population(conn, "2026-11-02", 101, 5, 20) == 0


async def test_population_large_relative_but_abs_one_ignored(conn):
    await costs.record_population(conn, "2026-11-01", 2, 5, 20)
    assert await costs.record_population(conn, "2026-11-02", 3, 5, 20) == 0


async def test_population_from_zero_abs_two_qualifies(conn):
    await costs.record_population(conn, "2026-11-01", 0, 0, 0)
    n = await costs.record_population(conn, "2026-11-02", 2, 1, 0)
    assert n == 1
    evs = await db.list_cost_events(conn, "0000-01-01", "9999-12-31")
    assert evs[0]["key"] == "users"


async def test_population_multiple_metrics_each_get_event(conn):
    await costs.record_population(conn, "2026-11-01", 10, 10, 10)
    n = await costs.record_population(conn, "2026-11-02", 10, 20, 5)
    assert n == 2
    keys = {e["key"] for e in await db.list_cost_events(conn, "0000-01-01", "9999-12-31")}
    assert keys == {"channels", "keywords"}


async def test_collect_population_excludes_banned_users(conn):
    for uid in (1, 2, 3):
        await db.get_or_create_user(conn, uid)
    await db.ban_user(conn, 3)
    idx = MagicMock()
    idx.keyword_count = 7
    users, channels, kws = await costs.collect_population(conn, idx, [101, 102, 103, 104])
    assert (users, channels, kws) == (2, 4, 7)


# ---------------------------------------------------------------------------
# format_buckets
# ---------------------------------------------------------------------------


def _bucket(label="2026-11-05", **kw):
    b = {
        "bucket": label, "scoring_usd": 1.0, "embedding_usd": 0.5,
        "input_tokens": 2_000_000, "output_tokens": 50_000, "cache_read_tokens": 0,
        "embedding_tokens": 10_000, "batches": 3, "succeeded": 30,
        "unpriced_batches": 0,
    }
    b.update(kw)
    return b


def test_format_buckets_empty_says_no_cost_data():
    assert "No cost data yet" in costs.format_buckets([], [], "day")


def test_format_buckets_shows_label_total_split_and_batches():
    out = costs.format_buckets([_bucket()], [], "day")
    assert "2026-11-05" in out
    assert "$1.50" in out
    assert "$1.00" in out and "$0.50" in out
    assert "3 batches" in out
    assert "2.0M" in out


def test_format_buckets_cost_per_request():
    out = costs.format_buckets([_bucket(scoring_usd=3.0, embedding_usd=0.0, succeeded=30)], [], "day")
    assert "$0.10/req" in out  # 3.00 / 30
    assert "/post" not in out


def test_format_buckets_tiny_per_request_four_decimals():
    out = costs.format_buckets(
        [_bucket(scoring_usd=0.0123, embedding_usd=0.0, succeeded=1)], [], "day"
    )
    assert "$0.0123/req" in out


def test_format_buckets_no_per_request_when_none_succeeded():
    out = costs.format_buckets([_bucket(succeeded=0)], [], "day")
    assert "/req" not in out
    assert "/post" not in out


def test_format_buckets_tiny_cost_uses_four_decimals():
    out = costs.format_buckets(
        [_bucket(scoring_usd=0.0042, embedding_usd=0.0, succeeded=0)], [], "day"
    )
    assert "$0.0042" in out


def test_format_buckets_unpriced_shown_not_added():
    out = costs.format_buckets(
        [_bucket(scoring_usd=0.0, embedding_usd=0.0, unpriced_batches=2, succeeded=0)], [], "day"
    )
    assert "2 unpriced" in out


def test_format_buckets_no_unpriced_text_when_zero():
    assert "unpriced" not in costs.format_buckets([_bucket()], [], "day")


def test_format_buckets_setting_event_under_matching_bucket_only():
    ev = [{"ts": "2026-11-05T10:00:00Z", "kind": "setting", "key": "claude_model",
           "old_value": "claude-haiku-4-5", "new_value": "claude-haiku-5-5"}]
    out = costs.format_buckets([_bucket("2026-11-05"), _bucket("2026-11-04")], ev, "day")
    line = "  ⚙ claude_model: claude-haiku-4-5 → claude-haiku-5-5"
    assert line in out
    assert out.index("2026-11-05") < out.index(line) < out.index("2026-11-04")


def test_format_buckets_population_event_rendered():
    ev = [{"ts": "2026-11-05T10:00:00Z", "kind": "population", "key": "users",
           "old_value": "10", "new_value": "13"}]
    out = costs.format_buckets([_bucket()], ev, "day")
    assert "  \U0001F465 users: 10 → 13" in out


@pytest.mark.parametrize("gran,label", [("month", "2026-11"), ("year", "2026")])
def test_format_buckets_event_prefix_match_for_coarser_granularity(gran, label):
    ev = [{"ts": "2026-11-05T10:00:00Z", "kind": "setting", "key": "k",
           "old_value": "a", "new_value": "b"}]
    assert "k: a → b" in costs.format_buckets([_bucket(label)], ev, gran)


def test_format_buckets_event_outside_buckets_not_shown():
    ev = [{"ts": "2026-10-01T10:00:00Z", "kind": "setting", "key": "k",
           "old_value": "a", "new_value": "b"}]
    assert "k: a" not in costs.format_buckets([_bucket("2026-11-05")], ev, "day")


def _ev(ts, key="k", old="a", new="b", kind="setting"):
    return {"ts": ts, "kind": kind, "key": key, "old_value": old, "new_value": new}


def test_format_buckets_event_on_day_without_bucket_between_buckets_shown():
    out = costs.format_buckets(
        [_bucket("2026-11-05"), _bucket("2026-11-03")],
        [_ev("2026-11-04T10:00:00Z", key="claude_model")],
        "day",
    )
    ph = "2026-11-04 \u2014 no API activity"
    assert ph in out
    assert "  \u2699 claude_model: a \u2192 b" in out
    assert out.index("2026-11-05") < out.index(ph) < out.index("2026-11-03")
    assert out.index(ph) < out.index("claude_model") < out.index("2026-11-03")


def test_format_buckets_event_newer_than_newest_bucket_shown_first():
    out = costs.format_buckets(
        [_bucket("2026-11-05")], [_ev("2026-11-07T10:00:00Z", key="zzz")], "day"
    )
    ph = "2026-11-07 \u2014 no API activity"
    assert ph in out
    assert out.index(ph) < out.index("zzz") < out.index("2026-11-05")


def test_format_buckets_event_older_than_oldest_bucket_hidden():
    out = costs.format_buckets(
        [_bucket("2026-11-05"), _bucket("2026-11-03")],
        [_ev("2026-11-02T10:00:00Z", key="old_key")],
        "day",
    )
    assert "old_key" not in out
    assert "no API activity" not in out


def test_format_buckets_event_equal_to_oldest_bucket_label_is_under_real_bucket():
    out = costs.format_buckets(
        [_bucket("2026-11-05")], [_ev("2026-11-05T01:00:00Z", key="here")], "day"
    )
    assert "here" in out
    assert "no API activity" not in out


def test_format_buckets_multiple_events_same_empty_day_one_placeholder():
    out = costs.format_buckets(
        [_bucket("2026-11-05"), _bucket("2026-11-01")],
        [_ev("2026-11-03T01:00:00Z", key="e1"), _ev("2026-11-03T02:00:00Z", key="e2")],
        "day",
    )
    assert out.count("2026-11-03 \u2014 no API activity") == 1
    assert "e1" in out and "e2" in out


def test_format_buckets_no_buckets_with_events_still_no_cost_data():
    out = costs.format_buckets([], [_ev("2026-11-05T01:00:00Z")], "day")
    assert out == "No cost data yet."


def test_format_buckets_month_event_without_bucket_placeholder():
    out = costs.format_buckets(
        [_bucket("2026-11"), _bucket("2026-08")],
        [_ev("2026-10-15T00:00:00Z", key="mk"), _ev("2026-07-15T00:00:00Z", key="hidden_m")],
        "month",
    )
    ph = "2026-10 \u2014 no API activity"
    assert ph in out
    assert out.index("2026-11") < out.index(ph) < out.index("2026-08")
    assert "mk" in out and "hidden_m" not in out


def test_format_buckets_year_event_without_bucket_placeholder():
    out = costs.format_buckets(
        [_bucket("2026"), _bucket("2024")],
        [_ev("2025-06-01T00:00:00Z", key="yk"), _ev("2023-06-01T00:00:00Z", key="hidden_y")],
        "year",
    )
    ph = "2025 \u2014 no API activity"
    assert ph in out
    assert out.index("2026") < out.index(ph) < out.index("2024")
    assert "yk" in out and "hidden_y" not in out


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------

TODAY = "2026-11-05"  # digest covers yesterday = 2026-11-04


async def _seed_batch(conn, batch_id, day, cost, succeeded=10, inp=1_000_000, out=50_000):
    await db.save_batch_usage(conn, batch_id, "claude-haiku-5-5", inp, out, 0, 0, succeeded, cost)
    await conn.execute(
        "UPDATE batch_usage SET recorded_at = ? WHERE batch_id = ?",
        (f"{day}T12:00:00Z", batch_id),
    )
    await conn.commit()


async def _seed_activity(conn):
    await _seed_batch(conn, "b-prev-month", "2026-10-31", 100.0)  # excluded from MTD
    await _seed_batch(conn, "b-early", "2026-11-02", 2.0)  # in MTD, not in yesterday
    await _seed_batch(conn, "b-yday", "2026-11-04", 1.0)
    await db.record_embedding_usage(conn, "2026-11-04", "voyage-4-lite", 10_000, 0.5)


async def test_build_digest_header_has_yesterday_date(conn):
    await _seed_activity(conn)
    out = await costs.build_digest(conn, TODAY)
    assert "2026-11-04" in out


async def test_build_digest_yesterday_total_batches_tokens(conn):
    await _seed_activity(conn)
    out = await costs.build_digest(conn, TODAY)
    assert "$1.50" in out  # 1.00 scoring + 0.50 embedding
    assert "1 batches" in out or "1 batch" in out
    assert "1.0M" in out  # input tokens


async def test_build_digest_month_to_date_includes_earlier_days_same_month(conn):
    await _seed_activity(conn)
    out = await costs.build_digest(conn, TODAY)
    assert "month" in out.lower()
    assert "$3.50" in out  # 2.00 + 1.00 + 0.50
    assert "$103.50" not in out  # previous month excluded


async def test_build_digest_includes_change_event_since_yesterday(conn):
    await _seed_activity(conn)
    await conn.execute(
        "INSERT INTO cost_events (ts, kind, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
        ("2026-11-04T10:00:00Z", "setting", "claude_model", "claude-haiku-4-5", "claude-haiku-5-5"),
    )
    await conn.execute(
        "INSERT INTO cost_events (ts, kind, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
        ("2026-10-01T10:00:00Z", "setting", "voyage_model", "old-v", "new-v"),
    )
    await conn.commit()
    out = await costs.build_digest(conn, TODAY)
    assert "Changes since last digest:" in out
    assert "  ⚙ claude_model: claude-haiku-4-5 → claude-haiku-5-5" in out
    assert "voyage_model" not in out


async def test_build_digest_no_changes_no_changes_header(conn):
    await _seed_activity(conn)
    out = await costs.build_digest(conn, TODAY)
    assert "Changes since last digest" not in out


async def test_build_digest_reconciliation_line_signed_percent(conn):
    await _seed_activity(conn)
    await db.save_reconciliation(conn, "2026-11-04", billed_usd=1.65, estimated_usd=1.5)
    out = await costs.build_digest(conn, TODAY)
    assert "billed $1.65 vs est. $1.50 (Δ +10.0%)" in out


async def test_build_digest_reconciliation_negative_percent(conn):
    await _seed_activity(conn)
    await db.save_reconciliation(conn, "2026-11-04", billed_usd=1.35, estimated_usd=1.5)
    out = await costs.build_digest(conn, TODAY)
    assert "(Δ -10.0%)" in out


async def test_build_digest_reconciliation_absent_no_line(conn):
    await _seed_activity(conn)
    out = await costs.build_digest(conn, TODAY)
    assert "billed" not in out


async def test_build_digest_reconciliation_zero_estimate_prints_na(conn):
    await _seed_activity(conn)
    await db.save_reconciliation(conn, "2026-11-04", billed_usd=0.5, estimated_usd=0.0)
    out = await costs.build_digest(conn, TODAY)
    assert "n/a" in out
    assert "billed $0.50 vs est. $0.00" in out


async def test_build_digest_no_activity_still_produced(conn):
    out = await costs.build_digest(conn, TODAY)
    assert out
    assert "2026-11-04" in out
    assert "No API activity" in out


async def test_build_digest_activity_has_no_no_activity_line(conn):
    await _seed_activity(conn)
    assert "No API activity" not in await costs.build_digest(conn, TODAY)


async def test_build_digest_unpriced_batch_shown_not_as_zero(conn):
    await _seed_batch(conn, "b-unpriced", "2026-11-04", None)
    out = await costs.build_digest(conn, TODAY)
    assert "1 unpriced" in out


async def test_build_digest_month_boundary_yesterday_in_previous_month(conn):
    await _seed_batch(conn, "b-oct", "2026-10-31", 4.0)
    await _seed_batch(conn, "b-nov", "2026-11-01", 50.0)
    out = await costs.build_digest(conn, "2026-11-01")  # yesterday = 2026-10-31
    assert "2026-10-31" in out
    assert "$4.00" in out
    assert "$54.00" not in out


async def _add_event(conn, ts, key="claude_model"):
    await conn.execute(
        "INSERT INTO cost_events (ts, kind, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
        (ts, "setting", key, "old", "new"),
    )
    await conn.commit()


async def test_build_digest_ts_key_excludes_event_before_stored_ts(conn):
    await _add_event(conn, "2026-11-04T10:00:00Z", key="before_k")
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-04T12:00:00Z"})
    out = await costs.build_digest(conn, TODAY)
    assert "before_k" not in out
    assert "Changes since last digest" not in out


async def test_build_digest_ts_key_includes_event_after_stored_ts(conn):
    await _add_event(conn, "2026-11-04T13:00:00Z", key="after_k")
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-04T12:00:00Z"})
    out = await costs.build_digest(conn, TODAY)
    assert "after_k" in out


async def test_build_digest_ts_key_is_exclusive(conn):
    await _add_event(conn, "2026-11-04T12:00:00Z", key="edge_k")
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-04T12:00:00Z"})
    assert "edge_k" not in await costs.build_digest(conn, TODAY)


async def test_build_digest_no_ts_key_falls_back_to_yesterday_start(conn):
    await _add_event(conn, "2026-11-04T00:00:00Z", key="fb_in")
    await _add_event(conn, "2026-11-03T23:59:59Z", key="fb_out")
    out = await costs.build_digest(conn, TODAY)
    assert "fb_in" in out and "fb_out" not in out


async def test_build_digest_no_event_repeat_across_consecutive_digests(conn):
    await _add_event(conn, "2026-11-04T10:00:00Z", key="once_k")
    first = await costs.build_digest(conn, TODAY)
    assert "once_k" in first
    await db.save_setting_snapshot(
        conn, {"digest_last_sent": TODAY, "digest_last_sent_ts": "2026-11-05T00:05:03Z"}
    )
    second = await costs.build_digest(conn, "2026-11-06")
    assert "once_k" not in second
    assert "Changes since last digest" not in second


async def test_build_digest_missed_day_includes_whole_gap(conn):
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-03T00:05:00Z"})
    await _add_event(conn, "2026-11-03T08:00:00Z", key="gap1")
    await _add_event(conn, "2026-11-04T08:00:00Z", key="gap2")
    await _add_event(conn, "2026-11-02T08:00:00Z", key="too_old")
    out = await costs.build_digest(conn, TODAY)
    assert "gap1" in out and "gap2" in out and "too_old" not in out


async def test_digest_last_sent_ts_not_diffed_as_setting(conn):
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-04T12:00:00Z"})
    assert await costs.record_setting_changes(conn, make_settings()) == 0
    # first-run semantics unaffected by the ts key; snapshot preserved afterwards
    assert await costs.record_setting_changes(conn, make_settings(claude_model="x")) == 1
    snap = await db.get_setting_snapshot(conn)
    assert snap["digest_last_sent_ts"] == "2026-11-04T12:00:00Z"
    events = await db.list_cost_events(conn, "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z")
    assert [e["key"] for e in events] == ["claude_model"]


async def test_save_setting_snapshot_preserves_digest_ts(conn):
    await db.save_setting_snapshot(conn, {"digest_last_sent_ts": "2026-11-04T12:00:00Z"})
    await db.save_setting_snapshot(conn, {"claude_model": "m"})
    snap = await db.get_setting_snapshot(conn)
    assert snap["digest_last_sent_ts"] == "2026-11-04T12:00:00Z"
