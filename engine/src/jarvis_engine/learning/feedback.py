"""Detect implicit feedback signals from conversation patterns and track route quality."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import TypedDict

from jarvis_engine._shared import now_iso as _now_iso

from jarvis_engine.learning._tracker_base import LearningTrackerBase
from jarvis_engine.learning.trust import classify_learning_subject

logger = logging.getLogger(__name__)


class RouteQuality(TypedDict):
    """Quality metrics for a specific route."""

    positive_count: int
    negative_count: int
    total: int
    satisfaction_rate: float


class ResponseFeedbackTracker(LearningTrackerBase):
    """Detect implicit feedback signals from conversation patterns."""

    CORRECTION_SIGNALS = [
        "no, i meant",
        "that's not what i asked",
        "let me rephrase",
        "wrong",
        "incorrect",
        "not quite",
        "try again",
        "i said",
        "that's not right",
    ]

    SATISFACTION_SIGNALS = [
        "perfect",
        "great",
        "thanks",
        "exactly",
        "that works",
        "good job",
        "well done",
        "nice",
        "awesome",
    ]

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock | None = None,
        db_lock: threading.Lock | None = None,
    ) -> None:
        super().__init__(db, write_lock, db_lock)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._write_lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS response_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    route TEXT NOT NULL DEFAULT '',
                    feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                    user_message_snippet TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_route ON response_feedback(route)
            """)
            self._db.commit()

    def detect_feedback(self, user_message: str) -> str:
        """Detect if the user is giving implicit positive or negative feedback.

        Returns: 'positive', 'negative', or 'neutral'
        """
        if not user_message or not user_message.strip():
            return "neutral"
        lower = user_message.lower()

        if any(signal in lower for signal in self.CORRECTION_SIGNALS):
            return "negative"
        if any(signal in lower for signal in self.SATISFACTION_SIGNALS):
            return "positive"
        return "neutral"

    def record_feedback(self, user_message: str, route: str = "") -> str:
        """Detect and record feedback for a given route.

        Returns the detected feedback type.
        """
        feedback = self.detect_feedback(user_message)
        if feedback == "neutral":
            return feedback
        now = _now_iso()
        snippet = user_message[:200]
        with self._write_lock:
            cur = self._db.execute(
                "INSERT INTO response_feedback (route, feedback, user_message_snippet, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (route, feedback, snippet, now),
            )
            self._db.commit()
        subject_id = str(cur.lastrowid)
        provenance = classify_learning_subject(
            subject_type="feedback",
            subject_id=subject_id,
            source_channel="user",
            content=snippet or feedback,
            mission_id=route,
        )
        self._provenance_store.record_subject(
            subject_type="feedback",
            subject_id=subject_id,
            metadata=provenance,
        )
        self._provenance_store.record_policy_event(
            subject_type="feedback",
            subject_id=subject_id,
            action="observe",
            verdict=provenance["promotion_state"],
            policy_mode=provenance["policy_mode"],
            reason="implicit_feedback_recorded",
            metadata={"route": route, "feedback": feedback},
        )
        return feedback

    def record_explicit_feedback(
        self, quality: str, route: str = "", comment: str = "",
    ) -> None:
        """Record an explicit feedback entry (e.g. from mobile client).

        Unlike :meth:`record_feedback`, this accepts a pre-determined *quality*
        value (``"positive"``, ``"negative"``, or ``"neutral"``) and an optional
        *comment* instead of detecting sentiment from a user message.
        """
        if quality not in ("positive", "negative", "neutral"):
            raise ValueError(f"quality must be 'positive', 'negative', or 'neutral', got {quality!r}")
        now_str = _now_iso()
        snippet = comment[:200] if comment else ""
        with self._write_lock:
            cur = self._db.execute(
                "INSERT INTO response_feedback (route, feedback, user_message_snippet, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (route, quality, snippet, now_str),
            )
            self._db.commit()
        subject_id = str(cur.lastrowid)
        provenance = classify_learning_subject(
            subject_type="feedback",
            subject_id=subject_id,
            source_channel="user",
            content=snippet or quality,
            mission_id=route,
        )
        self._provenance_store.record_subject(
            subject_type="feedback",
            subject_id=subject_id,
            metadata=provenance,
        )
        self._provenance_store.record_policy_event(
            subject_type="feedback",
            subject_id=subject_id,
            action="observe",
            verdict=provenance["promotion_state"],
            policy_mode=provenance["policy_mode"],
            reason="explicit_feedback_recorded",
            metadata={"route": route, "feedback": quality},
        )

    def get_route_quality(self, route: str, last_n: int = 20) -> RouteQuality:
        """Get quality metrics for a specific route.

        Returns dict with positive_count, negative_count, total, satisfaction_rate.
        """
        with self._db_lock:
            cur = self._db.execute(
                "SELECT feedback, COUNT(*) as cnt FROM "
                "(SELECT feedback FROM response_feedback "
                "WHERE route = ? ORDER BY rowid DESC LIMIT ?) "
                "GROUP BY feedback",
                (route, last_n),
            )
            counts = {"positive": 0, "negative": 0}
            for row in cur.fetchall():
                if row[0] in counts:
                    counts[row[0]] = row[1]
        total = counts["positive"] + counts["negative"]
        rate = counts["positive"] / total if total > 0 else 0.0
        return {
            "positive_count": counts["positive"],
            "negative_count": counts["negative"],
            "total": total,
            "satisfaction_rate": rate,
        }

    def get_all_route_quality(self, last_n: int = 20) -> dict[str, dict]:
        """Get quality metrics for all routes.

        Uses a windowed query to limit to the most recent *last_n* records
        per route, matching the behaviour of :meth:`get_route_quality`.
        """
        with self._db_lock:
            cur = self._db.execute(
                "SELECT route, feedback, COUNT(*) as cnt FROM ("
                "  SELECT route, feedback, "
                "    ROW_NUMBER() OVER (PARTITION BY route ORDER BY rowid DESC) AS rn "
                "  FROM response_feedback WHERE route != ''"
                ") WHERE rn <= ? "
                "GROUP BY route, feedback",
                (last_n,),
            )
            rows = cur.fetchall()

        route_counts: dict[str, dict[str, int]] = {}
        for row in rows:
            route, feedback, cnt = row[0], row[1], row[2]
            if route not in route_counts:
                route_counts[route] = {"positive": 0, "negative": 0}
            if feedback in route_counts[route]:
                route_counts[route][feedback] = cnt

        result: dict[str, dict] = {}
        for route, counts in route_counts.items():
            total = counts["positive"] + counts["negative"]
            rate = counts["positive"] / total if total > 0 else 0.0
            result[route] = {
                "positive_count": counts["positive"],
                "negative_count": counts["negative"],
                "total": total,
                "satisfaction_rate": rate,
            }
        return result
