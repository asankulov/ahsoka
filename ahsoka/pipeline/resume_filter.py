import re

from ahsoka.models import Post

RESUME_MARKERS = ("#резюме", "#resume", "#cv")

_RESUME_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(m) for m in RESUME_MARKERS) + r")(?!\w)",
    re.IGNORECASE,
)

# Leading-word signal: the first line itself starts with "Резюме"/"Resume"/"CV".
_RESUME_LEAD_RE = re.compile(r"(?:резюме|resume|cv)(?!\w)", re.IGNORECASE)


def is_resume(post: Post) -> bool:
    """Return True if the post is a candidate CV (resume hashtag or resume-led first line)."""
    return has_resume_marker(post.text)


def _starts_with_resume_word(text: str) -> bool:
    """True if the first line, after skipping leading non-alphanumerics, starts with a resume word."""
    first_line = text.lstrip().split("\n", 1)[0]
    i = 0
    while i < len(first_line) and not first_line[i].isalnum():
        i += 1
    return bool(_RESUME_LEAD_RE.match(first_line, i))


def has_resume_marker(text: str) -> bool:
    """Return True if text carries a resume hashtag or starts (first line) with a resume word.

    Only the start of the first line counts for the word signal, so mid-text
    mentions like "Отправьте резюме на hr@co.com" do not match.
    """
    return bool(_RESUME_RE.search(text)) or _starts_with_resume_word(text)
