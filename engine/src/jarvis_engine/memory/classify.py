"""Semantic branch classification using embedding cosine similarity.

Replaces the keyword-based _pick_branch() from brain_memory.py with a
semantic approach: branch descriptions are embedded into centroids, and
incoming content is classified by cosine similarity to those centroids.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    import math

    _HAS_NUMPY = False

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
    """Compute cosine similarity between two vectors.

    Uses numpy when available for ~10-50x speedup on 768-dim vectors.
    Raises ValueError if vectors have different dimensions.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")
    if _HAS_NUMPY:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a < 1e-12 or norm_b < 1e-12:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    dot = sum(x * y for x, y in zip(a, b))
    norm_a_f: float = math.sqrt(sum(x * x for x in a))
    norm_b_f: float = math.sqrt(sum(x * x for x in b))
    if norm_a_f < 1e-12 or norm_b_f < 1e-12:
        return 0.0
    return dot / (norm_a_f * norm_b_f)


class BranchClassifier:
    """Classifies content into memory branches using embedding cosine similarity."""

    def __init__(self, embed_service: "EmbeddingService") -> None:
        self._embed_service = embed_service
        self._centroids: dict[str, list[float]] | None = None

    def _ensure_centroids(self) -> None:
        """Lazy-compute branch centroids by embedding each branch description.

        Both centroids and content embeddings use ``prefix="search_document"``
        because classification compares stored-content vectors against branch
        descriptions (both are *documents*).  Search queries use
        ``prefix="search_query"`` only at query time, not here.

        If any centroid embedding fails, ``_centroids`` is reset to ``None``
        so the next call will retry rather than leaving a partially
        initialized dict.
        """
        if self._centroids is not None:
            return
        building: dict[str, list[float]] = {}
        try:
            for branch, description in BRANCH_DESCRIPTIONS.items():
                building[branch] = self._embed_service.embed(
                    description, prefix="search_document"
                )
        except (RuntimeError, ValueError, OSError) as exc:
            # Ensure we don't leave a partial centroid dict; the next call
            # will retry from scratch.
            logger.debug("Centroid initialization failed, will retry: %s", exc)
            self._centroids = None
            raise
        self._centroids = building

    def classify(self, text_embedding: list[float], threshold: float = 0.3) -> str:
        """Classify an embedding into the best-matching branch.

        Args:
            text_embedding: The embedding vector of the content to classify.
            threshold: Minimum cosine similarity to assign a branch.

        Returns:
            Branch name with highest similarity, or "general" if below threshold.
        """
        self._ensure_centroids()
        if self._centroids is None:
            raise RuntimeError("Branch centroids failed to initialize")

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
