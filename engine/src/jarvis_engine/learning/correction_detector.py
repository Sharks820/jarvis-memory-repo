"""Detects user corrections in conversation and applies them to the knowledge graph.

Parses natural-language correction patterns (e.g. "no, actually X", "I meant X",
"not X but Y") and optionally updates matching KG facts so the assistant learns
from explicit user feedback.

Thread safety: all KG mutations acquire ``KnowledgeGraph._write_lock``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_engine._constants import STOP_WORDS
from jarvis_engine._shared import extract_keywords as _extract_keywords_core, now_iso
from jarvis_engine.knowledge._base import upsert_fts_kg, delete_fts_kg

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


@dataclass
class Correction:
    """A detected correction from user input."""

    old_claim: str
    new_claim: str
    entity: str | None
    confidence: float


# Compiled correction patterns
# Each entry is (compiled_regex, old_claim_group, new_claim_group).
# Groups are 1-indexed regex group numbers, or None if not captured.

_PATTERNS: list[tuple[re.Pattern[str], int | None, int]] = [
    # "not X, it's Y" / "not X but Y"
    (
        re.compile(
            r"(?:^|[,;.]\s*)not\s+(.+?)\s*[,;]\s*(?:it(?:'s| is)\s+|it\s+is\s+)?(.+)",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    (
        re.compile(
            r"(?:^|[,;.]\s*)not\s+(.+?)\s+but\s+(.+)",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    # "you're confusing X with Y"
    (
        re.compile(
            r"you(?:'re| are)\s+confusing\s+(.+?)\s+with\s+(.+)",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    # "no, actually X" / "no, it's actually X"
    (
        re.compile(
            r"no\s*[,;]\s*(?:it(?:'s| is)\s+)?actually\s+(.+)",
            re.IGNORECASE,
        ),
        None,
        1,
    ),
    # "that's wrong, X" / "that's not right, X"
    (
        re.compile(
            r"that(?:'s| is)\s+(?:wrong|not right)\s*[,;.]\s*(.+)",
            re.IGNORECASE,
        ),
        None,
        1,
    ),
    # "what I meant was X" / "I meant X"
    (
        re.compile(
            r"(?:what\s+)?I\s+meant\s+(?:was\s+)?(.+)",
            re.IGNORECASE,
        ),
        None,
        1,
    ),
    # "correction: X"
    (
        re.compile(
            r"correction\s*:\s*(.+)",
            re.IGNORECASE,
        ),
        None,
        1,
    ),
    # "wrong, X" / "incorrect, X"
    (
        re.compile(
            r"(?:wrong|incorrect)\s*[,;.]\s*(.+)",
            re.IGNORECASE,
        ),
        None,
        1,
    ),
]


class CorrectionDetector:
    """Detects and applies user corrections to the knowledge graph."""

    def __init__(self, kg: KnowledgeGraph | None = None) -> None:
        self._kg = kg

    # Detection

    def detect_correction(self, user_message: str) -> Correction | None:
        """Detect a correction pattern in *user_message*.

        Returns a ``Correction`` dataclass if a pattern matches, else ``None``.
        """
        if not user_message or not user_message.strip():
            return None

        text = user_message.strip()

        for pattern, old_group, new_group in _PATTERNS:
            m = pattern.search(text)
            if m:
                old_claim = m.group(old_group).strip() if old_group else ""
                new_claim = m.group(new_group).strip()
                # Strip trailing punctuation from claims
                new_claim = new_claim.rstrip(".!?")
                if not new_claim:
                    continue  # Stripped to empty — not a meaningful correction
                if old_claim:
                    old_claim = old_claim.rstrip(".!?")
                return Correction(
                    old_claim=old_claim,
                    new_claim=new_claim,
                    entity=None,
                    confidence=0.9,
                )

        return None

    # Application

    def apply_correction(self, correction: Correction) -> bool:
        """Apply *correction* to the knowledge graph.

        Searches the KG for facts matching ``old_claim`` (by label keyword
        overlap), updates the best match's label to ``new_claim``, and boosts
        confidence to ``max(existing + 0.1, 0.9)``.

        If both old and new claims exist as separate nodes, a ``superseded``
        edge is added from old to new.

        Returns ``True`` if the KG was updated, ``False`` otherwise (including
        when no KG is available).
        """
        if self._kg is None:
            return False

        # Require an explicit old_claim to find the fact to correct;
        # without it we risk overwriting an unrelated fact.
        if not correction.old_claim:
            return False
        keywords = _extract_keywords(correction.old_claim)
        if not keywords:
            return False

        # Search for matching facts
        matches = self._kg.query_relevant_facts(keywords, min_confidence=0.0, limit=5)
        if not matches:
            return False

        # Pick the best match -- first result (highest confidence, best keyword overlap)
        best = matches[0]
        node_id = best["node_id"]

        # Pre-capture: find node matching new_claim BEFORE we update the old one
        pre_new_matches: list = []
        if correction.old_claim:
            new_keywords = _extract_keywords(correction.new_claim)
            pre_new_matches = self._kg.query_relevant_facts(
                new_keywords,
                min_confidence=0.0,
                limit=1,
            )

        with self._kg.write_lock:
            try:
                # Read current confidence and lock state
                row = self._kg._db.execute(
                    "SELECT confidence, locked FROM kg_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                if row is None:
                    return False

                existing_confidence = (
                    row[0] if isinstance(row[0], float) else float(row[0])
                )
                is_locked = bool(row[1]) if row[1] is not None else False

                # Respect fact locks -- never modify a locked node
                if is_locked:
                    logger.warning(
                        "Correction skipped: node %s is locked (label=%r)",
                        node_id,
                        correction.new_claim,
                    )
                    return False

                new_confidence = min(max(existing_confidence + 0.1, 0.9), 1.0)

                # Check if a node with new_claim already exists to avoid duplication
                if pre_new_matches:
                    existing_new_id = pre_new_matches[0]["node_id"]
                    if existing_new_id != node_id:
                        merged = self._retract_and_merge(
                            node_id,
                            existing_new_id,
                            new_confidence,
                        )
                        if merged:
                            return True

                # Update the fact label and confidence
                self._update_fts_and_vec(node_id, correction.new_claim, new_confidence)

                self._kg._db.commit()
                # Invalidate NetworkX cache (bypassed add_fact)
                self._kg._mutation_counter += 1
            except (sqlite3.Error, OSError) as exc:
                self._kg._db.rollback()
                logger.debug(
                    "Correction apply transaction failed, rolled back: %s", exc
                )
                raise

        logger.info(
            "Correction applied: node %s updated to %r (confidence %.2f)",
            node_id,
            correction.new_claim,
            new_confidence,
        )

        return True

    # Internal helpers for apply_correction

    def _retract_and_merge(
        self,
        old_node_id: str,
        new_node_id: str,
        new_confidence: float,
    ) -> bool:
        """Retract *old_node_id* and boost *new_node_id* confidence.

        Called when the correction's new claim already exists as a separate
        node.  The old node is retracted (confidence set to 0), its FTS5/vec
        indexes are cleaned up, and a ``superseded`` edge is added for audit.

        Must be called while holding ``self._kg.write_lock``.
        Returns ``True`` on success.
        """
        assert self._kg is not None  # caller guarantees

        existing_new_row = self._kg._db.execute(
            "SELECT confidence FROM kg_nodes WHERE node_id = ?",
            (new_node_id,),
        ).fetchone()
        if existing_new_row is not None:
            existing_new_conf = float(existing_new_row[0])
            merged_confidence = min(max(existing_new_conf, new_confidence), 1.0)
            self._kg._db.execute(
                """UPDATE kg_nodes
                   SET confidence = ?, updated_at = datetime('now')
                   WHERE node_id = ?""",
                (merged_confidence, new_node_id),
            )
        else:
            merged_confidence = new_confidence

        # Retract the old node (set confidence to 0) rather than deleting
        self._kg._db.execute(
            """UPDATE kg_nodes
               SET confidence = 0, updated_at = datetime('now')
               WHERE node_id = ?""",
            (old_node_id,),
        )
        # Clean up FTS5 index for the retracted node
        delete_fts_kg(self._kg._db, old_node_id)
        # Clean up vec index for the retracted node
        if getattr(self._kg, "_vec_available", False):
            try:
                self._kg._db.execute(
                    "DELETE FROM vec_kg_nodes WHERE node_id = ?", (old_node_id,)
                )
            except sqlite3.Error as exc:
                logger.debug(
                    "Vec cleanup for retracted node %s failed: %s", old_node_id, exc
                )

        self._kg._db.commit()
        self._kg._mutation_counter += 1
        logger.info(
            "Correction merged: node %s retracted, existing node %s boosted to %.2f",
            old_node_id,
            new_node_id,
            merged_confidence,
        )
        # Add superseded edge for historical audit trail.
        # NOTE: We are already inside self._kg.write_lock (from apply_correction),
        # so call the raw SQL insert directly instead of add_edge() which would
        # deadlock (threading.Lock is non-reentrant).
        try:
            self._kg._db.execute(
                """INSERT OR IGNORE INTO kg_edges
                   (source_id, target_id, relation, confidence, source_record)
                   VALUES (?, ?, ?, ?, ?)""",
                (old_node_id, new_node_id, "superseded", 1.0, "correction_merge"),
            )
            self._kg._db.commit()
            self._kg._mutation_counter += 1
        except (sqlite3.Error, ValueError) as exc:
            logger.debug("KG audit edge failed: %s", exc)
        return True

    def _update_fts_and_vec(
        self,
        node_id: str,
        new_label: str,
        new_confidence: float,
    ) -> None:
        """Update a node's label, confidence, and FTS5 index.

        Must be called while holding ``self._kg.write_lock``.
        Does NOT commit -- the caller is responsible for committing.
        """
        assert self._kg is not None  # caller guarantees

        self._kg._db.execute(
            """UPDATE kg_nodes
               SET label = ?, confidence = ?, updated_at = datetime('now')
               WHERE node_id = ?""",
            (new_label, new_confidence, node_id),
        )

        # Maintain FTS5 index (DELETE + INSERT since FTS5 has no UPDATE)
        upsert_fts_kg(self._kg._db, node_id, new_label)


# Helpers

def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from *text* for KG searching."""
    return _extract_keywords_core(
        text,
        stop_words=STOP_WORDS,
        min_length=3,
        pattern=r"[a-zA-Z0-9]+",
        deduplicate=False,
    )


