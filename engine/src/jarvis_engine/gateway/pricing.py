"""Static pricing table for LLM models.

Maps model-name prefixes to (input_cost_per_mtok, output_cost_per_mtok).
Local Ollama models cost 0.0. Unknown models default to 0.0.
"""

from __future__ import annotations

# Pricing per million tokens: (input_cost_usd, output_cost_usd)
# Updated Feb 2026 Anthropic pricing
# Longer prefixes must appear first so startswith() matches them before shorter ones
PRICING: dict[str, tuple[float, float]] = {
    # Claude 4.x naming convention
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    # Claude 3.x naming convention (e.g. claude-3-opus-20240229)
    "claude-3-opus": (15.0, 75.0),
    "claude-3.5-sonnet": (3.0, 15.0),
    "claude-3-sonnet": (3.0, 15.0),
    "claude-3.5-haiku": (0.80, 4.0),
    "claude-3-haiku": (0.25, 1.25),
    # Groq (free tier = $0, paid tier below)
    "kimi-k2": (1.0, 3.0),
    "llama-3.3-70b": (0.59, 0.79),
    # Mistral (devstral-2 currently free promo, pricing below for after)
    "devstral-2": (0.40, 2.00),
    "devstral-small-2": (0.10, 0.30),
    # Z.ai GLM (free tier available) — longer prefix first for startswith() matching
    "glm-4.7-flash": (0.14, 0.14),
    "glm-4.7": (0.60, 2.50),
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a completion based on model and token counts.

    Matches the model string against PRICING keys using startswith().
    Returns 0.0 for unrecognized models (e.g. local Ollama models).
    """
    for prefix, (input_rate, output_rate) in PRICING.items():
        if model.startswith(prefix):
            return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return 0.0
