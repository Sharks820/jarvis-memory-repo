"""Memory hygiene — intelligent signal extraction and cleanup.

Classifies memory records into quality tiers (high_signal, contextual,
ephemeral, junk) and provides automated cleanup with anti-loss guardrails.

Quality tiers:
- high_signal: personal facts, commitments, decisions, learning outcomes
- contextual: conversation context, task details, intermediate reasoning
- ephemeral: greetings, small talk, acknowledgments, repeated content
- junk: empty/garbled entries, hallucination artifacts, test data
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso, parse_iso_timestamp

logger = logging.getLogger(__name__)

# Quality tiers

QUALITY_TIERS = ("high_signal", "contextual", "ephemeral", "junk")

# Keyword patterns for rule-based classification (Pass 1)

_HIGH_SIGNAL_KEYWORDS = re.compile(
    r"\b("
    r"medication|prescription|allergy|diagnosis|doctor|hospital|medical|health|"
    r"payment|salary|investment|account|budget|finance|bank|credit|"
    r"password|credential|token|secret|signing|security|"
    r"remember|important|don't forget|never forget|critical|"
    r"meeting|appointment|deadline|due date|schedule|calendar|"
    r"birthday|anniversary|wedding|funeral|"
    r"address|phone number|social security|passport|"
    r"decision|decided|committed|agreed|promise"
    r")\b",
    re.IGNORECASE,
)

_EPHEMERAL_KEYWORDS = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|"
    r"good morning|good night|bye|goodbye|see you|"
    r"got it|understood|alright|cool|nice|great|"
    r"how are you|what's up|no problem|you're welcome)[\.\!\?]?$",
    re.IGNORECASE,
)

_JUNK_PATTERNS = [
    re.compile(r"^[\s\W]*$"),  # whitespace/punctuation only
    re.compile(r"^(.)\1{10,}$"),  # repeated single character
    re.compile(r"test_?\d*|unittest|pytest|mock", re.IGNORECASE),  # test data
    re.compile(r"lorem ipsum", re.IGNORECASE),  # placeholder text
]

_GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "greetings",
    "howdy",
    "sup",
    "morning",
    "evening",
    "night",
}


# Classification result


class ClassificationResult(TypedDict):
    """Result of classifying a single memory record."""

    record_id: str
    quality: str  # one of QUALITY_TIERS
    confidence: float  # 0.0 - 1.0
    reason: str  # human-readable explanation


# Cleanup result


@dataclass
class HygieneReport:
    """Summary of a memory hygiene run."""

    scanned: int = 0
    classified: int = 0
    distribution: dict[str, int] = field(
        default_factory=lambda: {
            "high_signal": 0,
            "contextual": 0,
            "ephemeral": 0,
            "junk": 0,
        }
    )
    cleanup_candidates: int = 0
    archived: int = 0
    protected: int = 0
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_now_iso)


# Rule-based classifier (Pass 1)


def classify_record(record: dict[str, Any]) -> ClassificationResult:
    """Classify a memory record into a quality tier using rule-based heuristics.

    Pass 1: fast, deterministic, no LLM calls.
    """
    record_id = str(record.get("record_id", ""))
    summary = str(record.get("summary", ""))
    content = summary  # summary IS the searchable content
    tags_raw = record.get("tags", "[]")
    try:
        confidence_val = float(record.get("confidence", 0.72) or 0.72)
    except (TypeError, ValueError):
        confidence_val = 0.72

    # --- Junk detection ---
    if not content.strip():
        return {
            "record_id": record_id,
            "quality": "junk",
            "confidence": 0.95,
            "reason": "empty content",
        }

    for pattern in _JUNK_PATTERNS:
        if pattern.search(content):
            return {
                "record_id": record_id,
                "quality": "junk",
                "confidence": 0.85,
                "reason": f"matches junk pattern: {pattern.pattern[:40]}",
            }

    # --- Ephemeral detection (before length check — "ok", "hi" are ephemeral, not junk) ---
    if _EPHEMERAL_KEYWORDS.match(content.strip()):
        return {
            "record_id": record_id,
            "quality": "ephemeral",
            "confidence": 0.90,
            "reason": "greeting/acknowledgment",
        }

    words = content.split()
    if len(words) <= 3 and any(w.lower() in _GREETING_WORDS for w in words):
        return {
            "record_id": record_id,
            "quality": "ephemeral",
            "confidence": 0.80,
            "reason": "short greeting",
        }

    if len(content) < 5:
        return {
            "record_id": record_id,
            "quality": "junk",
            "confidence": 0.80,
            "reason": "content too short (<5 chars)",
        }

    if len(content) < 20 and confidence_val < 0.6:
        return {
            "record_id": record_id,
            "quality": "ephemeral",
            "confidence": 0.70,
            "reason": "short low-confidence content",
        }

    # --- High signal detection ---
    high_matches = _HIGH_SIGNAL_KEYWORDS.findall(content)
    if len(high_matches) >= 2:
        return {
            "record_id": record_id,
            "quality": "high_signal",
            "confidence": 0.90,
            "reason": f"multiple high-signal keywords: {', '.join(high_matches[:3])}",
        }

    if high_matches:
        return {
            "record_id": record_id,
            "quality": "high_signal",
            "confidence": 0.75,
            "reason": f"high-signal keyword: {high_matches[0]}",
        }

    if confidence_val >= 0.90:
        return {
            "record_id": record_id,
            "quality": "high_signal",
            "confidence": 0.70,
            "reason": "high confidence score",
        }

    # --- User-pinned detection ---
    if isinstance(tags_raw, str):
        if "user_pinned" in tags_raw or "remember" in tags_raw.lower():
            return {
                "record_id": record_id,
                "quality": "high_signal",
                "confidence": 0.95,
                "reason": "user-pinned content",
            }

    # --- Default: contextual ---
    return {
        "record_id": record_id,
        "quality": "contextual",
        "confidence": 0.60,
        "reason": "default classification",
    }


# Protection checks (anti-loss guardrails)


def is_protected(
    record: dict[str, Any],
    *,
    kg_fact_ids: set[str] | None = None,
    active_mission_ids: set[str] | None = None,
    anchor_entity_ids: set[str] | None = None,
    min_age_days: int = 7,
    min_cross_refs: int = 3,
) -> tuple[bool, str]:
    """Check if a record is protected from cleanup.

    Returns (is_protected, reason).
    """
    record_id = str(record.get("record_id", ""))

    # Never clean high_signal records
    quality = str(record.get("signal_quality", record.get("quality", "")))
    if quality == "high_signal":
        return True, "high_signal classification"

    # Records in knowledge graph
    if kg_fact_ids and record_id in kg_fact_ids:
        return True, "referenced in knowledge graph"

    # Active mission context
    if active_mission_ids:
        task_id = str(record.get("task_id", ""))
        if task_id and task_id in active_mission_ids:
            return True, "active mission context"

    # Anchor entities
    if anchor_entity_ids and record_id in anchor_entity_ids:
        return True, "referenced as anchor entity"

    # User-pinned
    tags = str(record.get("tags", "[]"))
    if "user_pinned" in tags:
        return True, "user-pinned"

    # High cross-reference count
    try:
        access_count = int(record.get("access_count", 0) or 0)
    except (TypeError, ValueError):
        access_count = 0
    if access_count >= min_cross_refs:
        return True, f"high access count ({access_count})"

    # Cooling period
    ts = str(record.get("ts", ""))
    if ts:
        record_time = parse_iso_timestamp(ts)
        if record_time is not None:
            age = datetime.now(UTC) - record_time
            if age < timedelta(days=min_age_days):
                return True, f"within {min_age_days}-day cooling period"

    return False, ""


# Memory Hygiene Engine


class MemoryHygieneEngine:
    """Scans, classifies, and cleans memory records."""

    # Auto-archive thresholds (days)
    JUNK_ARCHIVE_DAYS = 3
    EPHEMERAL_ARCHIVE_DAYS = 14
    CONTEXTUAL_ARCHIVE_DAYS = 60

    def __init__(self, root: Path) -> None:
        self._root = root

    def scan_and_classify(
        self,
        engine: Any,
        *,
        limit: int = 0,
    ) -> list[ClassificationResult]:
        """Scan all records and classify them.

        Args:
            engine: MemoryEngine instance
            limit: Max records to scan (0 = all)

        Returns:
            List of classification results.
        """
        try:
            records = engine.get_all_records_for_tier_maintenance()
        except (RuntimeError, OSError, sqlite3.Error) as exc:
            logger.warning("Failed to fetch records for hygiene scan: %s", exc)
            return []

        if limit > 0:
            records = records[:limit]

        results: list[ClassificationResult] = []
        for record in records:
            try:
                # Need full record for classification — fetch summary
                full = engine.get_record(record["record_id"])
                if full is None:
                    continue
                results.append(classify_record(full))
            except (KeyError, TypeError, RuntimeError) as exc:
                logger.debug(
                    "Failed to classify record %s: %s",
                    record.get("record_id", "?"),
                    exc,
                )

        return results

    def identify_cleanup_candidates(
        self,
        classifications: list[ClassificationResult],
        records_by_id: dict[str, dict[str, Any]],
        *,
        kg_fact_ids: set[str] | None = None,
        active_mission_ids: set[str] | None = None,
        anchor_entity_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Identify records that are candidates for cleanup.

        Returns list of dicts with record_id, quality, reason, and age_days.
        """
        candidates: list[dict[str, Any]] = []
        now = datetime.now(UTC)

        for cls in classifications:
            quality = cls["quality"]
            if quality not in ("junk", "ephemeral"):
                continue

            record_id = cls["record_id"]
            record = records_by_id.get(record_id)
            if record is None:
                continue

            # Check protection
            protected, reason = is_protected(
                record,
                kg_fact_ids=kg_fact_ids,
                active_mission_ids=active_mission_ids,
                anchor_entity_ids=anchor_entity_ids,
            )
            if protected:
                continue

            # Check age threshold
            ts = str(record.get("ts", ""))
            age_days = 0.0
            if ts:
                record_time = parse_iso_timestamp(ts)
                if record_time is not None:
                    age_days = (now - record_time).total_seconds() / 86400.0

            threshold = (
                self.JUNK_ARCHIVE_DAYS
                if quality == "junk"
                else self.EPHEMERAL_ARCHIVE_DAYS
            )

            if age_days >= threshold:
                candidates.append(
                    {
                        "record_id": record_id,
                        "quality": quality,
                        "reason": cls["reason"],
                        "age_days": round(age_days, 1),
                    }
                )

        return candidates

    def run_cleanup(
        self,
        engine: Any,
        *,
        dry_run: bool = False,
        kg_fact_ids: set[str] | None = None,
        active_mission_ids: set[str] | None = None,
        anchor_entity_ids: set[str] | None = None,
    ) -> HygieneReport:
        """Run a full hygiene scan and cleanup cycle.

        Args:
            engine: MemoryEngine instance
            dry_run: If True, don't actually delete anything
            kg_fact_ids: Record IDs referenced by knowledge graph facts
            active_mission_ids: Task IDs of active missions
            anchor_entity_ids: Record IDs referenced as anchor entities
        """
        report = HygieneReport()

        # Step 1: Scan and classify
        classifications = self.scan_and_classify(engine)
        report.scanned = len(classifications)
        report.classified = len(classifications)

        # Build distribution
        for cls in classifications:
            q = cls["quality"]
            if q in report.distribution:
                report.distribution[q] += 1

        # Step 2: Build records lookup for candidate checking
        record_ids = [
            c["record_id"]
            for c in classifications
            if c["quality"] in ("junk", "ephemeral")
        ]
        if not record_ids:
            return report

        try:
            records_list = engine.get_records_batch(record_ids)
        except (RuntimeError, OSError, sqlite3.Error) as exc:
            report.errors.append(f"Failed to fetch records: {exc}")
            return report

        records_by_id = {r["record_id"]: r for r in records_list}

        # Step 3: Identify candidates
        candidates = self.identify_cleanup_candidates(
            classifications,
            records_by_id,
            kg_fact_ids=kg_fact_ids,
            active_mission_ids=active_mission_ids,
            anchor_entity_ids=anchor_entity_ids,
        )
        report.cleanup_candidates = len(candidates)
        report.protected = len(record_ids) - len(candidates)

        # Step 4: Archive (delete) candidates
        if not dry_run and candidates:
            ids_to_delete = [c["record_id"] for c in candidates]
            try:
                deleted = engine.delete_records_batch(ids_to_delete)
                report.archived = deleted
            except (RuntimeError, OSError, ValueError) as exc:
                report.errors.append(f"Cleanup failed: {exc}")

        return report


# CQRS command support


def hygiene_dashboard_metrics(root: Path) -> dict[str, Any]:
    """Build hygiene metrics for the intelligence dashboard.

    Returns safe dict (never raises).
    """
    try:
        from jarvis_engine._shared import runtime_dir
        from jarvis_engine._shared import load_jsonl_tail

        history_path = runtime_dir(root) / "hygiene_history.jsonl"
        tail = load_jsonl_tail(history_path, limit=1)
        if tail:
            latest = tail[0]
            return {
                "last_scan_utc": latest.get("timestamp", ""),
                "distribution": latest.get("distribution", {}),
                "cleanup_candidates": latest.get("cleanup_candidates", 0),
                "archived": latest.get("archived", 0),
                "protected": latest.get("protected", 0),
            }
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug("Hygiene metrics unavailable: %s", exc)

    return {}
