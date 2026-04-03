from datetime import datetime

import pytest

from ahsoka.models import Post
from ahsoka.pipeline.keyword_filter import passes_keyword_filter


def make_post(text: str) -> Post:
    return Post(channel_id=1, message_id=1, channel_name="test", text=text, timestamp=datetime.now())


def test_empty_keywords_passes_all():
    assert passes_keyword_filter(make_post("anything here"), "") is True


def test_whitespace_only_keywords_passes_all():
    assert passes_keyword_filter(make_post("anything"), "   ") is True


def test_matching_keyword():
    assert passes_keyword_filter(make_post("we need a Python developer"), "python golang") is True


def test_non_matching_keywords():
    assert passes_keyword_filter(make_post("Java Spring developer needed"), "python golang") is False


def test_case_insensitive():
    assert passes_keyword_filter(make_post("PYTHON developer"), "python") is True


def test_partial_match():
    assert passes_keyword_filter(make_post("pythonista wanted"), "python") is True


def test_multiple_keywords_any_match():
    assert passes_keyword_filter(make_post("we love Rust"), "python golang rust") is True
