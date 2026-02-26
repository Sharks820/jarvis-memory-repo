"""Memory relevance scoring with frequency, recency, and connectedness factors.

Provides a BM25-inspired scoring function that combines:
- Frequency boost (diminishing returns via log)
- Recency decay (exponential with 30-day half-life)
- Knowledge graph connection bonus
"""

from __future__ import annotations

import math


def compute_relevance_score(
    access_count: int,
    days_since_access: float,
    days_since_creation: float,
    connection_count: int = 0,
) -> float:
    """Compute a relevance score combining frequency, recency, and connectedness.

    Uses a modified BM25-like scoring with exponential time decay.

    Args:
        access_count: Number of times this record has been accessed.
        days_since_access: Days since the record was last accessed.
        days_since_creation: Days since the record was created.
        connection_count: Number of knowledge graph connections.

    Returns:
        Score in [0.0, 1.0] range. Higher means more relevant.
    """
    # Frequency boost (diminishing returns via log, normalised around 10 accesses)
    freq_score = math.log1p(max(access_count, 0)) / math.log1p(10)

    # Recency (exponential decay with half-life of 30 days)
    recency_score = math.exp(-0.693 * max(days_since_access, 0.0) / 30)

    # Connection bonus (well-connected facts are more important, capped at 1.0)
    connection_score = min(max(connection_count, 0) / 5.0, 1.0)

    # Weighted combination
    raw = 0.4 * freq_score + 0.4 * recency_score + 0.2 * connection_score

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, raw))


def classify_tier_by_relevance(
    relevance_score: float,
    days_since_creation: float,
) -> str:
    """Suggest a memory tier based on relevance score and age.

    Tiers:
        hot:     relevance >= 0.7
        warm:    relevance >= 0.4
        cold:    relevance >= 0.15
        archive: relevance < 0.15
    """
    if relevance_score >= 0.7:
        return "hot"
    if relevance_score >= 0.4:
        return "warm"
    if relevance_score >= 0.15:
        return "cold"
    return "archive"
