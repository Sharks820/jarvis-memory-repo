"""Fact lock enforcement for the knowledge graph.

Handles auto-lock promotion (confidence >= 0.9 AND sources >= 3),
owner-confirmed locks, and unlock operations.  All writes use the
shared write_lock from MemoryEngine for thread safety.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

logger = logging.getLogger(__name__)

LOCK_THRESHOLD_CONFIDENCE = 0.9
LOCK_THRESHOLD_SOURCES = 3


class FactLockManager:
    """Manages fact locking: auto-lock thresholds and owner confirmation."""

    def __init__(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        self._db = db
        self._write_lock = write_lock

    # ------------------------------------------------------------------
    # Threshold check
    # ------------------------------------------------------------------

    def should_auto_lock(self, node: dict) -> bool:
        """Check if a node meets auto-lock criteria.

        Auto-lock fires when confidence >= 0.9 AND the node has 3+ distinct sources.
        """
        confidence = float(node.get("confidence", 0.0))
        if confidence < LOCK_THRESHOLD_CONFIDENCE:
            return False

        sources_raw = node.get("sources", "[]")
        if isinstance(sources_raw, str):
            try:
                sources = json.loads(sources_raw)
            except (json.JSONDecodeError, TypeError):
                sources = []
        elif isinstance(sources_raw, list):
            sources = sources_raw
        else:
            sources = []

        return len(sources) >= LOCK_THRESHOLD_SOURCES

    # ------------------------------------------------------------------
    # Lock / unlock operations
    # ------------------------------------------------------------------

    def lock_fact(self, node_id: str, locked_by: str = "auto") -> bool:
        """Atomically lock a fact node.

        Returns True if the node was newly locked, False if already locked
        or not found.
        """
        with self._write_lock:
            cur = self._db.execute(
                """UPDATE kg_nodes
                   SET locked = 1, locked_at = datetime('now'), locked_by = ?
                   WHERE node_id = ? AND locked = 0""",
                (locked_by, node_id),
            )
            self._db.commit()
            if cur.rowcount > 0:
                logger.info("Fact %s locked by %s", node_id, locked_by)
                return True
            return False

    def owner_confirm_lock(self, node_id: str) -> bool:
        """Owner-confirmed lock -- bypasses threshold checks.

        Any fact can be locked by the owner regardless of confidence or
        source count.
        """
        return self.lock_fact(node_id, locked_by="owner")

    def unlock_fact(self, node_id: str) -> bool:
        """Unlock a previously locked fact node.

        Returns True if the node was unlocked, False if already unlocked
        or not found.
        """
        with self._write_lock:
            cur = self._db.execute(
                """UPDATE kg_nodes
                   SET locked = 0, locked_at = NULL, locked_by = NULL
                   WHERE node_id = ? AND locked = 1""",
                (node_id,),
            )
            self._db.commit()
            if cur.rowcount > 0:
                logger.info("Fact %s unlocked", node_id)
                return True
            return False

    # ------------------------------------------------------------------
    # Auto-lock after fact update
    # ------------------------------------------------------------------

    def check_and_auto_lock(self, node_id: str) -> bool:
        """Load a node and auto-lock it if it meets the threshold.

        Called after every add_fact that updates confidence or sources.
        Returns True if the node was auto-locked, False otherwise.
        """
        row = self._db.execute(
            "SELECT node_id, confidence, sources, locked FROM kg_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            return False
        if row["locked"]:
            return False  # Already locked

        node = {
            "confidence": row["confidence"],
            "sources": row["sources"],
        }
        if self.should_auto_lock(node):
            return self.lock_fact(node_id, locked_by="auto")
        return False
