"""LLM-powered fact extraction from text using the ModelGateway.

Supplements the regex-based FactExtractor with LLM intelligence for
richer, more diverse fact extraction.  Uses a carefully crafted system
prompt with few-shot examples to guide the model.  Privacy-aware:
routes through local Ollama when the text contains privacy keywords.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis_engine._constants import PRIVACY_KEYWORDS as _PRIVACY_KW

if TYPE_CHECKING:
    from jarvis_engine.gateway.models import ModelGateway

logger = logging.getLogger(__name__)

# Confidence defaults per category -- more specific categories get higher base
# confidence; generic ones get lower.
_CATEGORY_CONFIDENCE: dict[str, float] = {
    "health": 0.80,
    "family": 0.85,
    "finance": 0.75,
    "preference": 0.70,
    "schedule": 0.65,
    "location": 0.75,
    "work": 0.70,
    "hobby": 0.65,
    "education": 0.70,
    "social": 0.60,
}

_DEFAULT_CONFIDENCE: float = 0.60

_SYSTEM_PROMPT = """\
You are a fact-extraction engine for a personal knowledge graph.
Given a piece of text, extract structured facts as JSON.

Each fact must have:
- entity: the subject of the fact (a person, place, object, or concept)
- relationship: the verb or relation connecting entity to value
- value: the object or detail
- category: one of health, finance, family, preference, schedule, location, work, hobby, education, social
- source_text: the exact phrase from the input that supports this fact

Return a JSON array of objects.  If no facts can be extracted, return [].
Do NOT wrap the JSON in markdown code fences.  Return ONLY the JSON array.

Examples:

Input: "I take metformin 500mg every morning for diabetes."
Output: [{"entity": "owner", "relationship": "takes_medication", "value": "metformin 500mg", "category": "health", "source_text": "take metformin 500mg every morning for diabetes"}]

Input: "My wife Sarah works at Memorial Hospital as a nurse."
Output: [{"entity": "Sarah", "relationship": "spouse_of", "value": "owner", "category": "family", "source_text": "My wife Sarah"}, {"entity": "Sarah", "relationship": "works_at", "value": "Memorial Hospital", "category": "work", "source_text": "Sarah works at Memorial Hospital as a nurse"}]

Input: "I switched to decaf because caffeine gives me anxiety."
Output: [{"entity": "owner", "relationship": "prefers", "value": "decaf coffee", "category": "preference", "source_text": "switched to decaf"}, {"entity": "owner", "relationship": "sensitive_to", "value": "caffeine", "category": "health", "source_text": "caffeine gives me anxiety"}]

Input: "My son Oliver starts kindergarten at Lincoln Elementary in September."
Output: [{"entity": "Oliver", "relationship": "child_of", "value": "owner", "category": "family", "source_text": "My son Oliver"}, {"entity": "Oliver", "relationship": "attends", "value": "Lincoln Elementary", "category": "education", "source_text": "Oliver starts kindergarten at Lincoln Elementary"}]

Input: "I have a dentist appointment on Thursday at 2pm."
Output: [{"entity": "owner", "relationship": "has_appointment", "value": "dentist on Thursday at 2pm", "category": "schedule", "source_text": "dentist appointment on Thursday at 2pm"}]

Input: "We moved to Portland, Oregon last year."
Output: [{"entity": "owner", "relationship": "lives_in", "value": "Portland, Oregon", "category": "location", "source_text": "moved to Portland, Oregon"}]

Input: "I got promoted to senior engineer at Acme Corp."
Output: [{"entity": "owner", "relationship": "job_title", "value": "senior engineer", "category": "work", "source_text": "promoted to senior engineer"}, {"entity": "owner", "relationship": "works_at", "value": "Acme Corp", "category": "work", "source_text": "senior engineer at Acme Corp"}]

Input: "I've been running 5K every Saturday morning at the park."
Output: [{"entity": "owner", "relationship": "practices", "value": "running 5K", "category": "hobby", "source_text": "running 5K every Saturday morning"}]

Input: "I'm finishing my MBA at State University this spring."
Output: [{"entity": "owner", "relationship": "studying", "value": "MBA", "category": "education", "source_text": "finishing my MBA at State University"}]

