from __future__ import annotations

import aiosqlite

from ahsoka import database as db
from ahsoka.models import Post


class KeywordIndex:
    """Union of all users' keywords for cheap pre-filtering before Claude."""

    def __init__(self) -> None:
        self._union: set[str] = set()
        self._any_empty: bool = True

    async def rebuild(self, conn: aiosqlite.Connection) -> None:
        configs = await db.get_all_active_configs(conn)
        self._any_empty = any(not c.keywords.strip() for c in configs)
        self._union = set()
        for c in configs:
            self._union.update(kw.lower() for kw in c.keywords.split() if kw.strip())

    def passes(self, post: Post) -> bool:
        """Return True if any user's keywords match or any user has empty keywords."""
        if self._any_empty or not self._union:
            return True
        text_lower = post.text.lower()
        return any(kw in text_lower for kw in self._union)
