from ahsoka.models import Post, Score, UserConfig
from ahsoka.pipeline.keyword_filter import passes_keyword_filter


def _matches_stack(score: Score, config: UserConfig) -> bool:
    if not config.stack:
        return True
    user_stack = {s.lower() for s in config.stack.split() if s.strip()}
    return bool(user_stack & set(score.stack))


def _matches_seniority(score: Score, config: UserConfig) -> bool:
    if not config.seniority:
        return True
    if score.seniority == "any":
        return True
    return config.seniority.lower() == score.seniority


def _matches_remote(score: Score, config: UserConfig) -> bool:
    if not config.remote:
        return True
    if score.remote == "unknown":
        return True
    return config.remote.lower() == score.remote


def matches_user(post: Post, score: Score, config: UserConfig) -> bool:
    """Check if a scored post should be sent to this user."""
    if config.paused:
        return False
    if score.score < config.threshold:
        return False
    if config.keywords and not passes_keyword_filter(post, config.keywords):
        return False
    if not _matches_stack(score, config):
        return False
    if not _matches_seniority(score, config):
        return False
    if not _matches_remote(score, config):
        return False
    return True
