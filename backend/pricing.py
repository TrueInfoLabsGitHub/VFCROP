"""Per-model pricing table + cost calculator.

Prices are USD per 1,000,000 tokens (input, output). Edit here when rates
change — no other file needs to know about pricing. Keys are the canonical
model labels used by providers.py (the "(mock)" suffix is stripped before
lookup so estimated mock costs match what a live run would cost).
"""

PRICING = {
    # label:            (input_per_1M, output_per_1M)
    "Gemini 3 Pro":     (2.00, 12.00),   # verified via OpenRouter
    "Gemini 3.1 Pro":   (2.00, 12.00),   # verified via OpenRouter
    "Gemini 2.5 Pro":   (1.25, 10.00),   # verified via OpenRouter
    "Gemini 2.5 Flash": (0.30, 2.50),
    "GPT-5.5":          (5.00, 30.00),   # verified via OpenRouter (openai/gpt-5.5)
    "GPT-5.2":          (1.75, 14.00),   # verified via OpenRouter (openai/gpt-5.2)
    "aggregator":       (0.0, 0.0),   # deterministic node, no model
}


def price_usage(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return the USD cost of one call given its token counts."""
    key = model.replace(" (mock)", "").strip()
    pin, pout = PRICING.get(key, (0.0, 0.0))
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)
