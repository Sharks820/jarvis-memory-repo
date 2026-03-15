"""Track temporal usage patterns to enable proactive features."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import Counter
from datetime import datetime
from typing import TypedDict

from jarvis_engine._compat import UTC

from jarvis_engine.learning._tracker_base import LearningTrackerBase
from jarvis_engine.learning.trust import classify_learning_subject

logger = logging.getLogger(__name__)


class ContextPrediction(TypedDict):
    """Result from :meth:`UsagePatternTracker.predict_context`."""

    likely_route: str
    common_topics: list[str]
    interaction_count: int


class UsagePatternTracker(LearningTrackerBase):
    """Learn when the user asks certain types of questions by time of day/week."""

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
                CREATE TABLE IF NOT EXISTS usage_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
                    day_of_week INTEGER NOT NULL CHECK(day_of_week >= 0 AND day_of_week <= 6),
                    route TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_hour_dow ON usage_patterns(hour, day_of_week)
            """)
            self._db.commit()

    def record_interaction(
        self,
        route: str = "",
        topic: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        """Store a timestamped interaction for pattern mining."""
        ts = timestamp or datetime.now(UTC)
        hour = ts.hour
        day_of_week = ts.weekday()  # 0=Monday, 6=Sunday
        with self._write_lock:
            cur = self._db.execute(
                "INSERT INTO usage_patterns (hour, day_of_week, route, topic, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (hour, day_of_week, route, topic, ts.isoformat()),
            )
            self._db.commit()
            subject_id = str(cur.lastrowid)
        provenance = classify_learning_subject(
            subject_type="usage_pattern",
            subject_id=subject_id,
            source_channel="user",
            content=f"{route} {topic}".strip() or f"{hour}:{day_of_week}",
            mission_id=route,
        )
        self._provenance_store.record_subject(
            subject_type="usage_pattern",
            subject_id=subject_id,
            metadata=provenance,
        )
        self._provenance_store.record_policy_event(
            subject_type="usage_pattern",
            subject_id=subject_id,
            action="observe",
            verdict=provenance["promotion_state"],
            policy_mode=provenance["policy_mode"],
            reason="usage_pattern_recorded",
            metadata={"route": route, "topic": topic, "hour": hour, "day_of_week": day_of_week},
        )

    def predict_context(self, hour: int, day_of_week: int) -> ContextPrediction:
        """Predict likely user context based on historical patterns.

        Returns dict with:
            likely_route: most common route at this time
            common_topics: list of most common topics at this time
            interaction_count: total interactions at this time slot
        """
        with self._db_lock:
            cur = self._db.execute(
                "SELECT route, topic FROM usage_patterns "
                "WHERE hour = ? AND day_of_week = ?",
                (hour, day_of_week),
            )
            rows = cur.fetchall()

        if not rows:
            return {
                "likely_route": "",
                "common_topics": [],
                "interaction_count": 0,
            }

        route_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        for route, topic in rows:
            if route:
                route_counter[route] += 1
            if topic:
                topic_counter[topic] += 1

        likely_route = route_counter.most_common(1)[0][0] if route_counter else ""
        common_topics = [t for t, _ in topic_counter.most_common(5)]

        return {
            "likely_route": likely_route,
            "common_topics": common_topics,
            "interaction_count": len(rows),
        }

    def get_hourly_distribution(self) -> dict[int, int]:
        """Get interaction counts per hour across all days."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT hour, COUNT(*) as cnt FROM usage_patterns GROUP BY hour ORDER BY hour"
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    def get_peak_hours(self, top_n: int = 3) -> list[int]:
        """Return the top N hours with most interactions."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT hour, COUNT(*) as cnt FROM usage_patterns "
                "GROUP BY hour ORDER BY cnt DESC LIMIT ?",
                (top_n,),
            )
            return [row[0] for row in cur.fetchall()]
