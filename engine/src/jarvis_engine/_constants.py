"""Shared constants for the Jarvis engine.

Centralises values that were previously duplicated across multiple modules.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Privacy keywords — used by IntentClassifier and manual fallback routing
# to ensure private queries never leave the local device.
# ---------------------------------------------------------------------------

PRIVACY_KEYWORDS: frozenset[str] = frozenset({
    "password", "ssn", "bank", "credit card", "social security",
    "medical", "health", "prescription", "salary", "income",
    "secret", "private", "personal", "confidential", "nude",
    "naked", "sex", "porn", "drug", "affair",
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
