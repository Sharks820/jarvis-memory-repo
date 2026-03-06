"""Shared constants for the Jarvis engine.

Centralises values that were previously duplicated across multiple modules.
"""

from __future__ import annotations

import os
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


def is_privacy_sensitive(text: str) -> bool:
    """Return *True* if *text* contains any privacy keyword."""
    lower = text.lower()
    return any(kw in lower for kw in PRIVACY_KEYWORDS)


# ---------------------------------------------------------------------------
# Default local (Ollama) model
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_MODEL = "gemma3:4b"


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
    ("GROQ_API_KEY", "kimi-k2"),
    ("MISTRAL_API_KEY", "devstral-2"),
    ("ZAI_API_KEY", "glm-4.7-flash"),
]


# ---------------------------------------------------------------------------
# Runtime data filenames (used with runtime_dir())
# ---------------------------------------------------------------------------

SELF_TEST_HISTORY = "self_test_history.jsonl"
GATEWAY_AUDIT_LOG = "gateway_audit.jsonl"
KG_METRICS_LOG = "kg_metrics.jsonl"


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

def make_task_id(prefix: str) -> str:
    """Generate a timestamped task ID like ``prefix-20260305143000``."""
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
