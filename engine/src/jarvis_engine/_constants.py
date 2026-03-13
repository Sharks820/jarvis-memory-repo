"""Shared constants for the Jarvis engine.

Centralises values that were previously duplicated across multiple modules.

NOTE: Utility *functions* live in ``_shared.py`` -- this module contains only
plain data constants (strings, ints, frozensets, lists).
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "PRIVACY_KEYWORDS",
    "DEFAULT_LOCAL_MODEL",
    "FAST_LOCAL_MODEL",
    "DEFAULT_CLOUD_MODEL",
    "EMBEDDING_DIM",
    "STOP_WORDS",
    "DEFAULT_API_PORT",
    "ENV_MODEL_PRIORITY",
    "SELF_TEST_HISTORY",
    "GATEWAY_AUDIT_LOG",
    "KG_METRICS_LOG",
    "OPS_SNAPSHOT_FILENAME",
    "ACTIONS_FILENAME",
    "SUBSYSTEM_ERRORS",
    "SUBSYSTEM_ERRORS_DB",
    "REPLAY_WINDOW_SECONDS",
    "MAX_NONCES",
    "MAX_AUTH_BODY_SIZE",
    "MAX_COMMAND_TEXT_CHARS",
    "MAX_COMMAND_STDOUT_TAIL_LINES",
    "MAX_COMMAND_STDOUT_LINE_CHARS",
    "MAX_COMMAND_RESPONSE_CHARS",
    "MAX_COMMAND_RESPONSE_CHUNK_CHARS",
    "MAX_COMMAND_RESPONSE_CHUNKS",
]

# Privacy keywords -- used by IntentClassifier and manual fallback routing
# to ensure private queries never leave the local device.

PRIVACY_KEYWORDS: frozenset[str] = frozenset(
    {
        # Identity / contact
        "address",
        "phone number",
        "social security",
        "ssn",
        # Financial
        "account",
        "bank",
        "bank account",
        "bill",
        "bills",
        "credit card",
        "credential",
        "income",
        "insurance",
        "payment",
        "pin",
        "salary",
        # Medical / health
        "allergy",
        "blood type",
        "diagnosis",
        "doctor",
        "health",
        "medical",
        "medication",
        "medications",
        "medicine",
        "pill",
        "prescription",
        "surgery",
        "symptom",
        "therapist",
        "therapy",
        "treatment",
        # Family / personal
        "appointment",
        "calendar",
        "daughter",
        "family",
        "husband",
        "son",
        "wife",
        # Auth / secrets
        "confidential",
        "password",
        "personal",
        "private",
        "secret",
        # Sensitive content
        "affair",
        "drug",
        "naked",
        "nude",
        "porn",
        "sex",
    }
)


# Model constants

DEFAULT_LOCAL_MODEL = "qwen3.5:latest"
FAST_LOCAL_MODEL = "qwen3.5:4b"
DEFAULT_CLOUD_MODEL = "kimi-k2"
EMBEDDING_DIM: int = 768


# Stop words -- superset used for keyword/topic extraction and cross-branch
# matching.  Individual modules may extend with ``STOP_WORDS | {...}``.

STOP_WORDS: frozenset[str] = frozenset(
    {
        # Articles / determiners
        "the",
        "a",
        "an",
        # Be / auxiliary
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        # Modals
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        # Prepositions
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "from",
        "by",
        "about",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        # Conjunctions / negation
        "and",
        "but",
        "or",
        "nor",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        # Adverbs / misc
        "too",
        "very",
        "just",
        "also",
        "only",
        # Pronouns / possessives
        "that",
        "this",
        "it",
        "its",
        "my",
        "me",
        "i",
        "your",
        "his",
        "her",
        "our",
        "their",
        "they",
        "them",
        "there",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        # Quantifiers
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "own",
        "same",
        # Adjectives / misc
        "new",
        "old",
        "true",
        "false",
        "none",
        "null",
        "yes",
        # Project-specific
        "conner",
        "jarvis",
    }
)


# Network and API constants

DEFAULT_API_PORT: int = 8787

ENV_MODEL_PRIORITY: list[tuple[str, str]] = [
    ("GROQ_API_KEY", DEFAULT_CLOUD_MODEL),
    ("MISTRAL_API_KEY", "devstral-2"),
    ("ZAI_API_KEY", "glm-4.7-flash"),
]

# Security -- PBKDF2 password hashing
PBKDF2_ITERATIONS: int = 600_000
PBKDF2_SALT_LEN: int = 32  # 256-bit random salt per password

# Runtime data filenames (used with runtime_dir())

SELF_TEST_HISTORY = "self_test_history.jsonl"
GATEWAY_AUDIT_LOG = "gateway_audit.jsonl"
KG_METRICS_LOG = "kg_metrics.jsonl"
OPS_SNAPSHOT_FILENAME = "ops_snapshot.live.json"
ACTIONS_FILENAME = "actions.generated.json"

# Broad exception tuple for subsystem catch-all handlers.
# Modules that lazily import subsystems should catch these so a single broken
# subsystem does not crash the daemon loop, mobile API, or CLI surface.
# Includes KeyError and AttributeError since subsystem dict-lookups and
# optional-attribute access are common failure modes alongside the core five.
SUBSYSTEM_ERRORS: tuple[type[Exception], ...] = (
    ImportError,
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
    KeyError,
    AttributeError,
)

# Extended subsystem errors including sqlite3.Error for DB-touching subsystems.
SUBSYSTEM_ERRORS_DB: tuple[type[Exception], ...] = SUBSYSTEM_ERRORS + (sqlite3.Error,)

# Mobile API / voice command limits — shared between mobile_api.py and
# mobile_routes/voice.py to avoid duplicate definitions.
REPLAY_WINDOW_SECONDS: float = 120.0
MAX_NONCES: int = 100_000
MAX_AUTH_BODY_SIZE: int = 2_000_000  # 2 MB (matches sync/push max_content_length)
MAX_COMMAND_TEXT_CHARS: int = 2000
MAX_COMMAND_STDOUT_TAIL_LINES: int = 30
MAX_COMMAND_STDOUT_LINE_CHARS: int = 1200
MAX_COMMAND_RESPONSE_CHARS: int = 12_000
MAX_COMMAND_RESPONSE_CHUNK_CHARS: int = 800
MAX_COMMAND_RESPONSE_CHUNKS: int = 24
