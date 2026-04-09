from datetime import datetime

import pytest

from ahsoka.models import Post, Score, UserConfig
from ahsoka.pipeline.user_filter import matches_user


def make_post(text: str = "Python backend developer needed") -> Post:
    return Post(channel_id=1, message_id=1, channel_name="test", text=text, timestamp=datetime.now())


def make_score(**kwargs) -> Score:
    defaults = dict(score=8, reason="Good", stack=["python", "django"], seniority="senior", remote="remote")
    defaults.update(kwargs)
    return Score(**defaults)


def make_config(**kwargs) -> UserConfig:
    defaults = dict(user_id=1, notify_chat_id=1, threshold=7)
    defaults.update(kwargs)
    return UserConfig(**defaults)


# --- Basic checks (paused, threshold, keywords) ---

def test_paused_user_never_matches():
    assert not matches_user(make_post(), make_score(), make_config(paused=True))


def test_below_threshold_does_not_match():
    assert not matches_user(make_post(), make_score(score=5), make_config(threshold=7))


def test_at_threshold_matches():
    assert matches_user(make_post(), make_score(score=7), make_config(threshold=7))


def test_keywords_filter_still_works():
    assert not matches_user(make_post("Go developer"), make_score(), make_config(keywords="python"))


def test_keywords_match_passes():
    assert matches_user(make_post("Python developer"), make_score(), make_config(keywords="python"))


# --- Stack matching ---

def test_empty_user_stack_matches_any():
    assert matches_user(make_post(), make_score(stack=["go", "rust"]), make_config(stack=""))


def test_stack_overlap_matches():
    assert matches_user(make_post(), make_score(stack=["python", "django"]), make_config(stack="python go"))


def test_stack_no_overlap_does_not_match():
    assert not matches_user(make_post(), make_score(stack=["go", "rust"]), make_config(stack="python django"))


def test_stack_match_is_case_insensitive():
    assert matches_user(make_post(), make_score(stack=["python"]), make_config(stack="Python"))


# --- Seniority matching ---

def test_empty_user_seniority_matches_any():
    assert matches_user(make_post(), make_score(seniority="junior"), make_config(seniority=""))


def test_score_seniority_any_matches_all_users():
    assert matches_user(make_post(), make_score(seniority="any"), make_config(seniority="senior"))


def test_seniority_exact_match():
    assert matches_user(make_post(), make_score(seniority="senior"), make_config(seniority="senior"))


def test_seniority_mismatch():
    assert not matches_user(make_post(), make_score(seniority="junior"), make_config(seniority="senior"))


# --- Remote matching ---

def test_empty_user_remote_matches_any():
    assert matches_user(make_post(), make_score(remote="onsite"), make_config(remote=""))


def test_score_remote_unknown_matches_all_users():
    assert matches_user(make_post(), make_score(remote="unknown"), make_config(remote="remote"))


def test_remote_exact_match():
    assert matches_user(make_post(), make_score(remote="remote"), make_config(remote="remote"))


def test_remote_mismatch():
    assert not matches_user(make_post(), make_score(remote="onsite"), make_config(remote="remote"))


# --- Combinations ---

def test_all_filters_must_pass():
    """Stack matches but seniority doesn't — should not match."""
    assert not matches_user(
        make_post(),
        make_score(stack=["python"], seniority="junior"),
        make_config(stack="python", seniority="senior"),
    )


def test_everything_matches():
    assert matches_user(
        make_post("Python senior remote"),
        make_score(score=9, stack=["python"], seniority="senior", remote="remote"),
        make_config(threshold=7, keywords="python", stack="python", seniority="senior", remote="remote"),
    )
