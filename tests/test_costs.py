import pytest

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
