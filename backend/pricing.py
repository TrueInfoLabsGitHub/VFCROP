"""Per-model pricing table + cost calculator.

Prices are USD per 1,000,000 tokens (input, output). Edit here when rates
change — no other file needs to know about pricing. Keys are the canonical
model labels used by providers.py (the "(mock)" suffix is stripped before
lookup so estimated mock costs match what a live run would cost).
"""

# Gemini and Kimi were removed on 2026-08-06 and their stored runs purged, so
# their rates went too. An unknown label prices at 0.0 rather than raising —
# see price_usage — which is the right behaviour for a model we do not bill for,
# and would be the wrong behaviour for one we do. If another engine is ever
# added, add its rate here in the same commit.
PRICING = {
    # label:            (input_per_1M, output_per_1M)
    "GPT-5.5":          (5.00, 30.00),   # verified via OpenRouter (openai/gpt-5.5)
    "GPT-5.2":          (1.75, 14.00),   # verified via OpenRouter (openai/gpt-5.2)
    "aggregator":       (0.0, 0.0),   # deterministic node, no model
}


def price_usage(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return the USD cost of one call given its token counts."""
    key = model.replace(" (mock)", "").strip()
    pin, pout = PRICING.get(key, (0.0, 0.0))
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)
