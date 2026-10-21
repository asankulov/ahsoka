import re

from ahsoka.models import Post

RESUME_MARKERS = ("#резюме", "#resume", "#cv")

_RESUME_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(m) for m in RESUME_MARKERS) + r")(?!\w)",
    re.IGNORECASE,
)


def is_resume(post: Post) -> bool:
    """Return True if the post carries a resume hashtag (candidate CV, not a vacancy)."""
    return bool(_RESUME_RE.search(post.text))
