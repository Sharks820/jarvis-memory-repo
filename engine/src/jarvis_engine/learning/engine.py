"""Conversation learning engine that extracts knowledge from every interaction.

Filters out trivial messages (short texts, greetings, commands) and ingests
knowledge-bearing content through the enriched pipeline with appropriate
source/kind/tag metadata.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.learning.correction_detector import CorrectionDetector
    from jarvis_engine.learning.feedback import ResponseFeedbackTracker
    from jarvis_engine.learning.preferences import PreferenceTracker
    from jarvis_engine.learning.usage_patterns import UsagePatternTracker
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline

logger = logging.getLogger(__name__)


class LearningResult(TypedDict, total=False):
    """Result from :meth:`ConversationLearningEngine.learn_from_interaction`."""

    records_created: int
    correction_detected: bool
    correction_applied: bool
    preferences_detected: list[tuple[str, str]]
    feedback_detected: str
    error: str


# Greeting prefixes that indicate non-knowledge-bearing messages (when short).
_GREETING_PREFIXES = (
    "jarvis ",
    "hey ",
    "ok ",
    "thanks",
    "thank you",
    "goodbye",
)


class ConversationLearningEngine:
    """Extracts and persists knowledge from user/assistant interactions."""

    def __init__(
        self,
        pipeline: "EnrichedIngestPipeline | None",
        kg: "KnowledgeGraph | None" = None,
        preference_tracker: "PreferenceTracker | None" = None,
        feedback_tracker: "ResponseFeedbackTracker | None" = None,
        usage_tracker: "UsagePatternTracker | None" = None,
    ) -> None:
        self._pipeline = pipeline
        self._kg = kg
        self._preference_tracker = preference_tracker
        self._feedback_tracker = feedback_tracker
        self._usage_tracker = usage_tracker
        self._correction_detector: CorrectionDetector | None = None

    def learn_from_interaction(
        self,
        user_message: str,
        assistant_response: str,
        task_id: str = "",
        route: str = "",
        topic: str = "",
    ) -> LearningResult:
        """Learn from a single interaction, returning ingestion stats.

        Filters non-knowledge-bearing messages, then ingests user messages
        as episodic memory and assistant responses as semantic memory.

        Returns:
            Dict with 'records_created' count, 'correction_detected' bool,
            'correction_applied' bool (and 'error' key on failure).
        """
        if self._pipeline is None:
            return {
                "records_created": 0,
                "correction_detected": False,
                "correction_applied": False,
                "error": "no pipeline",
            }

        correction_detected, correction_applied = self._detect_corrections(user_message)
        preferences_detected = self._extract_preferences(user_message)
        feedback_detected = self._record_feedback_and_usage(user_message, route, topic)
        records_created = self._ingest_messages(
            user_message, assistant_response, task_id
        )

        return {
            "records_created": records_created,
            "correction_detected": correction_detected,
            "correction_applied": correction_applied,
            "preferences_detected": preferences_detected,
            "feedback_detected": feedback_detected,
        }

    def _detect_corrections(self, user_message: str) -> tuple[bool, bool]:
        """Check for user corrections and apply them if found.

        Returns (correction_detected, correction_applied).
        """
        try:
            from jarvis_engine.learning.correction_detector import CorrectionDetector

            if self._correction_detector is None:
                self._correction_detector = CorrectionDetector(kg=self._kg)
            detector = self._correction_detector
            correction = detector.detect_correction(user_message)
            if correction:
                applied = detector.apply_correction(correction)
                try:
                    from jarvis_engine.memory.activity_feed import log_activity

                    log_activity(
                        "correction_applied",
                        f"Corrected: {correction.new_claim[:80]}",
                        {
                            "old_claim": correction.old_claim,
                            "new_claim": correction.new_claim,
                            "applied": applied,
                        },
                    )
                except ImportError as exc:
                    logger.warning(
                        "activity_feed not available for correction logging: %s", exc
                    )
                return True, applied
        except ImportError as exc:
            logger.warning("correction_detector not available: %s", exc)
        return False, False

    def _extract_preferences(self, user_message: str) -> list[tuple[str, str]]:
        """Extract and log user preferences from the message."""
        if self._preference_tracker is None:
            return []
        try:
            preferences = self._preference_tracker.observe(user_message)
            if preferences:
                try:
                    from jarvis_engine.memory.activity_feed import (
                        ActivityCategory,
                        log_activity,
                    )

                    for key, value in preferences:
                        log_activity(
                            ActivityCategory.PREFERENCE_LEARNED,
                            f"Learned preference: {key}={value}",
                            {"key": key, "value": value},
                        )
                except (ImportError, OSError, sqlite3.Error) as exc:
                    logger.warning("Preference activity logging failed: %s", exc)
            return preferences
        except (ValueError, TypeError, sqlite3.Error) as exc:
            logger.warning("Failed to observe preferences: %s", exc)
            return []

    def _record_feedback_and_usage(
        self,
        user_message: str,
        route: str,
        topic: str,
    ) -> str:
        """Record implicit feedback and usage patterns."""
        feedback = "neutral"
        if self._feedback_tracker is not None:
            try:
                feedback = self._feedback_tracker.record_feedback(
                    user_message, route=route
                )
            except (ValueError, TypeError, sqlite3.Error) as exc:
                logger.warning("Failed to record feedback: %s", exc)

        if self._usage_tracker is not None:
            try:
                self._usage_tracker.record_interaction(route=route, topic=topic)
            except (ValueError, TypeError, sqlite3.Error) as exc:
                logger.warning("Failed to record usage pattern: %s", exc)

        return feedback

    def _ingest_messages(
        self,
        user_message: str,
        assistant_response: str,
        task_id: str,
    ) -> int:
        """Ingest knowledge-bearing messages into the memory pipeline.

        Returns the number of records created.
        """
        assert self._pipeline is not None  # caller guarantees
        records_created = 0

        if self._is_knowledge_bearing(user_message):
            try:
                ids = self._pipeline.ingest(
                    source="conversation:user",
                    kind="episodic",
                    task_id=task_id,
                    content=user_message,
                    tags=["conversation", "user"],
                )
                records_created += len(ids)
            except (sqlite3.Error, OSError, ValueError) as exc:
                logger.warning("Failed to ingest user message: %s", exc)

        if self._is_knowledge_bearing(assistant_response):
            try:
                ids = self._pipeline.ingest(
                    source="conversation:assistant",
                    kind="episodic",
                    task_id=task_id,
                    content=assistant_response,
                    tags=["conversation", "assistant"],
                )
                records_created += len(ids)
            except (sqlite3.Error, OSError, ValueError) as exc:
                logger.warning("Failed to ingest assistant response: %s", exc)

        return records_created

    # Keywords indicating personal/factual data worth keeping even in short messages.
    # Single-word keywords checked via word-boundary (set intersection) to avoid
    # false positives like "age" in "message" or "son" in "reason".
    # Multi-word phrases checked via substring match (safe since they're specific).
    _PERSONAL_DATA_SINGLE_WORDS: frozenset[str] = frozenset({
        "name",
        "birthday",
        "born",
        "lives",
        "works",
        "prefer",
        "prefers",
        "preference",
        "allergy",
        "allergic",
        "wife",
        "husband",
        "daughter",
        "son",
        "mother",
        "father",
        "brother",
        "sister",
        "married",
        "favorite",
        "favourite",
        "address",
        "phone",
        "email",
        "job",
        "occupation",
        "school",
        "college",
        "university",
        "company",
        "weighs",
        "height",
        "diagnosed",
        "medication",
    })
    _PERSONAL_DATA_PHRASES: tuple[str, ...] = (
        "blood type",
        "credit card",
        "bank account",
    )

    @staticmethod
    def _is_knowledge_bearing(text: str) -> bool:
        """Determine if text contains extractable knowledge.

        Returns False for:
        - None/empty text
        - Text shorter than 20 characters (unless contains personal data keywords)
        - Short greetings (greeting prefix AND under 100 chars) unless they
          contain personal data keywords
        """
        if not text or not text.strip():
            return False

        stripped = text.strip()
        lower = stripped.lower()

        # Check personal data keywords FIRST (before length checks) so short
        # personal facts like "My son is Jake" are always accepted.
        # Strip punctuation from words for boundary matching (handles "son,", "email.")
        import re

        words = set(re.findall(r"[a-z]+", lower))
        has_personal_data = bool(
            words & ConversationLearningEngine._PERSONAL_DATA_SINGLE_WORDS
        ) or any(
            phrase in lower
            for phrase in ConversationLearningEngine._PERSONAL_DATA_PHRASES
        )
        if has_personal_data:
            return True

        if len(stripped) < 20:
            return False

        # Short greeting check: greeting prefix AND under 100 chars
        if len(stripped) < 100:
            for prefix in _GREETING_PREFIXES:
                if lower.startswith(prefix):
                    return False

        return True
