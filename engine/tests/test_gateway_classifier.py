"""Tests for IntentClassifier routing, privacy detection, and command evolution.

Uses a deterministic mock EmbeddingService that returns orthogonal 768-dim
vectors based on keyword matching, avoiding the 10+ second real model load.
"""

from __future__ import annotations


import numpy as np
import pytest

from jarvis_engine.commands.task_commands import QueryCommand, RouteCommand
from jarvis_engine.gateway.classifier import IntentClassifier


# ---------------------------------------------------------------------------
# Mock EmbeddingService
# ---------------------------------------------------------------------------

# Four orthogonal direction vectors in 768-dim space.
# Each route gets a distinct direction so cosine similarity separates them.
_DIM = 768
_MATH_DIR = np.zeros(_DIM)
_MATH_DIR[0] = 1.0

_COMPLEX_DIR = np.zeros(_DIM)
_COMPLEX_DIR[1] = 1.0

_ROUTINE_DIR = np.zeros(_DIM)
_ROUTINE_DIR[2] = 1.0

_PRIVATE_DIR = np.zeros(_DIM)
_PRIVATE_DIR[3] = 1.0

_CREATIVE_DIR = np.zeros(_DIM)
_CREATIVE_DIR[4] = 1.0

# Neutral vector -- low similarity with all directions
_NEUTRAL_DIR = np.ones(_DIM) / np.sqrt(_DIM)

_MATH_KEYWORDS = {
    "differential equation", "eigenvalues", "irrational", "probability", "logical proof",
    "incompleteness", "expected value", "linear programming", "compound interest",
    "time complexity", "gravitational", "standard deviation", "derivative",
    "fourier transform", "gaussian elimination", "integral",
}
_COMPLEX_KEYWORDS = {
    "debug", "binary search", "architectural", "race condition", "cqrs", "vulnerabilities",
    "threading code", "microservices", "memory leak", "redis vs memcached",
    "refactor", "unit tests", "ci/cd", "rate limiter", "distributed caching",
    "authentication flow",
}
_ROUTINE_KEYWORDS = {
    "summarize", "rewrite", "translate", "format", "key points", "concise",
    "markdown table", "transcript", "draft a professional email", "grocery list",
    "capital of", "json to yaml", "grammar", "thank you note", "csv",
    "bullet point",
}
_PRIVATE_KEYWORDS = {
    "calendar", "medication", "bill", "dinner", "appointment", "medications",
    "bills", "doctor", "next meeting", "groceries last month", "birthday",
    "wifi password", "tasks due", "parking", "oil change", "morning routine",
    "prescription refill", "jarvis learn",
}
_CREATIVE_KEYWORDS = {
    "short story", "brainstorm", "toast for", "creative names", "poem",
    "fictional dialogue", "product description", "science fiction",
    "motivational speech", "metaphors", "song lyrics", "comedy sketch",
    "gift ideas", "blog post", "mission statement",
}


def _match_direction(text: str) -> np.ndarray:
    """Return a direction vector based on keywords in text."""
    lower = text.lower()
    for kw in _MATH_KEYWORDS:
        if kw in lower:
            return _MATH_DIR.copy()
    for kw in _COMPLEX_KEYWORDS:
        if kw in lower:
            return _COMPLEX_DIR.copy()
    for kw in _ROUTINE_KEYWORDS:
        if kw in lower:
            return _ROUTINE_DIR.copy()
    for kw in _PRIVATE_KEYWORDS:
        if kw in lower:
            return _PRIVATE_DIR.copy()
    for kw in _CREATIVE_KEYWORDS:
        if kw in lower:
            return _CREATIVE_DIR.copy()
    return _NEUTRAL_DIR.copy()


class MockEmbedService:
    """Deterministic embedding service for testing."""

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        vec = _match_direction(text)
        return vec.tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_embed():
    return MockEmbedService()


@pytest.fixture
def classifier(mock_embed):
    return IntentClassifier(mock_embed)


