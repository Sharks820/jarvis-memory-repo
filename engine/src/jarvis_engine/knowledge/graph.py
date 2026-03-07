"""SQLite-persistent knowledge graph with NetworkX computation layer.

Stores fact nodes and relationship edges in SQLite tables (kg_nodes, kg_edges,
kg_contradictions).  Reconstructs a NetworkX DiGraph on demand for graph
operations (traversal, hashing).  SQLite is the source of truth; NetworkX is
the computation engine.

Thread safety: all writes go through MemoryEngine._write_lock.  Reads use
_db_lock to prevent cursor interleaving on the shared connection.

FTS5 index (fts_kg_nodes) accelerates keyword search in query_relevant_facts.
sqlite-vec index (vec_kg_nodes) enables semantic similarity search when an
EmbeddingService is available.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
from typing import TYPE_CHECKING

from jarvis_engine._shared import now_iso as _now_iso, sanitize_fts_query
from jarvis_engine._constants import EMBEDDING_DIM as _EMBEDDING_DIM
from jarvis_engine.knowledge._base import upsert_fts_kg

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """SQLite-backed knowledge graph with NetworkX bridge."""

    def __init__(
        self,
        engine: "MemoryEngine",
        embed_service: "EmbeddingService | None" = None,
    ) -> None:
        self._engine = engine
        self._db = engine.db
        self._write_lock = engine.write_lock
        self._db_lock = engine.db_lock
        self._embed_service = embed_service
        self._vec_available = getattr(engine, "_vec_available", False)
        self._mutation_counter = 0
        self._cached_graph: "nx.DiGraph | None" = None
        self._cached_gen = -1
        self._ensure_schema()

        # Initialize lock manager for auto-lock after fact updates
        from jarvis_engine.knowledge.locks import FactLockManager

        self._lock_manager = FactLockManager(self._db, self._write_lock, self._db_lock, kg=self)

    # ------------------------------------------------------------------
    # Public accessors (for handlers -- avoids direct access to private attrs)
    # ------------------------------------------------------------------

    @property
    def db(self) -> "sqlite3.Connection":
        return self._db

    @property
    def write_lock(self) -> "threading.Lock":
        return self._write_lock

    @property
    def db_lock(self) -> "threading.Lock":
        return self._db_lock

    @property
    def db_path(self) -> "Path":
        from pathlib import Path

        return Path(self._engine.db_path)

    @property
    def mutation_counter(self) -> int:
        return self._mutation_counter

    def invalidate_cache(self) -> None:
        """Invalidate the cached NetworkX graph so the next read rebuilds it."""
        self._mutation_counter += 1
        self._cached_graph = None

    def ensure_schema(self) -> None:
        """(Re-)create KG tables if they don't exist (idempotent).

        Useful after restoring a database backup to reinitialise schema
        on the fresh connection.
        """
        self._ensure_schema()

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

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_kg_nodes
                USING fts5(node_id, label);
        """)

        # vec0 virtual table must be created separately (not in executescript)
        if self._vec_available:
            try:
                self._db.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_kg_nodes
                        USING vec0(node_id TEXT PRIMARY KEY, embedding float[{_EMBEDDING_DIM}])
                    """
                )
            except sqlite3.Error as exc:
                self._vec_available = False
                logger.warning("Failed to create vec_kg_nodes table: %s", exc)

        # Backfill FTS5 index: populate fts_kg_nodes from existing kg_nodes
        # that are missing from the index (idempotent -- skips already-indexed nodes).
        # Uses subquery (NOT IN) instead of LEFT JOIN because FTS5 virtual tables
        # may not support LEFT JOIN reliably on all SQLite builds.
        try:
            missing = self._db.execute(
                """
                SELECT node_id, label FROM kg_nodes
                WHERE confidence > 0
                  AND node_id NOT IN (SELECT node_id FROM fts_kg_nodes)
                """
            ).fetchall()
            if missing:
                for row in missing:
                    self._db.execute(
                        "INSERT INTO fts_kg_nodes(node_id, label) VALUES (?, ?)",
                        (row[0], row[1]),
                    )
                logger.info("Backfilled %d nodes into fts_kg_nodes", len(missing))
        except sqlite3.Error as exc:
            logger.warning("FTS5 backfill failed (non-fatal, %s): %s", type(exc).__name__, exc)

        # Bump schema version to 2
        self._db.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (2)"
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # NetworkX bridge
    # ------------------------------------------------------------------

    def to_networkx(self, *, copy: bool = True) -> "nx.DiGraph":
        """Reconstruct full NetworkX DiGraph from SQLite tables.

        Uses generation-based caching: returns cached graph if no mutations
        have occurred since last build.  Invalidated by add_fact/add_edge/
        update_node via _mutation_counter.

        Lock strategy: _db_lock held only for SQL reads (not graph build)
        to avoid blocking concurrent read queries.  _mutation_counter is
        checked atomically (Python GIL) to prevent thundering-herd rebuilds.

        Args:
            copy: If True (default), return a defensive copy of the cached
                graph so callers cannot mutate internal state.  Pass False
                for read-only callers to avoid the O(n) copy overhead.
        """
        # Fast path: check cache under lock to prevent torn reads on
        # _cached_graph/_cached_gen pair across threads.
        with self._db_lock:
            if self._cached_graph is not None and self._cached_gen == self._mutation_counter:
                return self._cached_graph.copy() if copy else self._cached_graph

        # Read data from SQLite under _db_lock (minimal lock scope)
        with self._db_lock:
            # Re-check cache inside lock to prevent thundering herd
            if self._cached_graph is not None and self._cached_gen == self._mutation_counter:
                return self._cached_graph.copy() if copy else self._cached_graph

            cur = self._db.execute(
                "SELECT node_id, label, node_type, confidence, locked FROM kg_nodes"
            )
            nodes = cur.fetchall()

            cur = self._db.execute(
                "SELECT source_id, target_id, relation, confidence FROM kg_edges"
            )
            edges = cur.fetchall()

            gen = self._mutation_counter

        # Build graph outside lock (no DB access needed)
        import networkx as nx

        G = nx.DiGraph()

        for row in nodes:
            G.add_node(
                row[0],
                label=row[1],
                node_type=row[2],
                confidence=row[3],
                locked=bool(row[4]),
            )

        for row in edges:
            G.add_edge(row[0], row[1], relation=row[2], confidence=row[3])

        # Update cache (atomic under GIL; concurrent rebuilds are harmless
        # since they produce identical results for the same gen)
        self._cached_graph = G
        self._cached_gen = gen

        return G.copy() if copy else G

    # ------------------------------------------------------------------
    # Fact CRUD
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # add_fact helpers
    # ------------------------------------------------------------------

    def _precompute_embedding(self, label: str, node_id: str) -> bytes | None:
        """Compute vec embedding blob for *label*, or None on failure."""
        if self._embed_service is None or not self._vec_available:
            return None
        try:
            embedding = self._embed_service.embed(label, prefix="search_document")
            if len(embedding) == _EMBEDDING_DIM:
                return struct.pack(f"{len(embedding)}f", *embedding)
        except (ImportError, ValueError, struct.error) as exc:
            logger.debug("Vec embedding for KG node %s failed: %s", node_id, exc)
        return None

    def _handle_existing_node(
        self,
        node_id: str,
        label: str,
        confidence: float,
        source_record: str,
        node_type: str,
        existing: tuple,
    ) -> bool | None:
        """Process an existing node row.  Returns True/False for early exit, None to continue."""
        is_locked = bool(existing[0])
        existing_label = existing[1]
        existing_conf = existing[2]
        try:
            existing_sources = json.loads(existing[3])
        except (json.JSONDecodeError, TypeError):
            existing_sources = []

        if is_locked:
            if label != existing_label:
                self._quarantine_contradiction(
                    node_id, existing_label, label,
                    existing_conf, confidence, source=source_record,
                )
                self._db.commit()
                return False
            return True  # Same value on locked node — no-op

        # Update existing unlocked node
        if source_record and source_record not in existing_sources:
            existing_sources.append(source_record)
        self._db.execute(
            """UPDATE kg_nodes
               SET label = ?, node_type = ?,
                   confidence = MAX(confidence, ?),
                   sources = ?, updated_at = datetime('now')
               WHERE node_id = ?""",
            (label, node_type, confidence,
             json.dumps(existing_sources[-50:]), node_id),
        )
        upsert_fts_kg(self._db, node_id, label)
        return None  # continue

    def _insert_new_node(
        self, node_id: str, label: str, node_type: str,
        confidence: float, source_record: str,
    ) -> None:
        """INSERT a brand-new fact node + FTS5 entry."""
        sources = [source_record] if source_record else []
        self._db.execute(
            """INSERT INTO kg_nodes
               (node_id, label, node_type, confidence, sources)
               VALUES (?, ?, ?, ?, ?)""",
            (node_id, label, node_type, confidence, json.dumps(sources)),
        )
        upsert_fts_kg(self._db, node_id, label)

    def _upsert_vec_index(self, node_id: str, embedding_blob: bytes | None) -> None:
        """Upsert the vec embedding for *node_id* (DELETE+INSERT)."""
        if embedding_blob is None:
            return
        try:
            self._db.execute("DELETE FROM vec_kg_nodes WHERE node_id = ?", (node_id,))
            self._db.execute(
                "INSERT INTO vec_kg_nodes(node_id, embedding) VALUES (?, ?)",
                (node_id, embedding_blob),
            )
        except sqlite3.Error as exc:
            logger.debug("Vec index update for KG node %s failed: %s", node_id, exc)

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

        Embedding is computed BEFORE acquiring the write lock to avoid holding
        the lock during the (potentially slow) embedding model call.
        """
        embedding_blob = self._precompute_embedding(label, node_id)

        with self._write_lock:
            try:
                existing = self._db.execute(
                    "SELECT locked, label, confidence, sources FROM kg_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchone()

                if existing is not None:
                    result = self._handle_existing_node(
                        node_id, label, confidence, source_record, node_type, existing,
                    )
                    if result is not None:
                        return result
                else:
                    self._insert_new_node(node_id, label, node_type, confidence, source_record)

                self._upsert_vec_index(node_id, embedding_blob)
                self._db.commit()
                self._mutation_counter += 1
            except (sqlite3.Error, OSError) as exc:
                self._db.rollback()
                logger.debug("add_fact transaction failed, rolled back: %s", exc)
                raise

        try:
            self._lock_manager.check_and_auto_lock(node_id)
        except (sqlite3.Error, ValueError) as exc:
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
            try:
                cur = self._db.execute(
                    """INSERT OR IGNORE INTO kg_edges
                       (source_id, target_id, relation, confidence, source_record)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_id, target_id, relation, confidence, source_record),
                )
                self._db.commit()
                inserted = cur.rowcount > 0
                if inserted:
                    self._mutation_counter += 1
                return inserted
            except (sqlite3.Error, OSError) as exc:
                self._db.rollback()
                logger.debug("add_edge transaction failed, rolled back: %s", exc)
                raise

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
        source: str = "",
    ) -> None:
        """Quarantine a contradiction for owner review.

        Called inside the write_lock context -- does NOT commit (caller commits).
        """
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
                source,
            ),
        )
        logger.warning(
            "Contradiction quarantined for node %s: existing=%r incoming=%r",
            node_id,
            existing_value,
            incoming_value,
        )

    # ------------------------------------------------------------------
    # Read queries (protected by _db_lock for cursor interleaving safety)
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict | None:
        """Fetch a single node by ID."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM kg_nodes WHERE node_id = ?", (node_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_edges_from(self, node_id: str) -> list[dict]:
        """Fetch all outgoing edges from a node."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM kg_edges WHERE source_id = ?", (node_id,)
            )
            return [dict(row) for row in cur.fetchall()]

    def get_edges_to(self, node_id: str) -> list[dict]:
        """Fetch all incoming edges to a node."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM kg_edges WHERE target_id = ?", (node_id,)
            )
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Aggregate queries for status reporting
    # ------------------------------------------------------------------

    def count_nodes(self) -> int:
        """Total number of fact nodes."""
        with self._db_lock:
            return self._db.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]

    def count_edges(self) -> int:
        """Total number of edges."""
        with self._db_lock:
            return self._db.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]

    def count_locked(self) -> int:
        """Number of locked (immutable) fact nodes."""
        with self._db_lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE locked = 1"
            ).fetchone()[0]

    def count_pending_contradictions(self) -> int:
        """Number of unresolved contradictions."""
        with self._db_lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM kg_contradictions WHERE status = 'pending'"
            ).fetchone()[0]

    def query_relevant_facts(
        self,
        keywords: list[str],
        *,
        min_confidence: float = 0.4,
        limit: int = 10,
    ) -> list[dict]:
        """Find KG facts whose labels match any of the given keywords.

        Uses FTS5 MATCH for fast full-text search on fts_kg_nodes.  Falls back
        to SQL LIKE matching if FTS5 returns no results (handles partial matches
        that FTS5 misses, e.g. substrings within tokens).

        Returns facts sorted by confidence descending, limited to ``limit`` results.
        """
        if not keywords:
            return []

        # --- FTS5 path ---
        fts_query_parts = []
        for kw in keywords[:20]:
            sanitized = sanitize_fts_query(kw)
            if sanitized:
                fts_query_parts.append(sanitized)

        fts_results: list[dict] = []
        if fts_query_parts:
            fts_query = " OR ".join(fts_query_parts)
            try:
                with self._db_lock:
                    cur = self._db.execute(
                        """
                        SELECT n.node_id, n.label, n.node_type, n.confidence,
                               n.locked, n.updated_at, f.rank
                        FROM fts_kg_nodes f
                        JOIN kg_nodes n ON n.node_id = f.node_id
                        WHERE fts_kg_nodes MATCH ?
                          AND n.confidence >= ?
                        ORDER BY (f.rank * -1 * 0.4 + n.confidence * 0.6) DESC
                        LIMIT ?
                        """,
                        (fts_query, min_confidence, limit),
                    )
                    # Strip internal 'rank' column from public results
                    fts_results = [
                        {k: v for k, v in dict(row).items() if k != "rank"}
                        for row in cur.fetchall()
                    ]
            except sqlite3.Error as exc:
                logger.debug("FTS5 KG search failed, falling back to LIKE: %s", exc)

        if fts_results:
            return fts_results

        # --- LIKE fallback (handles partial/substring matches FTS5 misses) ---
        clauses = []
        params: list[object] = []
        for kw in keywords[:20]:
            if not kw or not kw.strip():
                continue
            sanitized = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("label LIKE ? ESCAPE '\\'")
            params.append(f"%{sanitized}%")
        if not clauses:
            return []
        params.append(min_confidence)
        params.append(limit)
        sql = (
            "SELECT node_id, label, node_type, confidence, locked, updated_at "
            "FROM kg_nodes WHERE (" + " OR ".join(clauses) + ") "
            "AND confidence >= ? "
            "ORDER BY confidence DESC LIMIT ?"
        )
        with self._db_lock:
            cur = self._db.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_relevant_facts_semantic(
        self,
        query: str,
        embed_service: "EmbeddingService | None" = None,
        *,
        limit: int = 10,
        min_confidence: float = 0.4,
    ) -> list[dict]:
        """Find KG facts by semantic similarity using sqlite-vec KNN search.

        Embeds the query using the provided embed_service (or the instance's
        default), performs KNN search on vec_kg_nodes, and returns facts
        sorted by similarity.  Filters out retracted facts (confidence=0).

        Returns empty list if sqlite-vec or embed_service is unavailable.
        """
        svc = embed_service or self._embed_service
        if svc is None or not self._vec_available:
            return []

        try:
            query_embedding = svc.embed(query, prefix="search_query")
            if len(query_embedding) != _EMBEDDING_DIM:
                logger.warning(
                    "KG semantic search dimension mismatch: got %d, expected %d",
                    len(query_embedding), _EMBEDDING_DIM,
                )
                return []

            blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
            # Oversample to allow post-filtering of retracted facts
            oversample = min(limit * 3, 200)

            with self._db_lock:
                cur = self._db.execute(
                    """
                    SELECT node_id, distance
                    FROM vec_kg_nodes
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                    """,
                    (blob, oversample),
                )
                knn_results = cur.fetchall()

                if not knn_results:
                    return []

                candidate_ids = [r[0] for r in knn_results]
                dist_map = {r[0]: r[1] for r in knn_results}

                # Filter out retracted facts and apply min_confidence
                placeholders = ",".join("?" for _ in candidate_ids)
                cur2 = self._db.execute(
                    f"""
                    SELECT node_id, label, node_type, confidence, locked, updated_at
                    FROM kg_nodes
                    WHERE node_id IN ({placeholders})
                      AND confidence >= ?
                    """,
                    candidate_ids + [min_confidence],
                )
                rows = cur2.fetchall()

            # Sort by distance (closest first), preserving KNN order
            result_map = {}
            for row in rows:
                d = dict(row)
                node_id = str(d.get("node_id", "")).strip()
                d["node_id"] = node_id
                if not node_id:
                    continue
                result_map[node_id] = d
            results = []
            for nid in candidate_ids:
                if nid in result_map:
                    fact = result_map[nid]
                    fact["distance"] = dist_map[nid]
                    results.append(fact)
                    if len(results) >= limit:
                        break

            return results

        except (sqlite3.Error, struct.error, ValueError) as exc:
            logger.warning("KG semantic search failed: %s", exc)
            return []

    def retract_facts(self, keywords: list[str]) -> int:
        """Soft-retract KG facts matching keywords by setting confidence to 0.

        Does not retract locked facts. Returns the number of facts retracted.
        Also removes retracted nodes from FTS5 and vec indexes.
        """
        if not keywords:
            return 0
        # Filter out empty/whitespace keywords to prevent LIKE '%%' matching everything
        filtered = [kw for kw in keywords[:20] if kw and kw.strip()]
        if not filtered:
            return 0
        clauses = []
        like_params: list[str] = []
        for kw in filtered:
            sanitized = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("label LIKE ? ESCAPE '\\'")
            like_params.append(f"%{sanitized}%")
        now = _now_iso()

        where_clause = " OR ".join(clauses)

        with self._write_lock:
            try:
                # Collect node_ids that will be retracted (for FTS5/vec cleanup)
                select_sql = (
                    "SELECT node_id FROM kg_nodes "
                    "WHERE (" + where_clause + ") AND confidence > 0 AND locked = 0"
                )
                cur = self._db.execute(select_sql, like_params)
                retracted_ids = [row[0] for row in cur.fetchall()]

                if not retracted_ids:
                    return 0

                # Update confidence to 0
                update_sql = (
                    "UPDATE kg_nodes SET confidence = 0.0, updated_at = ? "
                    "WHERE (" + where_clause + ") AND confidence > 0 AND locked = 0"
                )
                all_params: list[object] = [now] + like_params
                cur = self._db.execute(update_sql, all_params)
                retracted = cur.rowcount

                # Remove retracted nodes from FTS5 and vec indexes (batch DELETE,
                # chunked at 900 to stay under SQLite's 999-variable limit).
                for i in range(0, len(retracted_ids), 900):
                    chunk = retracted_ids[i : i + 900]
                    placeholders = ",".join("?" for _ in chunk)
                    self._db.execute(
                        f"DELETE FROM fts_kg_nodes WHERE node_id IN ({placeholders})",
                        chunk,
                    )
                    if self._vec_available:
                        try:
                            self._db.execute(
                                f"DELETE FROM vec_kg_nodes WHERE node_id IN ({placeholders})",
                                chunk,
                            )
                        except sqlite3.Error as exc:
                            logger.debug("KG chunk extraction failed: %s", exc)

                self._db.commit()
                if retracted > 0:
                    self._mutation_counter += 1
                return retracted
            except (sqlite3.Error, OSError) as exc:
                self._db.rollback()
                logger.debug("soft_retract transaction failed, rolled back: %s", exc)
                raise
