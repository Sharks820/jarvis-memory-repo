"""Fact extraction from text using domain-specific regex patterns and LLM.

Extracts structured fact triples (subject, predicate, object_val, confidence)
from ingested content.  Regex patterns cover health, schedule, preferences,
family, location, and finance domains.  An optional LLM-based extractor
supplements regex extraction for richer, more diverse fact coverage.

The hybrid extraction function runs regex first (fast/free), then LLM if a
gateway is available, and deduplicates the combined results.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

logger = logging.getLogger(__name__)


class FactTriple(NamedTuple):
    """A structured fact extracted from text."""

    subject: str       # node_id for the subject
    predicate: str     # relationship type / edge relation
    object_val: str    # the extracted value
    confidence: float  # extraction confidence (0.0 - 1.0)


def _normalize(text: str) -> str:
    """Normalize text for use as a node_id component.

    Lowercase, replace whitespace with underscore, strip non-alphanumeric
    except underscores.
    """
    result = text.lower().strip()
    result = re.sub(r"\s+", "_", result)
    result = re.sub(r"[^a-z0-9_]", "", result)
    return result


class FactExtractor:
    """Extract structured facts from text using domain-specific patterns."""

    # Each pattern tuple: (compiled_regex, subject_prefix, predicate, base_confidence)
    PATTERNS: list[tuple[re.Pattern, str, str, float]] = [
        # Health: "takes medication X", "prescribed X", "on X daily"
        (
            re.compile(
                r"\b(?:takes?|prescribed?|on)\s+([\w][\w\s]{0,40}?)\s+(?:for|daily|twice|morning|evening)\b",
                re.IGNORECASE,
            ),
            "health.medication",
            "takes",
            0.75,
        ),
        # Schedule: "meeting at X", "appointment on X", "event with X"
        (
            re.compile(
                r"\b(?:meeting|appointment|event)\s+(?:at|on|with)\s+(.{1,80}?)(?:\.|,|$)",
                re.IGNORECASE,
            ),
            "ops.schedule",
            "has_event",
            0.65,
        ),
        # Preference: "prefers X", "likes X", "favorite X"
        (
            re.compile(
                r"(?:prefers?|likes?|favorite)\s+(.+?)(?:\.|,|$)",
                re.IGNORECASE,
            ),
            "preference",
            "prefers",
            0.70,
        ),
        # Family: "son named X", "daughter X", "wife X", "husband X"
        (
            re.compile(
                r"(?:son|daughter|wife|husband|spouse|child)\s+(?:named?\s+)?(\w+)",
                re.IGNORECASE,
            ),
            "family.member",
            "family_relation",
            0.80,
        ),
        # Location: "lives in X", "address is X", "located at X"
        (
            re.compile(
                r"(?:lives?\s+in|address\s+is|located\s+at)\s+(.+?)(?:\.|,|$)",
                re.IGNORECASE,
            ),
            "ops.location",
            "located_at",
            0.70,
        ),
        # Finance: "salary is X", "income of X", "makes X"
        (
            re.compile(
                r"(?:salary|income|makes?|earns?)\s+(?:is\s+|of\s+)?(\$?[\d,]+(?:\.\d{2})?(?:\s*(?:per|a|/)\s*(?:year|month|week|hour))?)",
                re.IGNORECASE,
            ),
            "finance.income",
            "earns",
            0.65,
        ),
    ]

    def extract(
        self, text: str, source: str = "", branch: str = "", max_facts: int = 10
    ) -> list[FactTriple]:
        """Extract fact triples from text content.

        Args:
            text: The text to extract facts from.
            source: Origin identifier for provenance.
            branch: Memory branch for context.
            max_facts: Maximum number of facts to return (default 10).

        Returns:
            List of FactTriple objects, capped at ``max_facts`` per content.
        """
        facts: list[FactTriple] = []

        for pattern, subject_prefix, predicate, base_conf in self.PATTERNS:
            for match in pattern.finditer(text):
                object_val = match.group(1).strip()

                # Skip matches with object_val too short or too long
                if len(object_val) < 2 or len(object_val) > 100:
                    continue

                normalized = _normalize(object_val)
                if not normalized:
                    continue

                subject = f"{subject_prefix}.{normalized}"
                facts.append(
                    FactTriple(
                        subject=subject,
                        predicate=predicate,
                        object_val=object_val,
                        confidence=base_conf,
                    )
                )

        return facts[:max_facts]
