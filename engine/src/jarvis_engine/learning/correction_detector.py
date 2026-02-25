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

        keywords = _extract_keywords(correction.old_claim or correction.new_claim)
        if not keywords:
            return False

        # Search for matching facts
        matches = self._kg.query_relevant_facts(keywords, min_confidence=0.0, limit=5)
        if not matches:
            return False

        # Pick the best match -- first result (highest confidence, best keyword overlap)
        best = matches[0]
        node_id = best["node_id"]

        with self._kg._write_lock:
            # Read current confidence
            row = self._kg._db.execute(
                "SELECT confidence FROM kg_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            if row is None:
                return False

            existing_confidence = row[0] if isinstance(row[0], float) else float(row[0])
            new_confidence = max(existing_confidence + 0.1, 0.9)

            # Update the fact label and confidence
            self._kg._db.execute(
                """UPDATE kg_nodes
                   SET label = ?, confidence = ?, updated_at = datetime('now')
                   WHERE node_id = ?""",
                (correction.new_claim, new_confidence, node_id),
            )
            self._kg._db.commit()

        logger.info(
            "Correction applied: node %s updated to %r (confidence %.2f)",
            node_id,
            correction.new_claim,
            new_confidence,
        )

        # Add superseded edge if both old and new exist as separate nodes
        if correction.old_claim:
            old_keywords = _extract_keywords(correction.old_claim)
            new_keywords = _extract_keywords(correction.new_claim)
            old_matches = self._kg.query_relevant_facts(
                old_keywords, min_confidence=0.0, limit=1,
            )
            new_matches = self._kg.query_relevant_facts(
                new_keywords, min_confidence=0.0, limit=1,
            )
            if old_matches and new_matches:
                old_id = old_matches[0]["node_id"]
                new_id = new_matches[0]["node_id"]
                if old_id != new_id:
                    self._kg.add_edge(
                        source_id=old_id,
                        target_id=new_id,
                        relation="superseded",
                        confidence=correction.confidence,
                        source_record="correction_detector",
                    )

        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from *text* for KG searching."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    # Filter short/common words
    stopwords = {
        "a", "an", "the", "is", "it", "its", "was", "were", "be", "been",
        "am", "are", "do", "did", "does", "to", "of", "in", "on", "at",
        "for", "by", "and", "or", "but", "not", "no", "so", "if", "as",
        "my", "me", "i", "we", "us", "he", "she", "they", "that", "this",
        "with", "from", "has", "had", "have", "s",
    }
    return [w for w in words if len(w) > 2 and w not in stopwords]
