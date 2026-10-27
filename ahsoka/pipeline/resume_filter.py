import re

from ahsoka.models import Post

RESUME_MARKERS = ("#резюме", "#resume", "#cv")

_RESUME_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(m) for m in RESUME_MARKERS) + r")(?!\w)",
    re.IGNORECASE,
)


def is_resume(post: Post) -> bool:
    """Return True if the post carries a resume hashtag (candidate CV, not a vacancy)."""
    return has_resume_marker(post.text)


def has_resume_marker(text: str) -> bool:
    """Return True if arbitrary text (e.g. fetched linked content) carries a resume hashtag."""
    return bool(_RESUME_RE.search(text))
