"""Continuous learning module for automatic knowledge extraction."""

from jarvis_engine.learning.engine import ConversationLearningEngine
from jarvis_engine.learning.feedback import ResponseFeedbackTracker
from jarvis_engine.learning.preferences import PreferenceTracker
from jarvis_engine.learning.relevance import (
    classify_tier_by_relevance,
    compute_relevance_score,
)
from jarvis_engine.learning.usage_patterns import UsagePatternTracker

__all__ = [
    "ConversationLearningEngine",
    "PreferenceTracker",
    "ResponseFeedbackTracker",
    "UsagePatternTracker",
    "classify_tier_by_relevance",
    "compute_relevance_score",
]
