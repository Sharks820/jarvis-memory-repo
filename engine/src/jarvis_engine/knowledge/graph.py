"""SQLite-persistent knowledge graph with NetworkX computation layer.

Stores fact nodes and relationship edges in SQLite tables (kg_nodes, kg_edges,
kg_contradictions).  Reconstructs a NetworkX DiGraph on demand for graph
operations (traversal, hashing).  SQLite is the source of truth; NetworkX is
the computation engine.

Thread safety: all writes go through MemoryEngine._write_lock.  Reads are
lock-free (WAL mode allows concurrent readers alongside a single writer).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """SQLite-backed knowledge graph with NetworkX bridge."""

    def __init__(self, engine: "MemoryEngine") -> None:
        self._engine = engine
        self._db = engine._db
        self._write_lock = engine._write_lock
        self._ensure_schema()

        # Initialize lock manager for auto-lock after fact updates
        from jarvis_engine.knowledge.locks import FactLockManager

        self._lock_manager = FactLockManager(self._db, self._write_lock)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create kg tables if they don't exist (idempotent)."""
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(node_type);
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_locked ON kg_nodes(locked);

            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_record TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (source_id) REFERENCES kg_nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES kg_nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_edges_unique
                ON kg_edges(source_id, target_id, relation);

            CREATE TABLE IF NOT EXISTS kg_contradictions (
                contradiction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                existing_value TEXT NOT NULL,
                incoming_value TEXT NOT NULL,
                existing_confidence REAL NOT NULL,
                incoming_confidence REAL NOT NULL,
                incoming_source TEXT DEFAULT NULL,
                record_id TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                resolved_at TEXT DEFAULT NULL,
                resolution TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (node_id) REFERENCES kg_nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_kg_contradictions_status
                ON kg_contradictions(status);
            CREATE INDEX IF NOT EXISTS idx_kg_contradictions_node
                ON kg_contradictions(node_id);
        """)

        # Bump schema version to 2
        self._db.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (2)"
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # NetworkX bridge
    # ------------------------------------------------------------------

    def to_networkx(self) -> nx.DiGraph:
        """Reconstruct full NetworkX DiGraph from SQLite tables.

        Returns a fresh DiGraph every call -- never cached (see research
        guidance on stale graph pitfall).
        """
        G = nx.DiGraph()

        # Load nodes
        cur = self._db.execute(
            "SELECT node_id, label, node_type, confidence, locked FROM kg_nodes"
        )
        for row in cur.fetchall():
            G.add_node(
                row[0],
                label=row[1],
                node_type=row[2],
                confidence=row[3],
                locked=bool(row[4]),
            )

        # Load edges
        cur = self._db.execute(
            "SELECT source_id, target_id, relation, confidence FROM kg_edges"
        )
        for row in cur.fetchall():
            G.add_edge(row[0], row[1], relation=row[2], confidence=row[3])

        return G

    # ------------------------------------------------------------------
    # Fact CRUD
    # ------------------------------------------------------------------

    def add_fact(
        self,
        node_id: str,
        label: str,
        confidence: float,
        source_record: str = "",
        node_type: str = "fact",
    ) -> bool:
        """Add or update a fact node.  Returns False if blocked by lock.

        - If node exists AND locked AND label differs: quarantine contradiction, return False.
        - If node exists AND locked AND label matches: no-op, return True.
        - If node exists AND unlocked: update with MAX(confidence), append source.
        - If node does not exist: INSERT new node.
        """
        with self._write_lock:
            existing = self._db.execute(
                "SELECT locked, label, confidence, sources FROM kg_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()

            if existing is not None:
                is_locked = bool(existing[0])
                existing_label = existing[1]
                existing_conf = existing[2]
                existing_sources = json.loads(existing[3])

                if is_locked:
                    if label != existing_label:
                        self._quarantine_contradiction(
                            node_id,
                            existing_label,
                            label,
                            existing_conf,
                            confidence,
                            source_record,
                            source_record,
                        )
                        return False
                    return True  # Same value on locked node -- no-op

                # Update existing unlocked node
                if source_record and source_record not in existing_sources:
                    existing_sources.append(source_record)
                self._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, node_type = ?,
                           confidence = MAX(confidence, ?),
                           sources = ?,
                           updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (
                        label,
                        node_type,
                        confidence,
                        json.dumps(existing_sources[-50:]),  # cap at 50
                        node_id,
                    ),
                )
            else:
                # New node
                sources = [source_record] if source_record else []
                self._db.execute(
                    """INSERT INTO kg_nodes
                       (node_id, label, node_type, confidence, sources)
                       VALUES (?, ?, ?, ?, ?)""",
                    (node_id, label, node_type, confidence, json.dumps(sources)),
                )

            self._db.commit()

        # Auto-lock check (outside write_lock -- lock_fact acquires its own)
        try:
            self._lock_manager.check_and_auto_lock(node_id)
        except Exception as exc:
            logger.debug("Auto-lock check failed for %s: %s", node_id, exc)

        return True

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        confidence: float = 0.5,
        source_record: str = "",
    ) -> bool:
        """Add a directed edge.  INSERT OR IGNORE handles dedup via UNIQUE index.

        Returns True if a new edge was inserted, False if it already existed.
        """
        with self._write_lock:
            cur = self._db.execute(
                """INSERT OR IGNORE INTO kg_edges
                   (source_id, target_id, relation, confidence, source_record)
                   VALUES (?, ?, ?, ?, ?)""",
                (source_id, target_id, relation, confidence, source_record),
            )
            self._db.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Contradiction quarantine
    # ------------------------------------------------------------------

    def _quarantine_contradiction(
        self,
        node_id: str,
        existing_value: str,
        incoming_value: str,
        existing_confidence: float,
        incoming_confidence: float,
        source: str,
        record_id: str,
    ) -> None:
        """Quarantine a contradiction for owner review."""
        self._db.execute(
            """INSERT INTO kg_contradictions
               (node_id, existing_value, incoming_value,
                existing_confidence, incoming_confidence,
                incoming_source, record_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                existing_value,
                incoming_value,
                existing_confidence,
                incoming_confidence,
                source,
                record_id,
            ),
        )
        self._db.commit()
        logger.warning(
            "Contradiction quarantined for node %s: existing=%r incoming=%r",
            node_id,
            existing_value,
            incoming_value,
        )

    # ------------------------------------------------------------------
    # Read queries (no lock needed -- WAL allows concurrent reads)
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict | None:
        """Fetch a single node by ID."""
        cur = self._db.execute(
            "SELECT * FROM kg_nodes WHERE node_id = ?", (node_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_edges_from(self, node_id: str) -> list[dict]:
        """Fetch all outgoing edges from a node."""
        cur = self._db.execute(
            "SELECT * FROM kg_edges WHERE source_id = ?", (node_id,)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_edges_to(self, node_id: str) -> list[dict]:
        """Fetch all incoming edges to a node."""
        cur = self._db.execute(
            "SELECT * FROM kg_edges WHERE target_id = ?", (node_id,)
        )
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Aggregate queries for status reporting
    # ------------------------------------------------------------------

    def count_nodes(self) -> int:
        """Total number of fact nodes."""
        return self._db.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]

    def count_edges(self) -> int:
        """Total number of edges."""
        return self._db.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]

    def count_locked(self) -> int:
        """Number of locked (immutable) fact nodes."""
        return self._db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE locked = 1"
        ).fetchone()[0]

    def count_pending_contradictions(self) -> int:
        """Number of unresolved contradictions."""
        return self._db.execute(
            "SELECT COUNT(*) FROM kg_contradictions WHERE status = 'pending'"
        ).fetchone()[0]
