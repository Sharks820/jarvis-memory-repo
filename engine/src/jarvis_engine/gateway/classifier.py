"""IntentClassifier: embedding-based query routing with privacy keyword detection.

Routes queries to the optimal model:
- Math/logic reasoning -> Claude Opus (cloud, best reasoning)
- Complex coding/architecture -> Kimi K2 via Groq (fast, great code quality)
- Routine summarization/formatting -> Kimi K2 via Groq (fast cloud)
- Private/personal data -> Local Ollama (never leaves device)

Privacy keywords force local routing regardless of embedding similarity.
Low-confidence queries default to local (privacy-safe).
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import numpy as np


class IntentClassifier:
    """Classify user queries into routing categories using embedding similarity."""

    ROUTES: dict[str, list[str]] = {
        "math_logic": [
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
        ],
        "complex": [
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
        ],
        "routine": [
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
        ],
        "simple_private": [
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
        ],
        "creative": [
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
        ],
    }

    MODEL_MAP: dict[str, str] = {
        "math_logic": "claude-opus-4-0-20250514",  # Best reasoning for math/logic (Anthropic)
        "complex": "kimi-k2",        # Best code quality via Groq (free, 200+ t/s)
        "routine": "kimi-k2",        # Same fast cloud model for routine tasks
        "creative": "claude-opus-4-0-20250514",  # Claude excels at creative tasks
        # simple_private: resolved at runtime via JARVIS_LOCAL_MODEL env var
    }

    PRIVACY_KEYWORDS: set[str] = {
        "calendar",
        "medication",
        "medications",
        "medicine",
        "pill",
        "prescription",
        "bill",
        "bills",
        "payment",
        "password",
        "personal",
        "private",
        "salary",
        "bank",
        "account",
        "doctor",
        "appointment",
        "family",
        "wife",
        "husband",
        "son",
        "daughter",
        "address",
        "phone number",
        "social security",
    }

    CONFIDENCE_THRESHOLD: float = 0.35

    def __init__(self, embed_service: object) -> None:
        """Initialize with an EmbeddingService instance.

        Args:
            embed_service: Must implement embed(text, prefix) and embed_query(query).
        """
        self._embed = embed_service
        self._privacy_re = re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in self.PRIVACY_KEYWORDS) + r")\b"
        )
        self._centroids = self._precompute_routes()

    def _precompute_routes(self) -> "dict[str, np.ndarray]":
        """Compute centroid embeddings for each route's exemplars."""
        import numpy as np

        centroids: dict[str, np.ndarray] = {}
        for route_name, exemplars in self.ROUTES.items():
            embeddings = []
            for text in exemplars:
                try:
                    vec = self._embed.embed(text, prefix="search_query")
                    embeddings.append(np.array(vec))
                except Exception:
                    logger.warning("Failed to embed exemplar for route %r: %s", route_name, text[:80])
            if embeddings:
                centroid = np.mean(embeddings, axis=0)
                centroids[route_name] = centroid
        return centroids

    def _check_privacy(self, query: str) -> bool:
        """Return True if any privacy keyword appears in the query as a whole word."""
        return bool(self._privacy_re.search(query.lower()))

    def classify(self, query: str) -> tuple[str, str, float]:
        """Classify a query and return (route_name, model_name, confidence).

        Privacy keywords force local routing with confidence 1.0.
        Low-confidence results default to local routing (privacy-safe).
        """
        # Privacy check first -- always trumps embedding similarity
        if self._check_privacy(query):
            local_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
            return ("simple_private", local_model, 1.0)

        # Embed the query and find best route by cosine similarity
        import numpy as np

        query_vec = np.array(self._embed.embed_query(query))

        best_route = "simple_private"
        best_sim = -1.0

        for route_name, centroid in self._centroids.items():
            sim = self._cosine_sim(query_vec, centroid)
            if sim > best_sim:
                best_sim = sim
                best_route = route_name

        # Default to local if confidence is below threshold
        if best_sim < self.CONFIDENCE_THRESHOLD:
            local_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
            return ("simple_private", local_model, best_sim)

        if best_route == "simple_private":
            model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
        else:
            model = self.MODEL_MAP.get(best_route, os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b"))
        return (best_route, model, best_sim)

    @staticmethod
    def _cosine_sim(a: "np.ndarray", b: "np.ndarray") -> float:
        """Compute cosine similarity between two vectors."""
        import numpy as np

        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
