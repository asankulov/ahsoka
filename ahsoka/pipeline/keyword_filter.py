from ahsoka.models import Post


def passes_keyword_filter(post: Post, keywords_str: str) -> bool:
    """Return True if the post matches at least one keyword, or if no keywords are set."""
    keywords = [k.lower() for k in keywords_str.split() if k.strip()]
    if not keywords:
        return True
    text_lower = post.text.lower()
    return any(kw in text_lower for kw in keywords)
