"""Tests for jarvis_engine.memory.classify -- BranchClassifier and helpers.

Covers:
- _cosine_similarity: identical vectors, orthogonal vectors, zero vectors, dimension mismatch
- BranchClassifier: centroid initialization, classification, threshold fallback to 'general'
- BRANCH_DESCRIPTIONS: structure validation
- Error handling: failed centroid init, RuntimeError on None centroids
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.classify import (
    BRANCH_DESCRIPTIONS,
    BranchClassifier,
    _cosine_similarity,
)


# ---------------------------------------------------------------------------
# _cosine_similarity tests
# ---------------------------------------------------------------------------


class TestCosineSimilarity:

    def test_identical_vectors_return_one(self):
        """Identical normalized vectors have similarity 1.0."""
        v = [1.0, 0.0, 0.0]
        sim = _cosine_similarity(v, v)
        assert abs(sim - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self):
        """Orthogonal unit vectors have similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        sim = _cosine_similarity(a, b)
        assert abs(sim) < 1e-6

    def test_opposite_vectors_return_negative_one(self):
        """Opposite vectors have similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        sim = _cosine_similarity(a, b)
        assert abs(sim - (-1.0)) < 1e-6

    def test_dimension_mismatch_raises_value_error(self):
        """Vectors of different sizes raise ValueError."""
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="dimension mismatch"):
            _cosine_similarity(a, b)

    def test_zero_vector_a_returns_zero(self):
        """Zero norm for first vector returns 0.0."""
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        sim = _cosine_similarity(a, b)
        assert sim == 0.0

    def test_zero_vector_b_returns_zero(self):
        """Zero norm for second vector returns 0.0."""
        a = [1.0, 2.0, 3.0]
        b = [0.0, 0.0, 0.0]
        sim = _cosine_similarity(a, b)
        assert sim == 0.0

    def test_known_similarity_value(self):
        """Check a hand-computed cosine similarity."""
        a = [3.0, 4.0]
        b = [4.0, 3.0]
        # dot = 12 + 12 = 24, |a| = 5, |b| = 5, sim = 24/25 = 0.96
        sim = _cosine_similarity(a, b)
        assert abs(sim - 0.96) < 1e-6

    def test_single_dimension(self):
        """Single-dimension vectors work correctly."""
        sim = _cosine_similarity([5.0], [3.0])
        assert abs(sim - 1.0) < 1e-6  # Same direction in 1D


# ---------------------------------------------------------------------------
# BRANCH_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestBranchDescriptions:

    def test_branch_descriptions_not_empty(self):
        """BRANCH_DESCRIPTIONS must have at least one branch."""
        assert len(BRANCH_DESCRIPTIONS) > 0

    def test_all_branches_have_nonempty_descriptions(self):
        """Each branch description is a non-empty string."""
        for branch, desc in BRANCH_DESCRIPTIONS.items():
            assert isinstance(branch, str) and branch
            assert isinstance(desc, str) and desc

    def test_expected_branches_present(self):
        """Core branches like ops, coding, health, finance should be present."""
        expected = {"ops", "coding", "health", "finance"}
        assert expected.issubset(set(BRANCH_DESCRIPTIONS.keys()))


# ---------------------------------------------------------------------------
# BranchClassifier
# ---------------------------------------------------------------------------


class TestBranchClassifier:

    def _make_mock_embed(self, dim: int = 4) -> MagicMock:
        """Create a mock embed service that returns distinct per-branch vectors."""
        svc = MagicMock(spec=EmbeddingService)
        call_count = [0]

        def fake_embed(text: str, prefix: str = "search_document") -> list[float]:
            call_count[0] += 1
            seed = hash(text) % 1000
            return [math.sin(seed + i * 0.5) for i in range(dim)]

        svc.embed.side_effect = fake_embed
        return svc

    def test_ensure_centroids_builds_on_first_classify(self):
        """Centroids are built lazily on the first classify call."""
        svc = self._make_mock_embed()
        clf = BranchClassifier(svc)
        assert clf._centroids is None
        # Trigger classification
        embedding = [0.5, 0.5, 0.5, 0.5]
        clf.classify(embedding)
        assert clf._centroids is not None
        assert len(clf._centroids) == len(BRANCH_DESCRIPTIONS)

    def test_centroids_not_rebuilt_on_second_call(self):
        """Centroids are computed once and cached."""
        svc = self._make_mock_embed()
        clf = BranchClassifier(svc)
        clf.classify([0.5, 0.5, 0.5, 0.5])
        first_centroids = clf._centroids
        clf.classify([0.1, 0.2, 0.3, 0.4])
        assert clf._centroids is first_centroids

    def test_classify_returns_string(self):
        """classify returns a branch name string."""
        svc = self._make_mock_embed()
        clf = BranchClassifier(svc)
        result = clf.classify([0.5, 0.5, 0.5, 0.5])
        assert isinstance(result, str)

    def test_classify_returns_general_below_threshold(self):
        """When best similarity is below threshold, return 'general'."""
        svc = MagicMock(spec=EmbeddingService)
        # All centroids return the same vector -> similarity will be high for that direction only
        # Make centroids all identical
        svc.embed.return_value = [1.0, 0.0, 0.0, 0.0]
        clf = BranchClassifier(svc)
        clf.classify([1.0, 0.0, 0.0, 0.0])  # Build centroids
        # Now test with a perpendicular vector and a very high threshold
        result = clf.classify([0.0, 0.0, 0.0, 1.0], threshold=0.99)
        # Similarity of [0,0,0,1] vs [1,0,0,0] = 0.0, so should be "general"
        assert result == "general"

    def test_classify_returns_best_matching_branch(self):
        """classify returns the branch whose centroid is most similar."""
        svc = MagicMock(spec=EmbeddingService)
        # Give each branch a unique unit vector centroid
        branch_names = list(BRANCH_DESCRIPTIONS.keys())
        dim = len(branch_names) + 1
        centroid_map = {}
        for i, name in enumerate(branch_names):
            vec = [0.0] * dim
            vec[i] = 1.0
            centroid_map[name] = vec

        call_idx = [0]

        def fake_embed(text, prefix="search_document"):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(branch_names):
                return centroid_map[branch_names[idx]]
            return [0.0] * dim

        svc.embed.side_effect = fake_embed
        clf = BranchClassifier(svc)

        # Test that classifying with the first branch's centroid returns that branch
        target_branch = branch_names[0]
        result = clf.classify(centroid_map[target_branch], threshold=0.1)
        assert result == target_branch

    def test_failed_centroid_init_resets_to_none(self):
        """If embedding fails during centroid build, _centroids stays None."""
        svc = MagicMock(spec=EmbeddingService)
        svc.embed.side_effect = RuntimeError("model failed")
        clf = BranchClassifier(svc)
        with pytest.raises(RuntimeError, match="model failed"):
            clf.classify([0.0, 0.0, 0.0, 0.0])
        assert clf._centroids is None

    def test_partial_failure_resets_centroids(self):
        """If embedding fails mid-build, centroids are reset to None (not partial)."""
        svc = MagicMock(spec=EmbeddingService)
        call_count = [0]

        def fail_on_third(text, prefix="search_document"):
            call_count[0] += 1
            if call_count[0] == 3:
                raise ValueError("embedding failed")
            return [1.0, 0.0, 0.0]

        svc.embed.side_effect = fail_on_third
        clf = BranchClassifier(svc)
        with pytest.raises(ValueError, match="embedding failed"):
            clf.classify([0.0, 0.0, 0.0])
        # Centroids should be None, not partially built
        assert clf._centroids is None
