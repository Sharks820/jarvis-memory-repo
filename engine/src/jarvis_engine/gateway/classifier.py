"""IntentClassifier: embedding-based query routing with privacy keyword detection.

Routes queries to the optimal model:
- Complex reasoning/coding -> Claude Opus (cloud)
- Routine summarization/formatting -> Claude Sonnet (cloud)
- Private/personal data -> Local Ollama (never leaves device)

Privacy keywords force local routing regardless of embedding similarity.
Low-confidence queries default to local (privacy-safe).
"""

from __future__ import annotations

import os
import re

import numpy as np


class IntentClassifier:
    """Classify user queries into routing categories using embedding similarity."""

    ROUTES: dict[str, list[str]] = {
        "complex": [
            "write a Python script that implements a binary search tree with balancing",
            "analyze this codebase and suggest architectural improvements",
            "help me debug this race condition in my threading code",
            "explain the tradeoffs between CQRS and event sourcing",
            "review this security policy and identify vulnerabilities",
        ],
        "routine": [
            "summarize this article for me",
            "rewrite this paragraph to be more concise",
            "what are the key points from this meeting transcript",
            "translate this text to French",
            "format this data as a markdown table",
        ],
        "simple_private": [
            "what's on my calendar today",
            "what medications do I take",
            "remind me about my doctor appointment",
            "what did I have for dinner yesterday",
            "show me my recent bills",
        ],
    }

    MODEL_MAP: dict[str, str] = {
        "complex": "claude-opus-4-5-20250929",
        "routine": "claude-sonnet-4-5-20250929",
        "simple_private": "qwen3:14b",
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
        self._centroids = self._precompute_routes()

    def _precompute_routes(self) -> dict[str, np.ndarray]:
        """Compute centroid embeddings for each route's exemplars."""
        centroids: dict[str, np.ndarray] = {}
        for route_name, exemplars in self.ROUTES.items():
            embeddings = []
            for text in exemplars:
                vec = self._embed.embed(text, prefix="search_query")
                embeddings.append(np.array(vec))
            centroid = np.mean(embeddings, axis=0)
            centroids[route_name] = centroid
        return centroids

    def _check_privacy(self, query: str) -> bool:
        """Return True if any privacy keyword appears in the query as a whole word."""
        query_lower = query.lower()
        for keyword in self.PRIVACY_KEYWORDS:
            # Use word boundaries to prevent substring false positives
            # (e.g. "bill" matching "billboard", "son" matching "reason")
            if re.search(r"\b" + re.escape(keyword) + r"\b", query_lower):
                return True
        return False

    def classify(self, query: str) -> tuple[str, str, float]:
        """Classify a query and return (route_name, model_name, confidence).

        Privacy keywords force local routing with confidence 1.0.
        Low-confidence results default to local routing (privacy-safe).
        """
        # Privacy check first -- always trumps embedding similarity
        if self._check_privacy(query):
            local_model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")
            return ("simple_private", local_model, 1.0)

        # Embed the query and find best route by cosine similarity
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
            local_model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")
            return ("simple_private", local_model, best_sim)

        if best_route == "simple_private":
            model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")
        else:
            model = self.MODEL_MAP.get(best_route, os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b"))
        return (best_route, model, best_sim)

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
