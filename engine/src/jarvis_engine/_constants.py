"""Shared constants for the Jarvis engine.

Centralises values that were previously duplicated across multiple modules.
"""

from __future__ import annotations

__all__ = [
    "PRIVACY_KEYWORDS",
    "is_privacy_sensitive",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_CLOUD_MODEL",
    "EMBEDDING_DIM",
    "get_local_model",
    "STOP_WORDS",
    "DEFAULT_API_PORT",
    "ENV_MODEL_PRIORITY",
    "SELF_TEST_HISTORY",
    "GATEWAY_AUDIT_LOG",
    "KG_METRICS_LOG",
    "OPS_SNAPSHOT_FILENAME",
    "ACTIONS_FILENAME",
    "memory_db_path",
    "runtime_dir",
    "extract_keywords",
    "make_task_id",
    "recency_weight",
]

import os
import re
from datetime import datetime
from pathlib import Path

from jarvis_engine._compat import UTC

# ---------------------------------------------------------------------------
# Privacy keywords — used by IntentClassifier and manual fallback routing
# to ensure private queries never leave the local device.
# ---------------------------------------------------------------------------

PRIVACY_KEYWORDS: frozenset[str] = frozenset({
    # Identity / contact
    "address", "phone number", "social security", "ssn",
    # Financial
    "account", "bank", "bank account", "bill", "bills", "credit card",
    "credential", "income", "insurance", "payment", "pin", "salary",
    # Medical / health
    "allergy", "blood type", "diagnosis", "doctor", "health", "medical",
    "medication", "medications", "medicine", "pill", "prescription",
    "surgery", "symptom", "therapist", "therapy", "treatment",
    # Family / personal
    "appointment", "calendar", "daughter", "family", "husband", "son", "wife",
    # Auth / secrets
    "confidential", "password", "personal", "private", "secret",
    # Sensitive content
    "affair", "drug", "naked", "nude", "porn", "sex",
})


