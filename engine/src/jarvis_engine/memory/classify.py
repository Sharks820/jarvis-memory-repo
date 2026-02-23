"""Semantic branch classification using embedding cosine similarity.

Replaces the keyword-based _pick_branch() from brain_memory.py with a
semantic approach: branch descriptions are embedded into centroids, and
incoming content is classified by cosine similarity to those centroids.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.embeddings import EmbeddingService

BRANCH_DESCRIPTIONS: dict[str, str] = {
    "ops": "calendar scheduling meetings daily operations email organization tasks planning",
    "coding": "programming software development debugging testing code deployment build compile",
    "health": "medications prescriptions doctor appointments health pharmacy wellness exercise",
    "finance": "budget banking payments invoices expenses financial planning investment",
    "security": "authentication passwords security access control trusted devices encryption",
    "learning": "studying research education knowledge reading learning missions courses",
    "family": "children family school spouse home activities parenting",
    "communications": "phone calls text messages SMS contacts communication voicemail",
    "gaming": "video games gaming sessions steam fortnite competitive play esports",
}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


class BranchClassifier:
    """Classifies content into memory branches using embedding cosine similarity."""

    def __init__(self, embed_service: "EmbeddingService") -> None:
        self._embed_service = embed_service
        self._centroids: dict[str, list[float]] | None = None

    def _ensure_centroids(self) -> None:
        """Lazy-compute branch centroids by embedding each branch description."""
        if self._centroids is not None:
            return
        self._centroids = {}
        for branch, description in BRANCH_DESCRIPTIONS.items():
            self._centroids[branch] = self._embed_service.embed(
                description, prefix="classification"
            )

    def classify(self, text_embedding: list[float], threshold: float = 0.3) -> str:
        """Classify an embedding into the best-matching branch.

        Args:
            text_embedding: The embedding vector of the content to classify.
            threshold: Minimum cosine similarity to assign a branch.

        Returns:
            Branch name with highest similarity, or "general" if below threshold.
        """
        self._ensure_centroids()
        assert self._centroids is not None

        best_branch = "general"
        best_similarity = -1.0

        for branch, centroid in self._centroids.items():
            sim = _cosine_similarity(text_embedding, centroid)
            if sim > best_similarity:
                best_similarity = sim
                best_branch = branch

        if best_similarity < threshold:
            return "general"
        return best_branch
