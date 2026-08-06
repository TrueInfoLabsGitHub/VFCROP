"""Per-model pricing table + cost calculator.

Prices are USD per 1,000,000 tokens (input, output). Edit here when rates
change — no other file needs to know about pricing. Keys are the canonical
model labels used by providers.py (the "(mock)" suffix is stripped before
lookup so estimated mock costs match what a live run would cost).
"""

# The Gemini and Kimi entries are retained deliberately. Those engines were
# removed on 2026-08-06 and nothing calls them any more, but runs saved under
# them are still in the store, and anything that re-prices a historical run
# needs its rate to exist or the cost silently becomes zero.
PRICING = {
    # label:            (input_per_1M, output_per_1M)
    "Gemini 3 Pro":     (2.00, 12.00),   # verified via OpenRouter
    "Gemini 3.1 Pro":   (2.00, 12.00),   # verified via OpenRouter
    "Gemini 2.5 Pro":   (1.25, 10.00),   # verified via OpenRouter
    "Gemini 2.5 Flash": (0.30, 2.50),
    "GPT-5.5":          (5.00, 30.00),   # verified via OpenRouter (openai/gpt-5.5)
    "GPT-5.2":          (1.75, 14.00),   # verified via OpenRouter (openai/gpt-5.2)
    "Kimi K2.6":        (0.66, 3.41),   # current native-multimodal flagship (kimi-k2.6)
    "Kimi K2.7":        (0.72, 3.49),   # coding-focused variant (kimi-k2.7-code)
    "Kimi K2.5":        (0.60, 2.50),   # prior multimodal generation (kimi-k2.5)
    "Kimi K2":          (0.60, 2.50),   # earlier Kimi K2 generation
    "aggregator":       (0.0, 0.0),   # deterministic node, no model
}


def price_usage(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return the USD cost of one call given its token counts."""
    key = model.replace(" (mock)", "").strip()
    pin, pout = PRICING.get(key, (0.0, 0.0))
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)
