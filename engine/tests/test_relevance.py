"""Tests for memory relevance scoring."""

from __future__ import annotations

import pytest

from jarvis_engine.learning.relevance import (
    classify_tier_by_relevance,
    compute_relevance_score,
)


class TestComputeRelevanceScore:
    """Tests for the compute_relevance_score function."""

    def test_fresh_frequently_accessed(self):
        """Recently created, heavily accessed -> high score."""
        score = compute_relevance_score(
            access_count=20,
            days_since_access=0.0,
            days_since_creation=1.0,
            connection_count=5,
        )
        assert score > 0.8

    def test_old_never_accessed(self):
        """Old record with zero access -> low score."""
        score = compute_relevance_score(
            access_count=0,
            days_since_access=365.0,
            days_since_creation=365.0,
            connection_count=0,
        )
        assert score < 0.1

    def test_moderate_usage(self):
        """Moderate usage -> mid-range score."""
        score = compute_relevance_score(
            access_count=5,
            days_since_access=15.0,
            days_since_creation=30.0,
            connection_count=2,
        )
        assert 0.3 < score < 0.8

    def test_recency_matters(self):
        """Same access count, but recently accessed wins."""
        recent = compute_relevance_score(
            access_count=3,
            days_since_access=1.0,
            days_since_creation=30.0,
        )
        stale = compute_relevance_score(
            access_count=3,
            days_since_access=90.0,
            days_since_creation=120.0,
        )
        assert recent > stale

    def test_frequency_matters(self):
        """Higher access count -> higher score, all else equal."""
        high_freq = compute_relevance_score(
            access_count=50,
            days_since_access=5.0,
            days_since_creation=30.0,
        )
        low_freq = compute_relevance_score(
            access_count=1,
            days_since_access=5.0,
            days_since_creation=30.0,
        )
        assert high_freq > low_freq

    def test_connections_boost(self):
        """More KG connections -> higher score."""
        connected = compute_relevance_score(
            access_count=3,
            days_since_access=10.0,
            days_since_creation=30.0,
            connection_count=10,
        )
        isolated = compute_relevance_score(
            access_count=3,
            days_since_access=10.0,
            days_since_creation=30.0,
            connection_count=0,
        )
        assert connected > isolated

    def test_connection_cap(self):
        """Connection bonus caps at 5 connections (score=1.0)."""
        five = compute_relevance_score(
            access_count=0,
            days_since_access=100.0,
            days_since_creation=100.0,
            connection_count=5,
        )
        hundred = compute_relevance_score(
            access_count=0,
            days_since_access=100.0,
            days_since_creation=100.0,
            connection_count=100,
        )
        assert five == pytest.approx(hundred)

    def test_score_bounded_zero_one(self):
        """Score is always in [0.0, 1.0]."""
        # Extreme high
        high = compute_relevance_score(
            access_count=10000,
            days_since_access=0.0,
            days_since_creation=0.0,
            connection_count=100,
        )
        assert 0.0 <= high <= 1.0

        # Extreme low
        low = compute_relevance_score(
            access_count=0,
            days_since_access=10000.0,
            days_since_creation=10000.0,
            connection_count=0,
        )
        assert 0.0 <= low <= 1.0

    def test_negative_inputs_treated_as_zero(self):
        """Negative values don't cause errors."""
        score = compute_relevance_score(
            access_count=-1,
            days_since_access=-5.0,
            days_since_creation=-10.0,
            connection_count=-3,
        )
        assert 0.0 <= score <= 1.0

    def test_half_life_approximately_correct(self):
        """After 30 days with no access, recency component drops ~50%."""
        fresh = compute_relevance_score(
            access_count=0,
            days_since_access=0.0,
            days_since_creation=0.0,
            connection_count=0,
        )
        aged = compute_relevance_score(
            access_count=0,
            days_since_access=30.0,
            days_since_creation=30.0,
            connection_count=0,
        )
        # Fresh score is purely recency (0.4 * 1.0 = 0.4)
        # Aged recency is 0.4 * 0.5 = 0.2
        assert fresh == pytest.approx(0.4, abs=0.01)
        assert aged == pytest.approx(0.2, abs=0.01)


class TestClassifyTierByRelevance:
    """Tests for tier classification based on relevance score."""

    def test_hot_tier(self):
        assert classify_tier_by_relevance(0.9, 1.0) == "hot"
        assert classify_tier_by_relevance(0.7, 10.0) == "hot"

    def test_warm_tier(self):
        assert classify_tier_by_relevance(0.5, 30.0) == "warm"
        assert classify_tier_by_relevance(0.4, 60.0) == "warm"

    def test_cold_tier(self):
        assert classify_tier_by_relevance(0.3, 90.0) == "cold"
        assert classify_tier_by_relevance(0.15, 180.0) == "cold"

    def test_archive_tier(self):
        assert classify_tier_by_relevance(0.1, 365.0) == "archive"
        assert classify_tier_by_relevance(0.0, 1000.0) == "archive"

    def test_boundary_values(self):
        """Exact boundary values classify correctly."""
        assert classify_tier_by_relevance(0.7, 0.0) == "hot"
        assert classify_tier_by_relevance(0.6999, 0.0) == "warm"
        assert classify_tier_by_relevance(0.4, 0.0) == "warm"
        assert classify_tier_by_relevance(0.3999, 0.0) == "cold"
        assert classify_tier_by_relevance(0.15, 0.0) == "cold"
        assert classify_tier_by_relevance(0.1499, 0.0) == "archive"
