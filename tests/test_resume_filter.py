"""Tests for ahsoka.pipeline.resume_filter.is_resume."""
from datetime import datetime

import pytest

from ahsoka.models import Post
from ahsoka.pipeline.resume_filter import has_resume_marker, is_resume


def make_post(text: str) -> Post:
    return Post(
        channel_id=1,
        message_id=1,
        channel_name="chan",
        text=text,
        urls=[],
        timestamp=datetime.now(),
    )


REAL_RESUME = (
    "#резюме #backend #nodejs #nestjs #golang #senior #microservices\n\n"
    "Позиция: Senior Backend Developer\nОпыт: 8 лет"
)


def test_is_resume_realistic_sample_true():
    assert is_resume(make_post(REAL_RESUME)) is True


@pytest.mark.parametrize(
    "text",
    ["#Resume", "#RESUME", "#CV", "#cv", "#РЕЗЮМЕ", "#Резюме", "#resume"],
)
def test_is_resume_case_insensitive_markers_true(text):
    assert is_resume(make_post(text)) is True


@pytest.mark.parametrize(
    "text",
    [
        "Ищу работу #резюме Python",
        "Python dev\n#cv\nопыт 5 лет",
        "Python developer looking for job #resume",
        "Python developer #resume.",
        "(#cv)",
    ],
)
def test_is_resume_hashtag_in_any_position_true(text):
    assert is_resume(make_post(text)) is True


@pytest.mark.parametrize(
    "text",
    ["Отправьте резюме на hr@co.com", "#резюмера", "#cvs", "#resumes", "x#cv", "#cv_senior", "#резюме2"],
)
def test_is_resume_non_hashtag_or_longer_token_false(text):
    assert is_resume(make_post(text)) is False


def test_is_resume_normal_vacancy_false():
    text = "#vacancy #python\nВакансия: Senior Python Developer\nПрисылайте резume/CV на почту"
    assert is_resume(make_post(text)) is False


def test_is_resume_empty_text_false():
    assert is_resume(make_post("")) is False


@pytest.mark.parametrize("text", ["#резюме dev", "see #CV below", "x\n#resume"])
def test_has_resume_marker_match_true(text):
    assert has_resume_marker(text) is True


@pytest.mark.parametrize("text", ["", "Senior Python vacancy", "#cvs", "#резюмера"])
def test_has_resume_marker_no_match_false(text):
    assert has_resume_marker(text) is False


def test_is_resume_delegates_to_post_text_marker():
    assert is_resume(make_post("#resume")) is True
    assert is_resume(make_post("vacancy")) is False