_PRIVACY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in sorted(PRIVACY_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def is_privacy_sensitive(text: str) -> bool:
    """Return *True* if *text* contains any privacy keyword (word-boundary match)."""
    return bool(_PRIVACY_RE.search(text))


# ---------------------------------------------------------------------------
# Default local (Ollama) model
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_MODEL = "gemma3:4b"

# ---------------------------------------------------------------------------
# Default cloud model (API-based, used by gateway/classifier/handlers)
# ---------------------------------------------------------------------------

DEFAULT_CLOUD_MODEL = "kimi-k2"

# ---------------------------------------------------------------------------
# Embedding dimension — nomic-ai/nomic-embed-text-v1.5 produces 768-dim vectors.
# Used by memory/engine.py, knowledge/graph.py, and KG sub-managers.
# ---------------------------------------------------------------------------

EMBEDDING_DIM: int = 768


def get_local_model() -> str:
    """Return the configured local Ollama model name."""
    return os.environ.get("JARVIS_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)


# ---------------------------------------------------------------------------
# Stop words — superset used for keyword/topic extraction and cross-branch
# matching.  Individual modules may extend with ``STOP_WORDS | {...}``.
# ---------------------------------------------------------------------------

STOP_WORDS: frozenset[str] = frozenset({
    # Articles / determiners
    "the", "a", "an",
    # Be / auxiliary
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    # Modals
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "need", "must",
    # Prepositions
    "of", "in", "to", "for", "with", "on", "at", "from", "by", "about",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between",
    # Conjunctions / negation
    "and", "but", "or", "nor", "not", "no", "so", "if", "then", "than",
    # Adverbs / misc
    "too", "very", "just", "also", "only",
    # Pronouns / possessives
    "that", "this", "it", "its", "my", "me", "i", "your", "his", "her",
    "our", "their", "they", "them", "there", "what", "which", "who", "whom",
    "how", "when", "where", "why",
    # Quantifiers
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "own", "same",
    # Adjectives / misc
    "new", "old", "true", "false", "none", "null", "yes",
    # Project-specific
    "conner", "jarvis",
})


# ---------------------------------------------------------------------------
# Default Mobile API port
# ---------------------------------------------------------------------------

DEFAULT_API_PORT: int = 8787


# ---------------------------------------------------------------------------
# Cloud model env-key fallback priority
# ---------------------------------------------------------------------------

ENV_MODEL_PRIORITY: list[tuple[str, str]] = [
    ("GROQ_API_KEY", DEFAULT_CLOUD_MODEL),
    ("MISTRAL_API_KEY", "devstral-2"),
    ("ZAI_API_KEY", "glm-4.7-flash"),
]


# ---------------------------------------------------------------------------
# Runtime data filenames (used with runtime_dir())
# ---------------------------------------------------------------------------

SELF_TEST_HISTORY = "self_test_history.jsonl"
GATEWAY_AUDIT_LOG = "gateway_audit.jsonl"
KG_METRICS_LOG = "kg_metrics.jsonl"
OPS_SNAPSHOT_FILENAME = "ops_snapshot.live.json"
ACTIONS_FILENAME = "actions.generated.json"


# ---------------------------------------------------------------------------
# Common path helpers
# ---------------------------------------------------------------------------

def memory_db_path(root: Path) -> Path:
    """Return the canonical path to the main Jarvis memory database."""
    return root / ".planning" / "brain" / "jarvis_memory.db"


def runtime_dir(root: Path) -> Path:
    """Return the canonical path to the runtime data directory."""
    return root / ".planning" / "runtime"


# ---------------------------------------------------------------------------
# Task ID generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Keyword extraction — shared core for pulling meaningful words from text.
# Used by learning/correction_detector.py and learning/cross_branch.py.
# ---------------------------------------------------------------------------

def extract_keywords(
    text: str,
    *,
    stop_words: frozenset[str] | None = None,
    min_length: int = 4,
    pattern: str = r"[a-zA-Z]+",
    deduplicate: bool = True,
) -> list[str]:
    """Extract meaningful keywords from *text*.

    Args:
        text: Input text to extract keywords from.
        stop_words: Words to filter out.  Defaults to :data:`STOP_WORDS`.
        min_length: Minimum word length to keep (inclusive).
        pattern: Regex pattern for tokenization (default: alpha-only).
        deduplicate: If True, remove duplicate keywords while preserving order.
    """
    if not text:
        return []

    import re as _re

    if stop_words is None:
        stop_words = STOP_WORDS

    words = _re.findall(pattern, text.lower())
    keywords = [w for w in words if len(w) >= min_length and w not in stop_words]

    if deduplicate:
        seen: set[str] = set()
        unique: list[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique.append(kw)
        return unique

    return keywords


def make_task_id(prefix: str) -> str:
    """Generate a timestamped task ID like ``prefix-20260305143000``."""
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"


# ---------------------------------------------------------------------------
# Recency weight helper — shared core for exponential time-decay scoring.
# Used by memory/search.py (default=0.0, decay=168h) and brain_memory.py
# (default=0.3, decay=96h).
# ---------------------------------------------------------------------------

def recency_weight(
    ts_text: str,
    *,
    default: float = 0.0,
    decay_hours: float = 168.0,
) -> float:
    """Compute exponential recency decay for a timestamp string.

    Returns a value between 0.0 and 1.0 for valid timestamps (1.0 = just
    created, decaying toward 0.0 with a half-life of approximately
    *decay_hours* hours).  Returns *default* for empty or unparseable input.

    Args:
        ts_text: ISO-8601 timestamp string (may end with ``Z``).
        default: Value returned for empty or invalid timestamps.
        decay_hours: Exponential decay constant in hours (default 168 = 7 days).
    """
    import math

    raw = str(ts_text).strip()
    if not raw:
        return default
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta_hours = max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600.0)
    return math.exp(-delta_hours / decay_hours)