Input: "Had dinner with Mike and Jane last night at the Italian place."
Output: [{"entity": "owner", "relationship": "socializes_with", "value": "Mike", "category": "social", "source_text": "dinner with Mike and Jane"}, {"entity": "owner", "relationship": "socializes_with", "value": "Jane", "category": "social", "source_text": "dinner with Mike and Jane"}]

Now extract facts from the following text:
"""

# Build privacy regex from the canonical PRIVACY_KEYWORDS in _constants.py.
_PRIVACY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _PRIVACY_KW) + r")\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedFact:
    """A fact extracted by the LLM from text."""

    entity: str
    relationship: str
    value: str
    confidence: float
    category: str
    source_text: str


class LLMFactExtractor:
    """Extract structured facts from text using an LLM via the ModelGateway.

    Privacy-aware: routes through local Ollama when the input text contains
    any of the known privacy keywords.  Otherwise uses kimi-k2 via Groq for
    fast cloud extraction.
    """

    def __init__(
        self,
        gateway: "ModelGateway",
        embed_service: object | None = None,
    ) -> None:
        self._gateway = gateway
        self._embed_service = embed_service

    def _contains_privacy_keyword(self, text: str) -> bool:
        """Return True if any privacy keyword appears in the text."""
        return bool(_PRIVACY_RE.search(text))

    def _pick_model(self, text: str) -> tuple[str, bool]:
        """Choose model based on privacy analysis.

        Returns (model_name, privacy_routed).
        """
        if self._contains_privacy_keyword(text):
            from jarvis_engine._constants import get_local_model as _get_local_model
            local_model = _get_local_model()
            return local_model, True
        return "kimi-k2", False

    def extract_facts(
        self, text: str, branch: str = ""
    ) -> list[ExtractedFact]:
        """Extract structured facts from text using an LLM.

        Args:
            text: The text to extract facts from.
            branch: Memory branch for context (unused currently but
                    kept for API compatibility with FactExtractor).

        Returns:
            List of ExtractedFact objects, or empty list on any error.
        """
        if not text or not text.strip():
            return []

        model, privacy_routed = self._pick_model(text)

        from jarvis_engine.temporal import get_datetime_prompt

        system_with_time = f"{get_datetime_prompt()}\n\n{_SYSTEM_PROMPT}"
        messages = [
            {"role": "system", "content": system_with_time},
            {"role": "user", "content": text},
        ]

        try:
            response = self._gateway.complete(
                messages=messages,
                model=model,
                max_tokens=1024,
                route_reason="llm_fact_extraction",
                privacy_routed=privacy_routed,
            )
        except Exception:
            logger.warning("LLM fact extraction failed for gateway call", exc_info=True)
            return []

        if not response.text or not response.text.strip():
            return []

        return self._parse_response(response.text)

    def _parse_response(self, raw: str) -> list[ExtractedFact]:
        """Parse the LLM JSON response into ExtractedFact objects.

        Handles common LLM quirks: markdown code fences, trailing commas,
        extra whitespace.  Returns empty list on any parse failure.
        """
        cleaned = raw.strip()

        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            # Remove opening fence (possibly with language tag)
            if "\n" in cleaned:
                first_newline = cleaned.index("\n")
                cleaned = cleaned[first_newline + 1 :]
            else:
                # Single-line: skip past the opening ``` and optional language tag
                # e.g. ```json[...] or ```[...]
                m = re.match(r"^```\w*", cleaned)
                cleaned = cleaned[m.end() :] if m else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.debug("LLM fact extractor: malformed JSON response: %s", cleaned[:200])
            return []

        if not isinstance(data, list):
            logger.debug("LLM fact extractor: expected list, got %s", type(data).__name__)
            return []

        facts: list[ExtractedFact] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            entity = str(item.get("entity", "")).strip()
            relationship = str(item.get("relationship", "")).strip()
            value = str(item.get("value", "")).strip()
            category = str(item.get("category", "")).strip().lower()
            source_text = str(item.get("source_text", "")).strip()

            # Skip incomplete facts
            if not entity or not relationship or not value:
                continue

            confidence = _CATEGORY_CONFIDENCE.get(category, _DEFAULT_CONFIDENCE)

            facts.append(
                ExtractedFact(
                    entity=entity,
                    relationship=relationship,
                    value=value,
                    confidence=confidence,
                    category=category,
                    source_text=source_text,
                )
            )

        return facts[:20]  # Cap per-content extraction
