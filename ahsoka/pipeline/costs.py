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