# ---------------------------------------------------------------------------
# STT Error Tracker (STT-14)
# ---------------------------------------------------------------------------

_stt_error_lock = threading.Lock()


class SttErrorTracker:
    """Tracks STT transcription errors for measurable improvement over time.

    Errors are persisted as JSONL records in
    ``<root>/.planning/runtime/stt_errors.jsonl``.  Each record contains the
    expected text, actual (mis-transcribed) text, backend name, and timestamp.

    The :meth:`get_error_trend` method aggregates error rates per backend over
    a sliding window so operators can verify that error rates decrease as the
    system improves.
    """

    def __init__(self, root: Path) -> None:
        from jarvis_engine._shared import runtime_dir

        self._errors_path = runtime_dir(root) / "stt_errors.jsonl"

    def log_stt_error(
        self,
        *,
        expected: str,
        actual: str,
        backend: str,
    ) -> None:
        """Log a single STT error for trend tracking."""
        record = {
            "ts": now_iso(),
            "expected": expected[:500],
            "actual": actual[:500],
            "backend": backend,
            "epoch": time.time(),
        }
        try:
            with _stt_error_lock:
                self._errors_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._errors_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.debug("Failed to write STT error record: %s", exc)

    def get_error_trend(self, days: int = 7) -> dict[str, object]:
        """Return error rate statistics over the last *days* days.

        Returns a dict with:
        - ``total_errors``: int
        - ``by_backend``: dict mapping backend name to error count
        - ``daily_counts``: dict mapping date string to error count
        - ``trend``: ``"improving"`` | ``"stable"`` | ``"worsening"``
        """
        cutoff = time.time() - (days * 86400)
        by_backend: dict[str, int] = {}
        daily_counts: dict[str, int] = {}
        total = 0

        try:
            if not self._errors_path.is_file():
                return {
                    "total_errors": 0,
                    "by_backend": {},
                    "daily_counts": {},
                    "trend": "stable",
                }

            with open(self._errors_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    epoch = record.get("epoch", 0)
                    if not isinstance(epoch, (int, float)) or epoch < cutoff:
                        continue

                    total += 1
                    backend = str(record.get("backend", "unknown"))
                    by_backend[backend] = by_backend.get(backend, 0) + 1

                    # Extract date from ISO timestamp
                    ts = str(record.get("ts", ""))
                    date_key = ts[:10] if len(ts) >= 10 else "unknown"
                    daily_counts[date_key] = daily_counts.get(date_key, 0) + 1

        except OSError as exc:
            logger.debug("Failed to read STT error log: %s", exc)
            return {
                "total_errors": 0,
                "by_backend": {},
                "daily_counts": {},
                "trend": "stable",
            }

        # Determine trend: compare first half vs second half of the window
        trend = "stable"
        if len(daily_counts) >= 2:
            sorted_dates = sorted(daily_counts.keys())
            mid = len(sorted_dates) // 2
            first_half = sum(daily_counts[d] for d in sorted_dates[:mid])
            second_half = sum(daily_counts[d] for d in sorted_dates[mid:])
            if second_half < first_half * 0.8:
                trend = "improving"
            elif second_half > first_half * 1.2:
                trend = "worsening"

        return {
            "total_errors": total,
            "by_backend": by_backend,
            "daily_counts": daily_counts,
            "trend": trend,
        }
