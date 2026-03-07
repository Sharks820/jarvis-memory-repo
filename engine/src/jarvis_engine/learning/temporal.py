"""Temporal metadata for knowledge graph facts.

Classifies facts as permanent, time-sensitive, or unknown based on node_id
prefix heuristics and date-pattern detection in labels.  Provides schema
migration for adding temporal columns and a batch job for flagging expired facts.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from jarvis_engine._compat import UTC

logger = logging.getLogger(__name__)

# Node-ID prefixes that indicate permanent facts (never expire).
_PERMANENT_PREFIXES = (
    "family.member",
    "preference",
    "ops.location",
    "finance.income",
)

# Node-ID prefixes that indicate time-sensitive facts (may expire).
_TIME_SENSITIVE_PREFIXES = (
    "ops.schedule",
    "health.medication",
)

# Regex for extracting date-like patterns from labels.
_DATE_PATTERN = re.compile(
    r"(?:expires?|due|until|today|tomorrow)\s*"
    r"(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Fallback: just an ISO date anywhere in the label.
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def migrate_temporal_metadata(
    db: sqlite3.Connection,
    write_lock: threading.Lock,
) -> None:
    """Idempotent ALTER TABLE to add temporal_type and expires_at to kg_nodes.

    Safe to call multiple times -- checks for column existence first.
    """
    with write_lock:
        # Check existing columns
        cursor = db.execute("PRAGMA table_info(kg_nodes)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        if "temporal_type" not in existing_cols:
            db.execute(
                "ALTER TABLE kg_nodes ADD COLUMN temporal_type TEXT DEFAULT 'unknown'"
            )

        if "expires_at" not in existing_cols:
            db.execute("ALTER TABLE kg_nodes ADD COLUMN expires_at TEXT DEFAULT NULL")

        # Create indexes (IF NOT EXISTS is idempotent)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_temporal_type "
            "ON kg_nodes(temporal_type)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_expires_at ON kg_nodes(expires_at)"
        )
        db.commit()


def classify_temporal(node_id: str, label: str) -> tuple[str, str | None]:
    """Classify a fact's temporal nature from its node_id prefix and label text.

    Returns:
        (temporal_type, expires_at_iso_or_none)
        temporal_type is one of: 'permanent', 'time_sensitive', 'unknown'
    """
    lower_id = node_id.lower()

    # Check permanent prefixes
    for prefix in _PERMANENT_PREFIXES:
        if lower_id.startswith(prefix):
            return ("permanent", None)

    # Check time-sensitive prefixes
    for prefix in _TIME_SENSITIVE_PREFIXES:
        if lower_id.startswith(prefix):
            # Try to extract a date from the label
            expires_at = _extract_date(label)
            if expires_at is None:
                # Default: 30 days from now for time-sensitive without explicit date
                expires_at = (datetime.now(UTC) + timedelta(days=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            return ("time_sensitive", expires_at)

    # Check for temporal date patterns in the label itself
    expires_at = _extract_date(label)
    if expires_at is not None:
        return ("time_sensitive", expires_at)

    return ("unknown", None)


def _extract_date(label: str) -> str | None:
    """Extract an expiration date from a label string.

    Looks for patterns like 'expires 2026-03-01' or 'due 2026-04-15'.
    Returns ISO datetime string or None.
    """
    match = _DATE_PATTERN.search(label)
    if match:
        return f"{match.group(1)}T00:00:00Z"
    # Fallback: bare ISO date anywhere in the label
    match = _ISO_DATE_PATTERN.search(label)
    if match:
        return f"{match.group(1)}T00:00:00Z"
    return None


def flag_expired_facts(kg: "object") -> int:
    """Flag facts whose expires_at has passed as 'expired'.

    Args:
        kg: KnowledgeGraph instance (uses .db and .write_lock).

    Returns:
        Number of facts flagged as expired.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = kg.db  # type: ignore[attr-defined]
    write_lock = kg.write_lock  # type: ignore[attr-defined]

    with write_lock:
        cursor = db.execute(
            """UPDATE kg_nodes
               SET temporal_type = 'expired'
               WHERE expires_at IS NOT NULL
                 AND expires_at < ?
                 AND temporal_type != 'expired'""",
            (now,),
        )
        count = cursor.rowcount
        db.commit()

    return count
