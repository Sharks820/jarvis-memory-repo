"""Detect implicit feedback signals from conversation patterns and track route quality."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from jarvis_engine._compat import UTC

logger = logging.getLogger(__name__)


class ResponseFeedbackTracker:
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

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._init_schema()

    def _init_schema(self) -> None:
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
        now = datetime.now(UTC).isoformat()
        snippet = user_message[:200]
        self._db.execute(
            "INSERT INTO response_feedback (route, feedback, user_message_snippet, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (route, feedback, snippet, now),
        )
        self._db.commit()
        return feedback

    def get_route_quality(self, route: str, last_n: int = 20) -> dict:
        """Get quality metrics for a specific route.

        Returns dict with positive_count, negative_count, total, satisfaction_rate.
        """
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

    def get_all_route_quality(self) -> dict[str, dict]:
        """Get quality metrics for all routes."""
        cur = self._db.execute(
            "SELECT DISTINCT route FROM response_feedback WHERE route != ''"
        )
        routes = [row[0] for row in cur.fetchall()]
        return {route: self.get_route_quality(route) for route in routes}
