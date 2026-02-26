"""Track user preferences extracted from conversation patterns."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from jarvis_engine._compat import UTC

logger = logging.getLogger(__name__)


class PreferenceTracker:
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

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._init_schema()

    def _init_schema(self) -> None:
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
        """Scan a user message for preference signals.

        Returns detected (category, preference) pairs.
        """
        if not user_message or not user_message.strip():
            return []
        detected: list[tuple[str, str]] = []
        lower = user_message.lower()
        for category, prefs in self.PREFERENCE_PATTERNS.items():
            for pref_name, keywords in prefs.items():
                if any(kw in lower for kw in keywords):
                    detected.append((category, pref_name))
                    self._update_preference(category, pref_name)
        return detected

    def _update_preference(self, category: str, preference: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._db.execute("""
            INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed)
            VALUES (?, ?, 1.0, 1, ?)
            ON CONFLICT(category, preference) DO UPDATE SET
                score = score + 0.1,
                evidence_count = evidence_count + 1,
                last_observed = ?
        """, (category, preference, now, now))
        self._db.commit()

    def get_preferences(self) -> dict[str, str]:
        """Return the highest-scored preference per category."""
        cur = self._db.execute("""
            SELECT category, preference, MAX(score) as max_score
            FROM user_preferences
            GROUP BY category
        """)
        return {row[0]: row[1] for row in cur.fetchall()}

    def get_all_preferences(self) -> list[dict]:
        """Return all preferences with full details."""
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
