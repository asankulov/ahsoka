from ahsoka.models import Post, Score, UserConfig
from ahsoka.pipeline.keyword_filter import passes_keyword_filter


def matches_user(post: Post, score: Score, config: UserConfig) -> bool:
    """Check if a scored post should be sent to this user."""
    if config.paused:
        return False
    if score.score < config.threshold:
        return False
    if config.keywords and not passes_keyword_filter(post, config.keywords):
        return False
    return True
