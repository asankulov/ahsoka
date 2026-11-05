"""Pure pricing helpers for Anthropic batch scoring and Voyage embeddings."""

# Batch API pricing per token (USD). Keys are model-name prefixes; longest match wins.
# Prices reflect the 50% batch-API discount vs. standard rates.
PRICES: dict[str, tuple[float, float, float, float]] = {
    # prefix → (input, output, cache_write, cache_read) per token
    "claude-haiku-3-5":  (0.40e-6, 2.00e-6, 0.50e-6, 0.04e-6),
    "claude-haiku-4-5":  (0.50e-6, 2.50e-6, 0.625e-6, 0.05e-6),
    # haiku-5-5: cache rates derived from 1.25x (write) / 0.1x (read) of input price
    "claude-haiku-5-5":  (0.05e-6, 0.25e-6, 0.0625e-6, 0.005e-6),
    "claude-sonnet-3-5": (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
    "claude-sonnet-4":   (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
    "claude-sonnet-4-6": (1.50e-6, 7.50e-6, 1.875e-6, 0.15e-6),
}

# Voyage embedding pricing, USD per token. Keys are model-name prefixes.
# NOTE: price is UNVERIFIED (no source in this repo); check Voyage's published
# pricing before relying on it for billing-grade numbers.
EMBED_PRICES: dict[str, float] = {
    "voyage-4-lite": 0.02e-6,
}


def _longest_prefix(table: dict, model: str):
    match = None
    for prefix, value in table.items():
        if model.startswith(prefix):
            if match is None or len(prefix) > len(match[0]):
                match = (prefix, value)
    return match[1] if match else None


def price_for(model: str) -> tuple[float, float, float, float] | None:
    """Return (input, output, cache_write, cache_read) per-token prices, or None if unknown."""
    return _longest_prefix(PRICES, model)


def cost_for(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write: int,
    cache_read: int,
) -> float | None:
    """Return USD cost for the given token counts, or None for an unknown model."""
    prices = price_for(model)
    if prices is None:
        return None
    p_in, p_out, p_cw, p_cr = prices
    return (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_write * p_cw
        + cache_read * p_cr
    )


def embed_cost_for(model: str, tokens: int) -> float | None:
    """Return USD cost for embedding `tokens` tokens, or None for an unknown model."""
    price = _longest_prefix(EMBED_PRICES, model)
    if price is None:
        return None
    return tokens * price


# --- Change tracking: cost-relevant settings and population ---

TRACKED_SETTINGS: tuple[str, ...] = (
    "claude_model",
    "repost_mode",
    "repost_threshold",
    "batch_flush_size",
    "batch_flush_seconds",
    "voyage_model",
    "repost_dimensions",
    "default_score_threshold",
)

_POP_REL_THRESHOLD = 0.20
_POP_ABS_THRESHOLD = 2


async def record_setting_changes(conn, settings) -> int:
    """Diff tracked settings against the stored snapshot; write 'setting' events.

    A run is "first" when no tracked key is stored yet (other keys such as
    digest_last_sent may share the table). First runs only save the snapshot.
    Returns the number of events written.
    """
    # Lazy import: database.py imports this module, so a top-level import would cycle.
    from ahsoka import database as db

    current = {k: str(getattr(settings, k)) for k in TRACKED_SETTINGS}
    stored = await db.get_setting_snapshot(conn) or {}
    is_first = not any(k in stored for k in TRACKED_SETTINGS)

    written = 0
    if not is_first:
        for k in TRACKED_SETTINGS:
            old = stored.get(k)
            if old != current[k]:
                await db.add_cost_event(conn, "setting", k, old, current[k])
                written += 1
    await db.save_setting_snapshot(conn, current)
    return written


async def record_population(conn, day: str, users: int, channels: int, keywords: int) -> int:
    """Write 'population' events for metrics that moved >=20% and >=2 absolute.

    Always saves the new snapshot; the first snapshot writes no events.
    """
    from ahsoka import database as db  # lazy: avoids database <-> costs import cycle

    new = {"users": users, "channels": channels, "keywords": keywords}
    last = await db.get_last_population_snapshot(conn)
    written = 0
    if last is not None:
        for key, value in new.items():
            old = last[key]
            diff = abs(value - old)
            if diff < _POP_ABS_THRESHOLD:
                continue
            if old == 0 or diff / old >= _POP_REL_THRESHOLD:
                await db.add_cost_event(conn, "population", key, str(old), str(value))
                written += 1
    await db.save_population_snapshot(conn, day, users, channels, keywords)
    return written


async def collect_population(conn, keyword_index, watched_channels) -> tuple[int, int, int]:
    """Return (non-banned users, watched channels, union keywords)."""
    from ahsoka import database as db  # lazy: avoids database <-> costs import cycle

    users = [u for u in await db.list_users(conn) if not u.is_banned]
    return len(users), len(watched_channels), keyword_index.keyword_count
