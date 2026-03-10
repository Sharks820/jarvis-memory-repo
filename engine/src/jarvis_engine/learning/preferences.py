"""Track user preferences extracted from conversation patterns."""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from datetime import datetime, timezone
from jarvis_engine._shared import now_iso as _now_iso

from jarvis_engine.learning._tracker_base import LearningTrackerBase

logger = logging.getLogger(__name__)


class PreferenceTracker(LearningTrackerBase):
    """Extracts and stores user preferences from interactions."""

    PREFERENCE_PATTERNS: dict[str, dict[str, list[str]]] = {
        "communication_style": {
            "verbose": ["explain in detail", "tell me more", "elaborate"],
            "concise": ["briefly", "tldr", "short version", "quick answer"],
        },
        "time_preferences": {
            "morning_person": ["morning routine", "early", "first thing"],
            "night_owl": ["late night", "evening", "after hours"],
        },
        "format_preferences": {
            "lists": ["list", "bullet points", "enumerate"],
            "prose": ["paragraph", "explain", "narrative"],
            "code": ["show me code", "code example", "implementation"],
        },
    }

    _NEGATIVE_PATTERNS: dict[str, list[str]] = {
        "communication_style": [
            "don't be",
            "stop being",
            "less formal",
            "too formal",
            "too casual",
        ],
        "format_preferences": ["no bullet", "no list", "don't use", "stop using"],
        "time_preferences": [
            "not in the morning",
            "don't remind me",
            "stop scheduling",
        ],
    }

    # Maximum score to prevent unbounded growth
    _MAX_SCORE: float = 10.0

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
                CREATE TABLE IF NOT EXISTS user_preferences (
                    category TEXT NOT NULL,
                    preference TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0.0,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    last_observed TEXT NOT NULL,
                    PRIMARY KEY (category, preference)
                )
            """)
            self._db.commit()

    def observe(self, user_message: str) -> list[tuple[str, str]]:
        """Scan a user message for preference signals (positive and negative).

        Returns detected (category, preference) pairs.
        """
        if not user_message or not user_message.strip():
            return []
        detected: list[tuple[str, str]] = []
        lower = user_message.lower()

        # Check negative patterns first (they take priority over positive)
        negative_categories: set[str] = set()
        for category, phrases in self._NEGATIVE_PATTERNS.items():
            if any(phrase in lower for phrase in phrases):
                negative_categories.add(category)
                self._detect_negative_preferences(category, lower)

        for category, prefs in self.PREFERENCE_PATTERNS.items():
            if category in negative_categories:
                continue  # Skip positive detection for categories with negative signals
            for pref_name, keywords in prefs.items():
                if any(kw in lower for kw in keywords):
                    detected.append((category, pref_name))
                    self._update_preference(category, pref_name)
        return detected

    def _detect_negative_preferences(self, category: str, lower_msg: str) -> None:
        """Decrease scores for preferences in a category when negative signals are found.

        Finds existing preferences in the category and decreases their scores
        by 0.2 (clamped to 0.0 minimum).
        """
        with self._db_lock:
            cur = self._db.execute(
                "SELECT preference FROM user_preferences WHERE category = ?",
                (category,),
            )
            existing = [row[0] for row in cur.fetchall()]

        if not existing:
            return

        now = _now_iso()
        with self._write_lock:
            for pref_name in existing:
                self._db.execute(
                    """UPDATE user_preferences
                       SET score = MAX(score - 0.2, 0.0),
                           last_observed = ?
                       WHERE category = ? AND preference = ?""",
                    (now, category, pref_name),
                )
            self._db.commit()

    def _update_preference(self, category: str, preference: str) -> None:
        now = _now_iso()
        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed)
                VALUES (?, ?, 1.0, 1, ?)
                ON CONFLICT(category, preference) DO UPDATE SET
                    score = MIN(score + 0.1, ?),
                    evidence_count = evidence_count + 1,
                    last_observed = ?
            """,
                (category, preference, now, self._MAX_SCORE, now),
            )
            self._db.commit()

    @staticmethod
    def _parse_iso_timestamp(ts: str) -> datetime | None:
        """Parse an ISO-8601 timestamp string to a datetime, or None on failure."""
        if not ts:
            return None
        try:
            # Handle both aware (with +00:00/Z) and naive ISO strings
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def get_preferences(self) -> dict[str, str]:
        """Return the highest-scored preference per category.

        Applies exponential time decay (30-day half-life) based on
        ``last_observed`` so stale preferences naturally lose influence.
        Falls back to raw score if the timestamp cannot be parsed.
        """
        now = datetime.now(timezone.utc)
        with self._db_lock:
            cur = self._db.execute(
                "SELECT category, preference, score, last_observed "
                "FROM user_preferences"
            )
            rows = cur.fetchall()

        # Compute decayed scores and pick the best per category
        best: dict[
            str, tuple[str, float]
        ] = {}  # category -> (preference, decayed_score)
        for row in rows:
            category, preference, score, last_observed = row[0], row[1], row[2], row[3]
            dt = self._parse_iso_timestamp(last_observed)
            if dt is not None:
                days_since = (now - dt).total_seconds() / 86400
                decayed_score = score * math.exp(
                    -0.023 * days_since
                )  # 30-day half-life
            else:
                decayed_score = score
            if category not in best or decayed_score > best[category][1]:
                best[category] = (preference, decayed_score)

        return {cat: pref for cat, (pref, _) in best.items()}

    def get_all_preferences(self) -> list[dict]:
        """Return all preferences with full details."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT category, preference, score, evidence_count, last_observed "
                "FROM user_preferences ORDER BY category, score DESC"
            )
            return [
                {
                    "category": row[0],
                    "preference": row[1],
                    "score": row[2],
                    "evidence_count": row[3],
                    "last_observed": row[4],
                }
                for row in cur.fetchall()
            ]
