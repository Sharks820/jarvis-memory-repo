"""Tests for jarvis_engine.knowledge.llm_extractor -- LLM-powered fact extraction.

Covers:
- ExtractedFact dataclass fields
- LLMFactExtractor.extract_facts: health, family, preference extraction
- Privacy keyword detection forces local model routing
- Empty/whitespace text returns empty list
- Malformed JSON response returns empty list
- Multiple facts extracted from a single message
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis_engine.gateway.models import GatewayResponse
from jarvis_engine.knowledge.llm_extractor import (
    ExtractedFact,
    LLMFactExtractor,
    _CATEGORY_CONFIDENCE,
    _DEFAULT_CONFIDENCE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gateway(response_text: str) -> MagicMock:
    """Create a mock ModelGateway that returns the given text."""
    gw = MagicMock()
    gw.complete.return_value = GatewayResponse(
        text=response_text,
        model="kimi-k2",
        provider="groq",
        input_tokens=100,
        output_tokens=50,
    )
    return gw


# ---------------------------------------------------------------------------
# ExtractedFact dataclass
# ---------------------------------------------------------------------------


class TestExtractedFact:

    def test_fields(self):
        fact = ExtractedFact(
            entity="owner",
            relationship="takes_medication",
            value="aspirin",
            confidence=0.80,
            category="health",
            source_text="takes aspirin daily",
        )
        assert fact.entity == "owner"
        assert fact.relationship == "takes_medication"
        assert fact.value == "aspirin"
        assert fact.confidence == 0.80
        assert fact.category == "health"
        assert fact.source_text == "takes aspirin daily"


# ---------------------------------------------------------------------------
# Health fact extraction
# ---------------------------------------------------------------------------


class TestExtractHealthFact:

    def test_extract_health_fact(self):
        response_json = (
            '[{"entity": "owner", "relationship": "takes_medication", '
            '"value": "metformin 500mg", "category": "health", '
            '"source_text": "take metformin 500mg every morning"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("I take metformin 500mg every morning for diabetes.")

        assert len(facts) == 1
        assert facts[0].entity == "owner"
        assert facts[0].relationship == "takes_medication"
        assert facts[0].value == "metformin 500mg"
        assert facts[0].category == "health"
        assert facts[0].confidence == _CATEGORY_CONFIDENCE["health"]

        # Verify gateway was called with correct model
        call_kwargs = gw.complete.call_args
        assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") or "kimi-k2"


# ---------------------------------------------------------------------------
# Family fact extraction
# ---------------------------------------------------------------------------


class TestExtractFamilyFact:

    def test_extract_family_fact(self):
        response_json = (
            '[{"entity": "Oliver", "relationship": "child_of", '
            '"value": "owner", "category": "family", '
            '"source_text": "My son Oliver"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("My son Oliver starts kindergarten next month.")

        assert len(facts) == 1
        assert facts[0].entity == "Oliver"
        assert facts[0].relationship == "child_of"
        assert facts[0].category == "family"
        assert facts[0].confidence == _CATEGORY_CONFIDENCE["family"]


# ---------------------------------------------------------------------------
# Preference fact extraction
# ---------------------------------------------------------------------------


class TestExtractPreferenceFact:

    def test_extract_preference_fact(self):
        response_json = (
            '[{"entity": "owner", "relationship": "prefers", '
            '"value": "dark mode", "category": "preference", '
            '"source_text": "prefer dark mode"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("I always prefer dark mode on all my devices.")

        assert len(facts) == 1
        assert facts[0].value == "dark mode"
        assert facts[0].category == "preference"
        assert facts[0].confidence == _CATEGORY_CONFIDENCE["preference"]


# ---------------------------------------------------------------------------
# Privacy routing
# ---------------------------------------------------------------------------


class TestPrivacyForcesLocalModel:

    @patch.dict("os.environ", {"JARVIS_LOCAL_MODEL": "llama3:8b"})
    def test_privacy_forces_local_model(self):
        response_json = (
            '[{"entity": "owner", "relationship": "takes_medication", '
            '"value": "lisinopril", "category": "health", '
            '"source_text": "medication lisinopril"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        # "medication" is a privacy keyword
        facts = extractor.extract_facts("My medication lisinopril needs a refill.")

        assert len(facts) == 1

        # Verify that local model was requested
        call_kwargs = gw.complete.call_args
        assert call_kwargs.kwargs["model"] == "llama3:8b"
        assert call_kwargs.kwargs["privacy_routed"] is True

    @patch.dict("os.environ", {"JARVIS_LOCAL_MODEL": "gemma3:4b"})
    def test_privacy_default_local_model(self):
        """When JARVIS_LOCAL_MODEL is default, still routes locally for privacy."""
        response_json = "[]"
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        extractor.extract_facts("Check my bank account balance.")

        call_kwargs = gw.complete.call_args
        assert call_kwargs.kwargs["model"] == "gemma3:4b"
        assert call_kwargs.kwargs["privacy_routed"] is True

    @patch.dict("os.environ", {}, clear=False)
    def test_non_private_uses_cloud_model(self):
        """Text without privacy keywords uses kimi-k2 for cloud extraction."""
        response_json = (
            '[{"entity": "owner", "relationship": "practices", '
            '"value": "running", "category": "hobby", '
            '"source_text": "running every morning"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        extractor.extract_facts("I enjoy running every morning in the park.")

        call_kwargs = gw.complete.call_args
        assert call_kwargs.kwargs["model"] == "kimi-k2"
        assert call_kwargs.kwargs["privacy_routed"] is False


# ---------------------------------------------------------------------------
# Empty text
# ---------------------------------------------------------------------------


class TestEmptyTextReturnsEmpty:

    def test_empty_text_returns_empty(self):
        gw = _make_gateway("[]")
        extractor = LLMFactExtractor(gateway=gw)

        assert extractor.extract_facts("") == []
        assert extractor.extract_facts("   ") == []
        assert extractor.extract_facts(None) == []  # type: ignore[arg-type]

        # Gateway should never be called for empty input
        gw.complete.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJsonReturnsEmpty:

    def test_malformed_json_returns_empty(self):
        gw = _make_gateway("This is not JSON at all!")
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("Some input text that triggers a bad response.")
        assert facts == []

    def test_json_object_instead_of_array(self):
        gw = _make_gateway('{"entity": "owner", "relationship": "takes"}')
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("Some text.")
        assert facts == []

    def test_json_with_markdown_fences(self):
        """LLM sometimes wraps JSON in markdown code fences -- should still parse."""
        inner = (
            '[{"entity": "owner", "relationship": "prefers", '
            '"value": "tea", "category": "preference", '
            '"source_text": "prefers tea"}]'
        )
        fenced = f"```json\n{inner}\n```"
        gw = _make_gateway(fenced)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("I prefer tea over coffee.")
        assert len(facts) == 1
        assert facts[0].value == "tea"

    def test_gateway_exception_returns_empty(self):
        """If gateway.complete raises, return empty list (never crash)."""
        gw = MagicMock()
        gw.complete.side_effect = RuntimeError("connection timeout")
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("Test input.")
        assert facts == []

    def test_empty_gateway_response_returns_empty(self):
        gw = _make_gateway("")
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("Some input.")
        assert facts == []


# ---------------------------------------------------------------------------
# Multiple facts from one message
# ---------------------------------------------------------------------------


class TestMultipleFactsFromOneMessage:

    def test_multiple_facts_from_one_message(self):
        response_json = (
            "["
            '{"entity": "Sarah", "relationship": "spouse_of", '
            '"value": "owner", "category": "family", '
            '"source_text": "My wife Sarah"},'
            '{"entity": "Sarah", "relationship": "works_at", '
            '"value": "Memorial Hospital", "category": "work", '
            '"source_text": "Sarah works at Memorial Hospital"},'
            '{"entity": "owner", "relationship": "lives_in", '
            '"value": "Portland", "category": "location", '
            '"source_text": "We live in Portland"}'
            "]"
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts(
            "My wife Sarah works at Memorial Hospital. We live in Portland."
        )

        assert len(facts) == 3

        categories = {f.category for f in facts}
        assert "family" in categories
        assert "work" in categories
        assert "location" in categories

        # Verify confidence is set per-category
        family_fact = next(f for f in facts if f.category == "family")
        assert family_fact.confidence == _CATEGORY_CONFIDENCE["family"]

        work_fact = next(f for f in facts if f.category == "work")
        assert work_fact.confidence == _CATEGORY_CONFIDENCE["work"]

    def test_incomplete_facts_skipped(self):
        """Facts missing required fields are silently skipped."""
        response_json = (
            "["
            '{"entity": "owner", "relationship": "prefers", '
            '"value": "tea", "category": "preference", '
            '"source_text": "prefers tea"},'
            '{"entity": "", "relationship": "broken", '
            '"value": "nothing", "category": "unknown", '
            '"source_text": ""},'
            '{"entity": "owner", "relationship": "", '
            '"value": "missing_rel", "category": "work", '
            '"source_text": "something"}'
            "]"
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("I prefer tea.")
        # Only the first valid fact should survive
        assert len(facts) == 1
        assert facts[0].value == "tea"

    def test_unknown_category_gets_default_confidence(self):
        """Categories not in the confidence map get the default."""
        response_json = (
            '[{"entity": "owner", "relationship": "owns", '
            '"value": "Tesla Model 3", "category": "vehicle", '
            '"source_text": "owns a Tesla Model 3"}]'
        )
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("I own a Tesla Model 3.")
        assert len(facts) == 1
        assert facts[0].confidence == _DEFAULT_CONFIDENCE

    def test_cap_at_twenty(self):
        """At most 20 facts are returned per call."""
        items = []
        for i in range(25):
            items.append(
                f'{{"entity": "item{i}", "relationship": "rel", '
                f'"value": "val{i}", "category": "social", '
                f'"source_text": "source {i}"}}'
            )
        response_json = "[" + ",".join(items) + "]"
        gw = _make_gateway(response_json)
        extractor = LLMFactExtractor(gateway=gw)

        facts = extractor.extract_facts("A long text with many facts.")
        assert len(facts) == 20
