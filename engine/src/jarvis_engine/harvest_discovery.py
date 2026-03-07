"""Auto-harvest topic discovery helpers for daemon cycle.

Pure helper functions for topic extraction, deduplication, and SQL
constants used by ``_discover_harvest_topics`` in ``daemon_loop``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from jarvis_engine._compat import UTC
from jarvis_engine._constants import STOP_WORDS as _HARVEST_STOP_WORDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named SQL constants for _discover_harvest_topics() queries
# ---------------------------------------------------------------------------

_SQL_RECENT_SUMMARIES = """\
SELECT summary FROM records
WHERE ts >= ? AND source = 'user'
ORDER BY ts DESC
LIMIT 30"""

_SQL_SPARSE_NODES = """\
SELECT n.label, COUNT(e.edge_id) AS edge_cnt
FROM kg_nodes n
LEFT JOIN kg_edges e ON n.node_id = e.source_id
WHERE n.confidence >= 0.3
GROUP BY n.node_id
HAVING edge_cnt BETWEEN 0 AND 1
ORDER BY n.updated_at DESC
LIMIT 10"""

_SQL_RARE_RELATIONS = """\
SELECT relation, COUNT(*) AS cnt
FROM kg_edges
GROUP BY relation
HAVING cnt BETWEEN 1 AND 3
ORDER BY cnt ASC
LIMIT 5"""

_SQL_NODE_BY_RELATION = """\
SELECT n.label FROM kg_nodes n
JOIN kg_edges e ON n.node_id = e.source_id
WHERE e.relation = ?
LIMIT 1"""

_SQL_STRONG_LABELS = """\
SELECT label
FROM kg_nodes
WHERE confidence >= 0.5"""


def _extract_topic_phrases(text: str) -> list[str]:
    """Extract multi-word topic phrases (2-5 words) from a text string.

    Uses simple heuristics: splits on punctuation, filters stop words,
    keeps capitalised/meaningful consecutive word runs of 2-5 words.
    No NLP libraries required.
    """
    # Split on sentence-level punctuation and common delimiters
    fragments = re.split(r'[.!?;:,\-\|/\(\)\[\]{}"\n]+', text)
    phrases: list[str] = []
    seen_lower: set[str] = set()

    for frag in fragments:
        words = frag.strip().split()
        # Filter out stop words and very short tokens
        meaningful = [w for w in words if w.lower() not in _HARVEST_STOP_WORDS and len(w) > 1]
        if len(meaningful) < 2:
            continue
        # Take up to 5 consecutive meaningful words
        phrase = " ".join(meaningful[:5])
        # Normalise and dedup
        phrase_lower = phrase.lower()
        if phrase_lower not in seen_lower and 2 <= len(phrase.split()) <= 5:
            phrases.append(phrase)
            seen_lower.add(phrase_lower)

    return phrases


def _get_recently_harvested_topics(root: Path) -> set[str]:
    """Return lowercase topic strings that were harvested in the last 14 days.

    Reads the activity feed for HARVEST events and extracts topic names
    so we can deduplicate against them.
    """
    recent: set[str] = set()
    try:
        from jarvis_engine.activity_feed import ActivityFeed, ActivityCategory

        feed_db = root / ".planning" / "brain" / "activity_feed.db"
        if not feed_db.exists():
            return recent
        feed = ActivityFeed(db_path=feed_db)
        since = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        events = feed.query(limit=100, category=ActivityCategory.HARVEST, since=since)
        for ev in events:
            details = ev.details or {}
            # The auto-harvest log_activity stores {"topics": [...], ...}
            for t in details.get("topics", []):
                recent.add(str(t).lower().strip())
            # Also check the summary for "Auto-harvest: ..." patterns
            summary = ev.summary or ""
            if summary:
                recent.add(summary.lower().strip())
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        logger.debug("Failed to read recent harvest topics from activity feed: %s", exc)
    return recent
