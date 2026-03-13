"""Activity feed: structured event log for observability and debugging.

Provides a SQLite-backed activity feed that records categorised events
(LLM routing decisions, fact extractions, daemon cycles, errors, etc.)
with thread-safe writes and convenient query helpers.

Thread safety: all mutations and reads go through a single threading.Lock
to prevent cursor interleaving on the shared SQLite connection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine.config import repo_root

logger = logging.getLogger(__name__)


# Categories


class ActivityCategory:
    """String constants for activity event categories."""

    LLM_ROUTING = "llm_routing"
    FACT_EXTRACTED = "fact_extracted"
    CORRECTION_APPLIED = "correction_applied"
    CONSOLIDATION = "consolidation"
    REGRESSION_CHECK = "regression_check"
    DAEMON_CYCLE = "daemon_cycle"
    PROACTIVE_TRIGGER = "proactive_trigger"
    HARVEST = "harvest"
    WEB_RESEARCH = "web_research"
    VOICE = "voice"
    ERROR = "error"
    SECURITY = "security"
    PREFERENCE_LEARNED = "preference_learned"
    MISSION_STATE_CHANGE = "mission_state_change"
    COMMAND_LIFECYCLE = "command_lifecycle"
    RESOURCE_PRESSURE = "resource_pressure"
    CONVERSATION_STATE = "conversation_state"
    VOICE_PIPELINE = "voice_pipeline"


# Event dataclass


@dataclass
class ActivityEvent:
    """Single activity event."""

    timestamp: str
    category: str
    summary: str
    details: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)


# Feed (SQLite-backed)


class ActivityFeed:
    """SQLite-backed activity feed with thread-safe reads and writes."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        max_events: int = 5000,
    ) -> None:
        if db_path is None:
            db_path = repo_root() / ".planning" / "brain" / "activity_feed.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_events = max_events
        self._lock = threading.Lock()
        self._closed = False

        from jarvis_engine._db_pragmas import connect_db

        self._db = connect_db(self._db_path, check_same_thread=False)
        self._init_schema()

    # Schema

    def _init_schema(self) -> None:
        """Create the activity_log table if it doesn't exist (idempotent)."""
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_activity_category
                ON activity_log(category);
            CREATE INDEX IF NOT EXISTS idx_activity_timestamp
                ON activity_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_activity_created_at
                ON activity_log(created_at);
        """)

    # Public API

    def _check_open(self) -> None:
        """Raise RuntimeError if the feed has been closed."""
        if self._closed:
            raise RuntimeError("ActivityFeed is closed")

    def log(
        self,
        category: str,
        summary: str,
        details: dict | None = None,
    ) -> str:
        """Log an activity event and return its event_id.  Thread-safe."""
        event_id = uuid.uuid4().hex
        ts = _now_iso()
        details_json = json.dumps(details or {})
        created_at = time.time()

        with self._lock:
            self._check_open()
            self._db.execute(
                "INSERT INTO activity_log (id, timestamp, category, summary, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, ts, category, summary, details_json, created_at),
            )
            self._db.commit()
            self._auto_prune()

        return event_id

    def query(
        self,
        limit: int = 50,
        category: str | None = None,
        since: str | None = None,
    ) -> list[ActivityEvent]:
        """Query recent events, newest first.  Optional category and since filters."""
        params: list[str | int]
        if category is not None and since is not None:
            sql = (
                "SELECT id, timestamp, category, summary, details "
                "FROM activity_log "
                "WHERE category = ? AND timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            params = [category, since, limit]
        elif category is not None:
            sql = (
                "SELECT id, timestamp, category, summary, details "
                "FROM activity_log "
                "WHERE category = ? "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            params = [category, limit]
        elif since is not None:
            sql = (
                "SELECT id, timestamp, category, summary, details "
                "FROM activity_log "
                "WHERE timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            params = [since, limit]
        else:
            sql = (
                "SELECT id, timestamp, category, summary, details "
                "FROM activity_log "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            params = [limit]

        with self._lock:
            self._check_open()
            rows = self._db.execute(sql, params).fetchall()

        return [
            ActivityEvent(
                timestamp=row["timestamp"],
                category=row["category"],
                summary=row["summary"],
                details=json.loads(row["details"]),
                event_id=row["id"],
            )
            for row in rows
        ]

    def clear_old(self, keep_days: int = 30) -> int:
        """Prune events older than *keep_days*.  Returns count deleted."""
        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat()
        with self._lock:
            self._check_open()
            cur = self._db.execute(
                "DELETE FROM activity_log WHERE timestamp < ?", (cutoff,)
            )
            self._db.commit()
            return cur.rowcount

    def stats(self) -> dict[str, int]:
        """Return event count per category for the last 24 hours."""
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        with self._lock:
            self._check_open()
            rows = self._db.execute(
                "SELECT category, COUNT(*) AS cnt "
                "FROM activity_log WHERE timestamp >= ? "
                "GROUP BY category ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
        return {row["category"]: row["cnt"] for row in rows}

    def close(self) -> None:
        """Close the database connection (idempotent).

        Returns silently even on error to support safe cleanup in finally blocks.
        Errors are logged at warning level for observability.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._db.close()
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "Failed to close activity feed database connection: %s", exc
                )

    # Internals

    def _auto_prune(self) -> None:
        """Delete oldest rows when count exceeds *max_events*.

        Must be called while self._lock is held.
        """
        row = self._db.execute("SELECT COUNT(*) AS cnt FROM activity_log").fetchone()
        count = row["cnt"]
        if count > self._max_events:
            excess = count - self._max_events
            self._db.execute(
                "DELETE FROM activity_log WHERE id IN ("
                "  SELECT id FROM activity_log ORDER BY created_at ASC LIMIT ?"
                ")",
                (excess,),
            )
            self._db.commit()

    # Context manager support -------------------------------------------------

    def __enter__(self) -> ActivityFeed:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


# Module-level singleton

_feed: ActivityFeed | None = None
_feed_lock = threading.Lock()


def get_activity_feed(db_path: Path | str | None = None) -> ActivityFeed:
    """Return (or create) the module-level ActivityFeed singleton."""
    global _feed
    if _feed is not None and not _feed._closed:
        return _feed
    with _feed_lock:
        # Double-checked locking
        if _feed is not None and not _feed._closed:
            return _feed
        _feed = ActivityFeed(db_path=db_path)
        return _feed


def log_activity(
    category: str,
    summary: str,
    details: dict | None = None,
) -> str:
    """Convenience: log an event via the module singleton."""
    return get_activity_feed().log(category, summary, details)


def _reset_feed() -> None:
    """Close and discard the module-level singleton.  Test-only."""
    global _feed
    with _feed_lock:
        if _feed is not None:
            try:
                _feed.close()
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "Failed to close activity feed singleton during reset: %s", exc
                )
            _feed = None
