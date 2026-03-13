"""IntentClassifier: embedding-based query routing with privacy keyword detection.

Local-first strategy — all queries start with local Ollama models:
- Math/logic + Complex tasks -> qwen3.5:latest (9B, deeper reasoning)
- Routine/creative/web research -> qwen3.5:4b (fast, ~30 tok/s)
- Private/personal data -> qwen3.5:latest (9B, never leaves device)

Cloud CLIs (Claude, Codex, Gemini) are fallbacks only — used when the
local model fails or can't handle the query (e.g., web grounding).

Privacy keywords force local routing regardless of embedding similarity.
Low-confidence queries default to local (privacy-safe).
"""

from __future__ import annotations

import logging
import os
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, cast

from jarvis_engine._constants import PRIVACY_KEYWORDS as _CANONICAL_PRIVACY_KEYWORDS
from jarvis_engine._protocols import EmbedServiceProtocol
from jarvis_engine._shared import get_fast_local_model as _get_fast_local_model

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import numpy as np

class FeedbackTrackerProtocol(Protocol):
    def get_route_quality(self, route_name: str) -> dict[str, Any]:
        ...

class IntentClassifier:
    """Classify user queries into routing categories using embedding similarity."""

    ROUTES: dict[str, tuple[str, ...]] = {
        "math_logic": (
            "solve this differential equation step by step",
            "prove that the square root of 2 is irrational",
            "calculate the eigenvalues of this 3x3 matrix",
            "explain the logical proof for Godel's incompleteness theorem",
            "what is the probability of drawing two aces from a shuffled deck",
            "what is the expected value of this probability distribution",
            "optimize this linear programming problem with constraints",
            "calculate the compound interest over 10 years at 5% APR",
            "what's the time complexity of merge sort vs quicksort",
            "derive the formula for gravitational potential energy",
            "compute the standard deviation of this dataset",
            "find the derivative of this multivariable function",
            "calculate the Fourier transform of this signal",
            "solve this system of linear equations using Gaussian elimination",
            "what is the integral of e to the power of negative x squared",
        ),
        "complex": (
            "write a Python script that implements a binary search tree with balancing",
            "analyze this codebase and suggest architectural improvements",
            "help me debug this race condition in my threading code",
            "explain the tradeoffs between CQRS and event sourcing",
            "review this security policy and identify vulnerabilities",
            "design a microservices architecture for this e-commerce system",
            "explain the memory leak in this Python code and how to fix it",
            "compare Redis vs Memcached for session storage in my use case",
            "refactor this monolithic function into clean modular components",
            "write unit tests for this complex state machine implementation",
            "set up a CI/CD pipeline with Docker and GitHub Actions",
            "implement a rate limiter using the token bucket algorithm",
            "explain how to scale this database for millions of concurrent users",
            "design a distributed caching strategy for this web application",
            "analyze the security implications of this authentication flow",
        ),
        "routine": (
            "summarize this article for me",
            "rewrite this paragraph to be more concise",
            "what are the key points from this meeting transcript",
            "translate this text to French",
            "format this data as a markdown table",
            "draft a professional email declining this meeting invitation",
            "create a grocery list based on this recipe",
            "what is the capital of France",
            "convert this JSON to YAML format",
            "fix the grammar and spelling in this paragraph",
            "write a brief thank you note for a gift",
            "list the main differences between Python and JavaScript",
            "explain what REST API means in simple terms",
            "generate a bullet point summary of these meeting notes",
            "reformat this CSV data into a readable table",
        ),
        "simple_private": (
            "what's on my calendar today",
            "what medications do I take",
            "remind me about my doctor appointment",
            "what did I have for dinner yesterday",
            "show me my recent bills",
            "what time is my next meeting",
            "how much did I spend on groceries last month",
            "when is my wife's birthday",
            "what's my home WiFi password",
            "what tasks do I have due this week",
            "where did I park my car",
            "when was my last oil change",
            "what's my morning routine",
            "show me my prescription refill schedule",
            "what did Jarvis learn about me today",
        ),
        "creative": (
            "write a short story about a robot learning to paint",
            "brainstorm 10 startup ideas in the health tech space",
            "help me write a toast for my friend's wedding",
            "come up with creative names for my new app",
            "write a poem about autumn",
            "create a fictional dialogue between two historical figures",
            "help me write a compelling product description",
            "generate an outline for a science fiction novel",
            "write a motivational speech about overcoming challenges",
            "come up with metaphors to explain machine learning to kids",
            "write song lyrics about a rainy day in the city",
            "create a funny script for a two-minute comedy sketch",
            "brainstorm unique gift ideas for someone who has everything",
            "write an engaging introduction for my blog post",
            "help me craft a personal mission statement",
        ),
        "web_research": (
            "what is the latest news about artificial intelligence",
            "what is the current price of bitcoin today",
            "who won the Super Bowl this year",
            "what are the top headlines right now",
            "what is the weather forecast for this weekend",
            "what are the latest stock market results",
            "when does the new iPhone come out",
            "what happened in the news today",
            "how much does a Tesla Model 3 cost right now",
            "who is winning the presidential election",
            "what are the current gas prices near me",
            "what is the score of the basketball game tonight",
            "find me the best restaurants in downtown Austin",
            "what movies are coming out this month",
            "what is the exchange rate for USD to EUR today",
        ),
    }

    # Primary model for each route — local-first strategy.
    # qwen3.5:4b (fast) handles routine/creative/web queries.
    # qwen3.5:latest (9B) handles complex/math tasks requiring deeper reasoning.
    # Cloud CLIs (Claude, Codex, Gemini) are fallbacks only — used when local
    # models detect they can't handle the query or for web grounding.
    # All routes resolved at runtime via _get_local_model / _get_fast_local_model.
    MODEL_MAP: MappingProxyType[str, str] = MappingProxyType({})  # All routes resolved dynamically in _resolve_model_for_route

    # Fallback preferences per route — cloud CLIs as escalation only.
    # Tried in order when local model fails or returns low-quality response.
    MODEL_FALLBACKS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({
        "math_logic": ("codex-cli", "claude-cli", "gemini-cli"),
        "complex": ("claude-cli", "codex-cli", "gemini-cli"),
        "routine": ("gemini-cli", "claude-cli"),
        "creative": ("gemini-cli", "claude-cli"),
        "web_research": ("gemini-cli", "claude-cli"),
    })

    PRIVACY_KEYWORDS: frozenset[str] = _CANONICAL_PRIVACY_KEYWORDS

    CONFIDENCE_THRESHOLD: float = 0.35

    def __init__(
        self,
        embed_service: EmbedServiceProtocol,
        feedback_tracker: FeedbackTrackerProtocol | None = None,
    ) -> None:
        """Initialize with an EmbeddingService instance.

        Args:
            embed_service: Must implement embed(text, prefix) and embed_query(query).
            feedback_tracker: Optional feedback tracker for route quality penalty (LEARN-02).
        """
        self._embed: EmbedServiceProtocol = embed_service
        self._feedback_tracker: FeedbackTrackerProtocol | None = feedback_tracker
        self._privacy_re = re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in self.PRIVACY_KEYWORDS) + r")\b"
        )
        self._centroids = self._precompute_routes()

    def set_feedback_tracker(self, tracker: FeedbackTrackerProtocol | None) -> None:
        """Set the feedback tracker for route quality penalty (late-binding).

        Called by the composition root when the learning subsystem is
        initialized after the classifier.  Avoids direct mutation of the
        private ``_feedback_tracker`` attribute.
        """
        self._feedback_tracker = tracker

    @staticmethod
    def _cache_dir() -> str:
        """Return a writable cache directory for centroid embeddings.

        Uses the project's ``.planning/cache`` directory when running from
        the repo, and falls back to a platform temp directory otherwise
        (e.g. when the package is installed read-only).
        """
        # Prefer .planning/cache under the repo root (two levels up from gateway/)
        repo_cache = os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir, os.pardir,
            os.pardir, ".planning", "cache",
        )
        repo_cache = os.path.normpath(repo_cache)
        try:
            os.makedirs(repo_cache, exist_ok=True)
            return repo_cache
        except OSError as exc:
            logger.debug("Cannot create repo cache dir %s, falling back to temp: %s", repo_cache, exc)
        # Fallback: system temp directory
        import tempfile
        return os.path.join(tempfile.gettempdir(), "jarvis_classifier_cache")

    def _precompute_routes(self) -> "dict[str, np.ndarray]":
        """Compute centroid embeddings for each route's exemplars.

        Caches centroids to disk keyed by a hash of all exemplar texts.
        On subsequent loads, skips re-embedding if cache is valid.
        """
        import hashlib
        import numpy as np

        # Build hash of all exemplar texts + embed service class to detect changes
        hasher = hashlib.sha256()
        hasher.update(type(self._embed).__qualname__.encode())
        for route_name in sorted(self.ROUTES):
            for text in self.ROUTES[route_name]:
                hasher.update(f"{route_name}:{text}".encode())
        exemplar_hash = hasher.hexdigest()[:16]

        # Try loading from disk cache (writable directory, not inside package)
        cache_dir = self._cache_dir()
        cache_path = os.path.join(cache_dir, f"centroids_{exemplar_hash}.npz")
        try:
            if os.path.exists(cache_path):
                data = cast(Any, np.load(cache_path))
                cached_centroids = {k: data[k] for k in data.files}
                # Reject incomplete or zero-dimension caches from prior failed writes.
                cache_complete = set(cached_centroids.keys()) == set(self.ROUTES.keys())
                cache_valid = cache_complete and all(
                    centroid.ndim == 1 and centroid.shape[0] > 0
                    for centroid in cached_centroids.values()
                )
                if cache_valid:
                    logger.debug("Loaded cached centroids from %s", cache_path)
                    return cached_centroids
                else:
                    logger.warning(
                        "Centroid cache invalid or incomplete (cached=%s, expected=%s), recomputing",
                        sorted(cached_centroids.keys()), sorted(self.ROUTES.keys()),
                    )
        except (OSError, ValueError, KeyError) as exc:
            logger.debug("Failed to load centroid cache, recomputing: %s", exc)

        centroids: dict[str, np.ndarray] = {}
        for route_name, exemplars in self.ROUTES.items():
            embeddings = []
            for text in exemplars:
                try:
                    vec = self._embed.embed(text, prefix="search_query")
                    embeddings.append(np.array(vec))
                except (RuntimeError, ValueError, OSError) as exc:
                    logger.warning("Failed to embed exemplar for route %r: %s (%s)", route_name, text[:80], exc)
            if embeddings:
                centroid = np.mean(embeddings, axis=0)
                centroids[route_name] = centroid
            else:
                logger.error("All embeddings failed for route %r — route will be unreachable", route_name)

        # Save to disk cache
        try:
            os.makedirs(cache_dir, exist_ok=True)
            cast(Any, np.savez)(cache_path, **centroids)
            logger.debug("Saved centroid cache to %s", cache_path)
        except OSError as exc:
            logger.debug("Failed to save centroid cache: %s", exc)

        return centroids

    def _check_privacy(self, query: str) -> bool:
        """Return True if any privacy keyword appears in the query as a whole word."""
        return bool(self._privacy_re.search(query.lower()))

    def _resolve_model_for_route(
        self, route: str, available_models: set[str] | None = None,
    ) -> str:
        """Pick the best available model for a route.

        Local-first strategy: uses qwen3.5:4b (fast) for routine/creative/web
        queries, and qwen3.5:latest (9B) for complex/math tasks needing deeper
        reasoning.  Cloud CLIs (Claude, Codex, Gemini) are fallbacks only.
        """
        from jarvis_engine._shared import get_local_model as _get_local_model

        # Local-first: pick the right local model based on task complexity
        if route in ("math_logic", "complex"):
            # Heavy reasoning → full 9B model
            primary = _get_local_model()
        else:
            # Routine, creative, web_research → fast 4B model
            primary = _get_fast_local_model()

        if available_models is None or primary in available_models:
            return primary

        # Try static MODEL_MAP entry (empty by default in local-first mode)
        static_primary = self.MODEL_MAP.get(route)
        if static_primary and (available_models is None or static_primary in available_models):
            return static_primary

        # Escalate to cloud CLIs as fallback
        for fallback in self.MODEL_FALLBACKS.get(route, []):
            if available_models is None or fallback in available_models:
                return fallback

        # Ultimate fallback: any available local model
        local = _get_local_model()
        if available_models is None or local in available_models:
            return local
        if available_models:
            return next(iter(available_models))
        return local

    def classify(
        self,
        query: str,
        available_models: set[str] | None = None,
    ) -> tuple[str, str, float]:
        """Classify a query and return (route_name, model_name, confidence).

        Args:
            query: User query text.
            available_models: Optional set of model names that are actually
                available. If provided, the classifier will only return models
                from this set. Pass ``gateway.available_model_names()`` here.

        Privacy keywords force local routing with confidence 1.0.
        Low-confidence results default to local routing (privacy-safe).
        """
        import numpy as np

        from jarvis_engine._shared import get_local_model as _get_local_model
        local_model = _get_local_model()

        # Privacy check first -- always trumps embedding similarity
        if self._check_privacy(query):
            return ("simple_private", local_model, 1.0)

        # Embed the query and find best route by cosine similarity
        try:
            query_vec = np.array(self._embed.embed_query(query))
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Embedding service failed for classify(), falling back to local model: %s", exc)
            return ("simple_private", local_model, 0.0)

        best_route = "simple_private"
        best_sim = 0.0

        # Pre-compute query norm once (avoid redundant per-route computation)
        query_norm = float(np.linalg.norm(query_vec))
        if query_norm == 0 or not np.isfinite(query_norm):
            return ("simple_private", local_model, 0.0)

        for route_name, centroid in self._centroids.items():
            sim = self._cosine_sim(query_vec, centroid, query_norm)
            # Apply route quality penalty (LEARN-02): penalize routes with poor feedback
            if self._feedback_tracker is not None:
                try:
                    quality = self._feedback_tracker.get_route_quality(route_name)
                    if quality["total"] >= 5:  # Minimum sample threshold
                        # Scale similarity by 0.5-1.0 based on satisfaction rate
                        sim *= (0.5 + 0.5 * quality["satisfaction_rate"])
                except (KeyError, ValueError, AttributeError, RuntimeError) as exc:
                    logger.debug("Route quality penalty lookup failed: %s", exc)
            if sim > best_sim:
                best_sim = sim
                best_route = route_name

        # Default to local if confidence is below threshold
        if best_sim < self.CONFIDENCE_THRESHOLD:
            return ("simple_private", local_model, best_sim)

        if best_route == "simple_private":
            model = local_model
        else:
            model = self._resolve_model_for_route(best_route, available_models)
        return (best_route, model, best_sim)

    @staticmethod
    def _cosine_sim(a: "np.ndarray", b: "np.ndarray", norm_a: float = 0.0) -> float:
        """Compute cosine similarity between two vectors.

        If *norm_a* is provided and non-zero, it is reused to avoid
        recomputing ``np.linalg.norm(a)`` on every call.
        """
        import numpy as np

        dot = float(np.dot(a, b))
        if norm_a == 0.0:
            norm_a = float(np.linalg.norm(a))
        norm_b = float(np.linalg.norm(b))
        if norm_a < 1e-9 or norm_b < 1e-9 or not np.isfinite(norm_b):
            return 0.0
        result = dot / (norm_a * norm_b)
        return result if np.isfinite(result) else 0.0

