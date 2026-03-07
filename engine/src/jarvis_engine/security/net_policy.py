"""Shared network endpoint safety policy helpers."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def is_safe_ollama_endpoint(endpoint: str) -> bool:
    """Allow only local Ollama endpoints unless explicitly overridden."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    allow_nonlocal = os.getenv(
        "JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT",
        "",
    ).strip().lower() in {"1", "true", "yes"}
    if allow_nonlocal:
        return True

    return host in {"127.0.0.1", "localhost", "::1"}
