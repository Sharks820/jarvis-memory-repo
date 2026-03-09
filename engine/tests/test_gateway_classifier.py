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

_WEB_RESEARCH_DIR = np.zeros(_DIM)
_WEB_RESEARCH_DIR[5] = 1.0

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
_WEB_RESEARCH_KEYWORDS = {
    "latest news", "current price", "super bowl", "top headlines",
    "weather forecast", "stock market", "iphone come out", "news today",
    "tesla model", "presidential election", "gas prices", "score of the",
    "best restaurants", "coming out", "exchange rate",
    # Ensure ALL 15 exemplars have keyword coverage (prevents neutral centroid drift)
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
    for kw in _WEB_RESEARCH_KEYWORDS:
        if kw in lower:
            return _WEB_RESEARCH_DIR.copy()
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
def classifier(mock_embed, tmp_path, monkeypatch):
    # Use a temp cache directory to avoid stale centroid caches from previous runs
    monkeypatch.setattr(IntentClassifier, "_cache_dir", staticmethod(lambda: str(tmp_path / "centroids")))
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
        from jarvis_engine._constants import get_local_model
        assert model == get_local_model()  # Local 9B for deep reasoning

    def test_classify_complex_query(self, classifier):
        route, model, confidence = classifier.classify(
            "help me debug this race condition in Python"
        )
        assert route == "complex"
        from jarvis_engine._constants import get_local_model
        assert model == get_local_model()  # Local 9B for complex tasks

    def test_classify_routine_query(self, classifier):
        route, model, confidence = classifier.classify(
            "summarize this meeting transcript for me"
        )
        assert route == "routine"
        from jarvis_engine._constants import get_fast_local_model
        assert model == get_fast_local_model()  # Fast local 4B for routine tasks

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
        from jarvis_engine._constants import get_fast_local_model
        assert model == get_fast_local_model()  # Fast local 4B for creative

    def test_classify_web_research_query(self, classifier):
        route, model, confidence = classifier.classify(
            "what is the latest news about artificial intelligence"
        )
        assert route == "web_research"
        from jarvis_engine._constants import get_fast_local_model
        assert model == get_fast_local_model()  # Fast local 4B for web research

    def test_classify_web_research_current_price(self, classifier):
        route, model, confidence = classifier.classify(
            "what is the current price of bitcoin today"
        )
        assert route == "web_research"
        from jarvis_engine._constants import get_fast_local_model
        assert model == get_fast_local_model()  # Fast local 4B for web research


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

    def test_privacy_keyword_word_boundary(self, classifier):
        """Words containing privacy keywords as substrings should NOT trigger privacy."""
        # "accountant" contains "account" but should NOT trigger privacy
        route, model, confidence = classifier.classify("I need to hire an accountant for taxes")
        assert route != "simple_private" or confidence != 1.0, (
            "Substring 'account' inside 'accountant' should not trigger privacy keyword"
        )

    def test_classify_with_available_models(self, classifier):
        """classify() correctly filters to available_models set."""
        # Only kimi-k2 and gemini-cli available — math_logic primary (codex-cli) unavailable
        available = {"kimi-k2", "gemini-cli"}
        route, model, confidence = classifier.classify(
            "solve this differential equation step by step",
            available_models=available,
        )
        assert route == "math_logic"
        assert model in available, f"Model {model} not in available set {available}"


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
