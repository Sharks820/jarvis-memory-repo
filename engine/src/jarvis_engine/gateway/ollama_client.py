"""Low-level Ollama ``/api/generate`` client.

Moved from ``_shared.py`` — this is domain-specific gateway code that
belongs alongside the other LLM provider integrations.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict
from urllib.request import Request, urlopen

__all__ = ["OllamaResponse", "call_ollama_generate"]

logger = logging.getLogger(__name__)


class OllamaResponse(TypedDict, total=False):
    """Shape returned by Ollama's ``/api/generate`` endpoint."""

    model: str
    response: str
    done: bool
    context: list[int]
    total_duration: int
    load_duration: int
    prompt_eval_count: int
    prompt_eval_duration: int
    eval_count: int
    eval_duration: int


def call_ollama_generate(
    endpoint: str,
    model: str,
    prompt: str,
    options: dict[str, Any],
    *,
    timeout_s: int = 120,
) -> OllamaResponse:
    """Send a non-streaming generate request to Ollama's ``/api/generate``.

    Args:
        endpoint: Ollama base URL (e.g. ``http://localhost:11434``).
        model: Model name (e.g. ``qwen3:14b``).
        prompt: The text prompt to send.
        options: Ollama options dict (num_ctx, num_predict, temperature, etc.).
        timeout_s: HTTP timeout in seconds.

    Returns:
        The parsed JSON response dict from Ollama.

    Raises:
        ValueError: If the endpoint fails the safety check or the response
            is not a JSON object.
        urllib.error.URLError: On network errors.
        TimeoutError: On request timeout.
    """
    from jarvis_engine.security.net_policy import is_safe_ollama_endpoint

    if not is_safe_ollama_endpoint(endpoint):
        raise ValueError(f"Unsafe Ollama endpoint: {endpoint}")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    req = Request(
        url=f"{endpoint.rstrip('/')}/api/generate",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with urlopen(req, timeout=timeout_s) as resp:  # nosec B310
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from Ollama")
    return data
