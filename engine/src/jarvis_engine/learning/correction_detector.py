"""Detects user corrections in conversation and applies them to the knowledge graph.

Parses natural-language correction patterns (e.g. "no, actually X", "I meant X",
"not X but Y") and optionally updates matching KG facts so the assistant learns
from explicit user feedback.

Thread safety: all KG mutations acquire ``KnowledgeGraph._write_lock``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

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


# ------------------------------------------------------------------
# Compiled correction patterns
# ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

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
                new_keywords, min_confidence=0.0, limit=1,
            )

        with self._kg._write_lock:
            try:
                # Read current confidence and lock state
                row = self._kg._db.execute(
                    "SELECT confidence, locked FROM kg_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                if row is None:
                    return False

                existing_confidence = row[0] if isinstance(row[0], float) else float(row[0])
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
                        # A node with the new claim already exists — merge instead of duplicating.
                        # Boost the existing node's confidence to the max of both.
                        existing_new_row = self._kg._db.execute(
                            "SELECT confidence FROM kg_nodes WHERE node_id = ?",
                            (existing_new_id,),
                        ).fetchone()
                        if existing_new_row is not None:
                            existing_new_conf = float(existing_new_row[0])
                            merged_confidence = min(max(existing_new_conf, new_confidence), 1.0)
                            self._kg._db.execute(
                                """UPDATE kg_nodes
                                   SET confidence = ?, updated_at = datetime('now')
                                   WHERE node_id = ?""",
                                (merged_confidence, existing_new_id),
                            )
                        # Retract the old node (set confidence to 0) rather than deleting
                        self._kg._db.execute(
                            """UPDATE kg_nodes
                               SET confidence = 0, updated_at = datetime('now')
                               WHERE node_id = ?""",
                            (node_id,),
                        )
                        # Clean up FTS5 index for the retracted node
                        try:
                            self._kg._db.execute(
                                "DELETE FROM fts_kg_nodes WHERE node_id = ?", (node_id,)
                            )
                        except Exception as exc:
                            if "no such table" not in str(exc):
                                raise
                            logger.debug("FTS5 table not available, skipping cleanup for retracted node %s", node_id)
                        # Clean up vec index for the retracted node
                        if getattr(self._kg, "_vec_available", False):
                            try:
                                self._kg._db.execute(
                                    "DELETE FROM vec_kg_nodes WHERE node_id = ?", (node_id,)
                                )
                            except Exception as exc:
                                logger.debug("Vec cleanup for retracted node %s failed: %s", node_id, exc)
                        self._kg._db.commit()
                        self._kg._mutation_counter += 1
                        logger.info(
                            "Correction merged: node %s retracted, existing node %s boosted to %.2f",
                            node_id, existing_new_id, merged_confidence if existing_new_row else new_confidence,
                        )
                        # Add superseded edge for historical audit trail
                        try:
                            self._kg.add_edge(
                                source_id=node_id,
                                target_id=existing_new_id,
                                relation="superseded",
                            )
                        except Exception as exc:
                            logger.debug("KG audit edge failed: %s", exc)
                        return True

                # Update the fact label and confidence
                self._kg._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, confidence = ?, updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (correction.new_claim, new_confidence, node_id),
                )

                # Maintain FTS5 index (DELETE + INSERT since FTS5 has no UPDATE)
                try:
                    self._kg._db.execute(
                        "DELETE FROM fts_kg_nodes WHERE node_id = ?", (node_id,)
                    )
                    self._kg._db.execute(
                        "INSERT INTO fts_kg_nodes(node_id, label) VALUES (?, ?)",
                        (node_id, correction.new_claim),
                    )
                except Exception as exc:
                    if "no such table" not in str(exc):
                        raise
                    logger.debug("FTS5 table not available, skipping index update for node %s", node_id)

                self._kg._db.commit()
                # Invalidate NetworkX cache (bypassed add_fact)
                self._kg._mutation_counter += 1
            except Exception:
                self._kg._db.rollback()
                raise

        logger.info(
            "Correction applied: node %s updated to %r (confidence %.2f)",
            node_id,
            correction.new_claim,
            new_confidence,
        )

        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_CORRECTION_STOPWORDS = frozenset({
    "a", "an", "the", "is", "it", "its", "was", "were", "be", "been",
    "am", "are", "do", "did", "does", "to", "of", "in", "on", "at",
    "for", "by", "and", "or", "but", "not", "no", "so", "if", "as",
    "my", "me", "i", "we", "us", "he", "she", "they", "that", "this",
    "with", "from", "has", "had", "have", "s",
})


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from *text* for KG searching."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _CORRECTION_STOPWORDS]
