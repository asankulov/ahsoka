"""Tests for ahsoka.pipeline.user_filter — new signature: matches_user(verdict, config)."""
import pytest

from ahsoka.models import PersonalizedVerdict, UserConfig
from ahsoka.pipeline.user_filter import matches_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_verdict(
    user_id: int = 1,
    score: int = 8,
    matched: bool = True,
    reason: str = "Good fit",
    apply: str = "",
    red_flags: list | None = None,
) -> PersonalizedVerdict:
    return PersonalizedVerdict(
        user_id=user_id,
        score=score,
        matched=matched,
        reason=reason,
        apply=apply,
        red_flags=red_flags or [],
    )


def make_config(
    user_id: int = 1,
    threshold: int = 7,
    paused: bool = False,
    is_banned: bool = False,
) -> UserConfig:
    return UserConfig(user_id=user_id, notify_chat_id=user_id, threshold=threshold, paused=paused, is_banned=is_banned)


# ---------------------------------------------------------------------------
# Paused
# ---------------------------------------------------------------------------


def test_paused_user_returns_false_regardless_of_score():
    verdict = make_verdict(score=10, matched=True)
    config = make_config(paused=True, threshold=5)
    assert matches_user(verdict, config) is False


def test_paused_user_returns_false_regardless_of_matched():
    verdict = make_verdict(score=9, matched=True)
    config = make_config(paused=True)
    assert matches_user(verdict, config) is False


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------


def test_score_below_threshold_returns_false_even_if_matched():
    verdict = make_verdict(score=5, matched=True)
    config = make_config(threshold=7, paused=False)
    assert matches_user(verdict, config) is False


def test_score_at_threshold_is_not_below():
    verdict = make_verdict(score=7, matched=True)
    config = make_config(threshold=7, paused=False)
    # score == threshold: check what the code does (score < threshold → False)
    # 7 < 7 is False, so we do NOT return False here
    assert matches_user(verdict, config) is True


def test_score_above_threshold_passes_threshold_check():
    verdict = make_verdict(score=9, matched=True)
    config = make_config(threshold=7, paused=False)
    assert matches_user(verdict, config) is True


# ---------------------------------------------------------------------------
# matched flag
# ---------------------------------------------------------------------------


def test_verdict_not_matched_returns_false_even_if_score_above_threshold():
    verdict = make_verdict(score=9, matched=False)
    config = make_config(threshold=7, paused=False)
    assert matches_user(verdict, config) is False


def test_verdict_matched_false_returns_false_regardless_of_other_fields():
    verdict = make_verdict(score=10, matched=False)
    config = make_config(threshold=1, paused=False)
    assert matches_user(verdict, config) is False


# ---------------------------------------------------------------------------
# All conditions True → True
# ---------------------------------------------------------------------------


def test_all_conditions_met_returns_true():
    verdict = make_verdict(score=8, matched=True)
    config = make_config(threshold=7, paused=False)
    assert matches_user(verdict, config) is True


def test_minimum_passing_combination():
    verdict = make_verdict(score=7, matched=True)
    config = make_config(threshold=7, paused=False)
    assert matches_user(verdict, config) is True


# ---------------------------------------------------------------------------
# Regression: old signature (post, score, config) must not be accepted
# This is a type-level concern — we just verify the new 2-arg API works cleanly.
# ---------------------------------------------------------------------------


def test_function_accepts_exactly_two_args():
    import inspect
    sig = inspect.signature(matches_user)
    assert len(sig.parameters) == 2


# ---------------------------------------------------------------------------
# Banned
# ---------------------------------------------------------------------------


def test_banned_user_returns_false_regardless_of_score():
    verdict = make_verdict(score=10, matched=True)
    config = make_config(is_banned=True, threshold=1, paused=False)
    assert matches_user(verdict, config) is False


def test_banned_user_returns_false_regardless_of_matched():
    verdict = make_verdict(score=10, matched=True)
    config = make_config(is_banned=True)
    assert matches_user(verdict, config) is False


def test_banned_user_returns_false_even_when_not_paused():
    """Ban is checked before pause; not-paused does not override ban."""
    verdict = make_verdict(score=10, matched=True)
    config = make_config(is_banned=True, paused=False, threshold=1)
    assert matches_user(verdict, config) is False


def test_banned_and_paused_user_returns_false():
    verdict = make_verdict(score=10, matched=True)
    config = make_config(is_banned=True, paused=True, threshold=1)
    assert matches_user(verdict, config) is False


def test_not_banned_unaffected_all_conditions_met():
    """is_banned=False must not block an otherwise-passing verdict."""
    verdict = make_verdict(score=8, matched=True)
    config = make_config(is_banned=False, threshold=7, paused=False)
    assert matches_user(verdict, config) is True


def test_not_banned_unaffected_score_below_threshold():
    """is_banned=False with low score still returns False (threshold guard fires)."""
    verdict = make_verdict(score=3, matched=True)
    config = make_config(is_banned=False, threshold=7, paused=False)
    assert matches_user(verdict, config) is False