# ---------------------------------------------------------------------------
# IntentClassifier routing tests
# ---------------------------------------------------------------------------

class TestIntentClassifierRouting:
    def test_classify_math_logic_query(self, classifier):
        route, model, confidence = classifier.classify(
            "solve this differential equation step by step"
        )
        assert route == "math_logic"
        assert model == "claude-opus-4-0-20250514"

    def test_classify_complex_query(self, classifier):
        route, model, confidence = classifier.classify(
            "help me debug this race condition in Python"
        )
        assert route == "complex"
        assert model == "claude-opus-4-0-20250514"

    def test_classify_routine_query(self, classifier):
        route, model, confidence = classifier.classify(
            "summarize this meeting transcript for me"
        )
        assert route == "routine"
        assert model == "kimi-k2"

    def test_classify_simple_private_query(self, classifier):
        route, model, confidence = classifier.classify(
            "what medications do I take"
        )
        # Privacy keyword triggers, so route is simple_private
        assert route == "simple_private"

    def test_classify_creative_query(self, classifier):
        route, model, confidence = classifier.classify(
            "write a short story about a robot learning to paint"
        )
        assert route == "creative"
        assert model == "kimi-k2"


# ---------------------------------------------------------------------------
# Privacy keyword tests
# ---------------------------------------------------------------------------

class TestPrivacyKeywords:
    def test_privacy_keyword_forces_local(self, classifier):
        """Even a complex-sounding query with a privacy keyword routes to local."""
        route, model, confidence = classifier.classify(
            "write a detailed analysis of my medication schedule"
        )
        assert route == "simple_private"
        assert confidence == 1.0

    def test_privacy_keywords_comprehensive(self, classifier):
        """Multiple privacy keywords all force simple_private."""
        keywords_to_test = ["calendar", "password", "salary", "bank", "doctor"]
        for kw in keywords_to_test:
            route, model, confidence = classifier.classify(f"tell me about my {kw}")
            assert route == "simple_private", f"Keyword '{kw}' did not force simple_private"
            assert confidence == 1.0, f"Keyword '{kw}' did not return confidence 1.0"


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_ambiguous_query_defaults_to_local(self, classifier):
        """Nonsensical query produces low similarity, defaults to local."""
        route, model, confidence = classifier.classify("xyzzy plugh")
        assert route == "simple_private"
        assert confidence < 0.5, f"Nonsensical query should have low confidence, got {confidence}"

    def test_classify_returns_confidence(self, classifier):
        """Third tuple element is a float between 0 and 1."""
        _, _, confidence = classifier.classify("help me debug this code issue")
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

    def test_model_map_configurable_local(self, mock_embed, monkeypatch):
        """JARVIS_LOCAL_MODEL env var overrides the local model selection."""
        monkeypatch.setenv("JARVIS_LOCAL_MODEL", "llama3:8b")
        # Re-create classifier to pick up env var
        clf = IntentClassifier(mock_embed)
        # Force a privacy-keyword query (guaranteed to return simple_private model)
        route, model, confidence = clf.classify("what is my calendar for today")
        assert route == "simple_private"
        assert model == "llama3:8b"


# ---------------------------------------------------------------------------
# RouteCommand backward compatibility tests
# ---------------------------------------------------------------------------

class TestRouteCommandCompat:
    def test_route_command_default_query_empty(self):
        cmd = RouteCommand()
        assert cmd.query == ""
        assert cmd.risk == "low"
        assert cmd.complexity == "normal"

    def test_route_command_with_query(self):
        cmd = RouteCommand(query="test query")
        assert cmd.query == "test query"
        assert cmd.risk == "low"
        assert cmd.complexity == "normal"


# ---------------------------------------------------------------------------
# QueryCommand tests
# ---------------------------------------------------------------------------

class TestQueryCommand:
    def test_query_command_creation(self):
        cmd = QueryCommand(query="hello")
        assert cmd.query == "hello"
        assert cmd.model is None
        assert cmd.max_tokens == 1024
        assert cmd.system_prompt == ""
