"""Auto-harvest topic discovery helpers for daemon cycle.

Pure helper functions for topic extraction, deduplication, SQL constants,
and the full ``discover_harvest_topics`` pipeline used by ``daemon_loop``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from jarvis_engine._compat import UTC
from jarvis_engine._constants import (
    STOP_WORDS as _HARVEST_STOP_WORDS,
    memory_db_path as _memory_db_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named SQL constants for discover_harvest_topics() queries
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


# ---------------------------------------------------------------------------
# Topic candidate helpers
# ---------------------------------------------------------------------------


def _try_add_candidate(
    topic: str,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> bool:
    """Add a topic candidate if unique and not recently harvested.

    Returns True when the candidates list has reached *max_topics* (i.e. full).
    """
    topic = topic.strip()
    if not topic or len(topic) < 4:
        return False
    tl = topic.lower()
    if tl in seen_lower or tl in recently_harvested:
        return False
    # Ensure 2-5 words
    word_count = len(topic.split())
    if word_count < 2 or word_count > 5:
        return False
    seen_lower.add(tl)
    candidates.append(topic)
    return len(candidates) >= max_topics


def _add_phrases(
    text: str,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> bool:
    """Extract topic phrases from *text* and add them as candidates.

    Returns True when candidates is full.
    """
    phrases = _extract_topic_phrases(text)
    for phrase in phrases:
        if _try_add_candidate(phrase, candidates, seen_lower, recently_harvested, max_topics):
            return True
    return len(candidates) >= max_topics


# ---------------------------------------------------------------------------
# Collection sources
# ---------------------------------------------------------------------------


def _collect_from_recent_memories(
    conn: sqlite3.Connection,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> None:
    """Source 1: Conversation-derived topics from recent memories (last 7 days)."""
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        rows = conn.execute(_SQL_RECENT_SUMMARIES, (cutoff,)).fetchall()
        for row in rows:
            summary = row["summary"] or ""
            if _add_phrases(summary, candidates, seen_lower, recently_harvested, max_topics):
                return
    except sqlite3.OperationalError as exc:
        logger.debug("Memory tables may not exist yet: %s", exc)


def _collect_from_kg_gaps(
    conn: sqlite3.Connection,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> None:
    """Source 2: KG gap analysis -- sparse nodes and rare relation types."""
    try:
        # 2a: Nodes with few outgoing edges (surface-level knowledge)
        sparse_rows = conn.execute(_SQL_SPARSE_NODES).fetchall()
        for row in sparse_rows:
            label = row["label"] or ""
            if _add_phrases(label, candidates, seen_lower, recently_harvested, max_topics):
                return

        # 2b: Relation types with few instances -- structural KG gaps
        if len(candidates) < max_topics:
            rel_rows = conn.execute(_SQL_RARE_RELATIONS).fetchall()
            for row in rel_rows:
                relation = row["relation"] or ""
                node_row = conn.execute(
                    _SQL_NODE_BY_RELATION, (relation,),
                ).fetchone()
                if node_row:
                    label = node_row["label"] or ""
                    if _add_phrases(label, candidates, seen_lower, recently_harvested, max_topics):
                        return
    except sqlite3.OperationalError as exc:
        logger.debug("KG tables may not exist yet: %s", exc)


def _collect_from_strong_kg_areas(
    conn: sqlite3.Connection,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> None:
    """Source 3: Complementary topics -- expand strong KG areas with suffixes."""
    try:
        label_rows = conn.execute(_SQL_STRONG_LABELS).fetchall()
        prefix_counts: dict[str, int] = {}
        for row in label_rows:
            label = (row["label"] or "").strip()
            words = label.split()
            if len(words) >= 2:
                prefix = " ".join(words[:2])
                if len(prefix) > 3:
                    prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        # Keep prefixes with >= 5 nodes, sorted by count descending
        strong_prefixes = sorted(
            ((p, c) for p, c in prefix_counts.items() if c >= 5),
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        suffixes = ["best practices", "advanced techniques", "common patterns"]
        suffix_idx = 0
        for prefix, _cnt in strong_prefixes:
            expanded = f"{prefix} {suffixes[suffix_idx % len(suffixes)]}"
            suffix_idx += 1
            if _try_add_candidate(expanded, candidates, seen_lower, recently_harvested, max_topics):
                return
            if len(candidates) >= max_topics:
                return
    except (sqlite3.Error, OSError) as exc:
        logger.debug("Failed to discover harvest topics from knowledge graph: %s", exc)


def _collect_from_activity_feed(
    root: Path,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> None:
    """Source 4: Activity feed fact-extraction summaries."""
    try:
        from jarvis_engine.activity_feed import ActivityFeed, ActivityCategory

        feed_db = root / ".planning" / "brain" / "activity_feed.db"
        if feed_db.exists():
            feed = ActivityFeed(db_path=feed_db)
            events = feed.query(limit=20, category=ActivityCategory.FACT_EXTRACTED)
            for ev in events:
                summary = ev.summary or ""
                if len(summary) > 5:
                    if _add_phrases(summary, candidates, seen_lower, recently_harvested, max_topics):
                        return
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        logger.debug("Failed to extract harvest topics from activity feed fact summaries: %s", exc)


def _collect_from_learning_missions(
    root: Path,
    candidates: list[str],
    seen_lower: set[str],
    recently_harvested: set[str],
    max_topics: int,
) -> None:
    """Source 5: Fallback -- completed learning mission topics."""
    try:
        from jarvis_engine.learning_missions import load_missions

        missions = load_missions(root)
        for m in reversed(missions):
            status = str(m.get("status", "")).lower()
            if status in ("completed", "done", "running"):
                topic = str(m.get("topic", "")).strip()
                if topic and len(topic.split()) >= 2:
                    if _try_add_candidate(topic, candidates, seen_lower, recently_harvested, max_topics):
                        return
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("Failed to discover harvest topics from learning missions: %s", exc)


# ---------------------------------------------------------------------------
# Main discovery pipeline
# ---------------------------------------------------------------------------


def discover_harvest_topics(root: Path) -> list[str]:
    """Discover 2-3 topics for autonomous knowledge harvesting.

    Topic sources (in priority order):
    1. Conversation-derived: recent memory entries (last 7 days) -- multi-word phrases
    2. KG gap analysis: edge relation types with few instances or high-node/low-edge areas
    3. Complementary topics: strong KG areas expanded with "best practices"/"advanced"
    4. Activity feed: recent fact extraction summaries
    5. Fallback: completed learning mission topics

    All topics are 2-5 words.  Deduplicates against recently harvested topics.
    Returns up to 3 topic strings.  Never raises -- returns [] on error.
    """
    _MAX_TOPICS = 3
    candidates: list[str] = []
    seen_lower: set[str] = set()
    recently_harvested = _get_recently_harvested_topics(root)
    args = (candidates, seen_lower, recently_harvested, _MAX_TOPICS)

    # Open a single shared SQLite connection for sources 1-3
    db_path = _memory_db_path(root)
    conn = None
    try:
        if db_path.exists():
            try:
                from jarvis_engine._db_pragmas import connect_db as _connect_db
                conn = _connect_db(db_path)
            except (sqlite3.Error, OSError) as exc:
                logger.debug("Failed to connect to memory DB: %s", exc)
                if conn is not None:
                    conn.close()
                conn = None

        if conn is not None:
            _collect_from_recent_memories(conn, *args)
            if len(candidates) < _MAX_TOPICS:
                _collect_from_kg_gaps(conn, *args)
            if len(candidates) < _MAX_TOPICS:
                _collect_from_strong_kg_areas(conn, *args)
    finally:
        if conn is not None:
            conn.close()

    if len(candidates) < _MAX_TOPICS:
        _collect_from_activity_feed(root, *args)
    if len(candidates) < _MAX_TOPICS:
        _collect_from_learning_missions(root, *args)

    return candidates[:_MAX_TOPICS]
