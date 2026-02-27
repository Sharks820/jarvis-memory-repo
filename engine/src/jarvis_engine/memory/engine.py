"""SQLite + FTS5 + sqlite-vec memory engine.

Provides ACID-transactional storage for memory records with:
- Full-text keyword search via FTS5
- Semantic similarity search via sqlite-vec KNN
- WAL mode for concurrent access from daemon + API + CLI
- Write-lock serialization via threading.Lock for writes
- Read-lock (_db_lock) to prevent cursor interleaving on shared connection
- Graceful degradation when sqlite-vec is unavailable
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import threading
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 768

# FTS5 special characters that must be escaped in user queries
_FTS5_SPECIAL_RE = re.compile(r'["\*\(\)\{\}\[\]:^~]')
_FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


class MemoryEngine:
    """SQLite-backed memory engine with FTS5 and sqlite-vec support."""

    def __init__(
        self,
        db_path: Path,
        embed_service: "EmbeddingService | None" = None,
    ) -> None:
        self._db_path = db_path
        self._embed_service = embed_service
        self._vec_available = True
        self._write_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._closed = False

        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA cache_size=-64000")       # 64 MB
        self._db.execute("PRAGMA mmap_size=268435456")     # 256 MB

        # Load sqlite-vec extension (graceful degradation)
        try:
            import sqlite_vec

            self._db.enable_load_extension(True)
            try:
                sqlite_vec.load(self._db)
            finally:
                self._db.enable_load_extension(False)
        except Exception as exc:
            self._vec_available = False
            logger.warning("sqlite-vec unavailable, falling back to FTS5-only search: %s", exc)

        self._init_schema()

    def _check_open(self) -> None:
        """Raise RuntimeError if the engine has been closed."""
        if self._closed:
            raise RuntimeError("MemoryEngine is closed")

    def _init_schema(self) -> None:
        """Create tables and virtual tables if they don't exist."""
        cur = self._db.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                branch TEXT NOT NULL DEFAULT 'general',
                tags TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.72,
                tier TEXT NOT NULL DEFAULT 'warm',
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_hash ON records(content_hash);
            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source);
            CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);
            CREATE INDEX IF NOT EXISTS idx_records_branch ON records(branch);
            CREATE INDEX IF NOT EXISTS idx_records_tier ON records(tier);
            CREATE INDEX IF NOT EXISTS idx_records_ts ON records(ts);

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_records
                USING fts5(record_id, summary);

            CREATE TABLE IF NOT EXISTS facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                locked INTEGER NOT NULL DEFAULT 0,
                updated_utc TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO schema_version(version) VALUES (1);
            """
        )

        # vec0 virtual table must be created separately (not in executescript)
        if self._vec_available:
            try:
                cur.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_records
                        USING vec0(record_id TEXT PRIMARY KEY, embedding float[{_EMBEDDING_DIM}])
                    """
                )
            except Exception as exc:
                self._vec_available = False
                logger.warning("Failed to create vec_records table: %s", exc)

        self._db.commit()

        # Initialize knowledge graph schema (Phase 2)
        self._init_kg_schema()

    def _init_kg_schema(self) -> None:
        """Create knowledge graph tables if they don't exist (idempotent).

        Tables: kg_nodes, kg_edges, kg_contradictions.
        Bumps schema_version to 2 if not already present.
        """
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

    def insert_record(
        self,
        record: dict,
        embedding: list[float] | None = None,
    ) -> bool:
        """Insert a record into records, fts_records, and vec_records.

        Returns True if inserted, False if duplicate (content_hash collision).
        Uses INSERT OR IGNORE for dedup via UNIQUE constraint on content_hash.
        All three inserts (records, FTS5, vec) happen in a single transaction.
        """
        self._check_open()
        # Normalize tags to JSON string
        raw_tags = record.get("tags", "[]")
        if isinstance(raw_tags, list):
            tags_str = json.dumps(raw_tags)
        elif isinstance(raw_tags, str):
            tags_str = raw_tags
        else:
            tags_str = "[]"

        with self._write_lock:
            cur = self._db.cursor()
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO records
                        (record_id, ts, source, kind, task_id, branch, tags,
                         summary, content_hash, confidence, tier, access_count,
                         last_accessed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["record_id"],
                        record["ts"],
                        record["source"],
                        record["kind"],
                        record.get("task_id", ""),
                        record.get("branch", "general"),
                        tags_str,
                        record["summary"],
                        record["content_hash"],
                        record.get("confidence", 0.72),
                        record.get("tier", "warm"),
                        record.get("access_count", 0),
                        record.get("last_accessed", ""),
                    ),
                )

                if cur.rowcount == 0:
                    # Duplicate content_hash -- INSERT OR IGNORE did nothing.
                    # No rollback needed: nothing was written.
                    return False

                # Insert into FTS5 (same transaction as records insert)
                cur.execute(
                    "INSERT INTO fts_records(record_id, summary) VALUES (?, ?)",
                    (record["record_id"], record["summary"]),
                )

                # Insert into vec_records if embedding provided and vec available
                if embedding is not None and self._vec_available:
                    if len(embedding) != _EMBEDDING_DIM:
                        raise ValueError(
                            f"Embedding dimension mismatch: got {len(embedding)}, expected {_EMBEDDING_DIM}"
                        )
                    blob = struct.pack(f"{len(embedding)}f", *embedding)
                    cur.execute(
                        "INSERT INTO vec_records(record_id, embedding) VALUES (?, ?)",
                        (record["record_id"], blob),
                    )

                self._db.commit()
                return True

            except Exception:
                self._db.rollback()
                raise

    def delete_record(self, record_id: str) -> bool:
        """Delete a record from records, fts_records, and vec_records atomically.

        Returns True if the record existed and was deleted, False otherwise.
        All three deletes happen in a single transaction so the tables stay
        consistent even if the process is interrupted.
        """
        self._check_open()
        with self._write_lock:
            cur = self._db.cursor()
            try:
                cur.execute(
                    "DELETE FROM records WHERE record_id = ?",
                    (record_id,),
                )
                if cur.rowcount == 0:
                    return False

                cur.execute(
                    "DELETE FROM fts_records WHERE record_id = ?",
                    (record_id,),
                )

                if self._vec_available:
                    try:
                        cur.execute(
                            "DELETE FROM vec_records WHERE record_id = ?",
                            (record_id,),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete vec_records for %s: %s",
                            record_id,
                            exc,
                        )

                self._db.commit()
                return True

            except Exception:
                self._db.rollback()
                raise

    def delete_records_batch(self, record_ids: list[str]) -> int:
        """Bulk-delete records from records, fts_records, and vec_records.

        Returns the number of records actually deleted from the records table.
        All deletes happen in a single transaction for consistency.  Vec
        failures roll back the entire batch to prevent partial state.
        """
        if not record_ids:
            return 0

        with self._write_lock:
            cur = self._db.cursor()
            try:
                placeholders = ",".join("?" for _ in record_ids)

                cur.execute(
                    f"DELETE FROM records WHERE record_id IN ({placeholders})",
                    record_ids,
                )
                deleted = cur.rowcount

                cur.execute(
                    f"DELETE FROM fts_records WHERE record_id IN ({placeholders})",
                    record_ids,
                )

                if self._vec_available:
                    cur.execute(
                        f"DELETE FROM vec_records WHERE record_id IN ({placeholders})",
                        record_ids,
                    )

                self._db.commit()
                return deleted

            except Exception:
                self._db.rollback()
                raise

    def get_record(self, record_id: str) -> dict | None:
        """Fetch a single record by ID."""
        self._check_open()
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM records WHERE record_id = ?",
                (record_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_record_by_hash(self, content_hash: str) -> dict | None:
        """Fetch a single record by content_hash."""
        self._check_open()
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM records WHERE content_hash = ?",
                (content_hash,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize a user query for FTS5 MATCH to prevent injection.

        Strips FTS5 special characters that could alter query semantics.
        """
        sanitized = _FTS5_SPECIAL_RE.sub(" ", query)
        # Remove FTS5 boolean operators to prevent query injection
        tokens = sanitized.split()
        tokens = [t for t in tokens if t.upper() not in _FTS5_KEYWORDS]
        return " ".join(tokens).strip()

    def search_fts(self, query: str, limit: int = 30) -> list[tuple[str, float]]:
        """FTS5 keyword search returning (record_id, rank) pairs.

        Rank is negative (more negative = more relevant in FTS5).
        The query is sanitized to prevent FTS5 syntax injection.
        """
        self._check_open()
        safe_query = self._sanitize_fts_query(query)
        if not safe_query:
            return []
        try:
            with self._db_lock:
                cur = self._db.execute(
                    """
                    SELECT record_id, rank
                    FROM fts_records
                    WHERE fts_records MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, limit),
                )
                return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("FTS5 search failed for query %r: %s", safe_query, exc)
            return []

    def search_vec(
        self,
        query_embedding: list[float],
        limit: int = 30,
    ) -> list[tuple[str, float]]:
        """sqlite-vec KNN search returning (record_id, distance) pairs.

        Returns empty list if sqlite-vec is unavailable (graceful degradation).
        """
        self._check_open()
        if not self._vec_available:
            return []
        try:
            blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
            with self._db_lock:
                cur = self._db.execute(
                    """
                    SELECT record_id, distance
                    FROM vec_records
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                    """,
                    (blob, limit),
                )
                return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("Vec search failed: %s", exc)
            return []

    def search_vec_filtered(
        self,
        query_embedding: list[float],
        limit: int = 30,
        tier: str | None = None,
        source: str | None = None,
        since: str | None = None,
    ) -> list[tuple[str, float]]:
        """sqlite-vec KNN search with metadata pre-filtering.

        Pre-filtering by tier/source/date reduces scan space significantly
        for large memory stores.
        """
        self._check_open()
        if not self._vec_available:
            return []
        try:
            blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
            conditions: list[str] = []
            params: list = []

            if tier:
                conditions.append("r.tier = ?")
                params.append(tier)
            if source:
                conditions.append("r.source LIKE ?")
                params.append(f"%{source}%")
            if since:
                conditions.append("r.ts >= ?")
                params.append(since)

            if conditions:
                where_clause = " AND ".join(conditions)
                query = f"""
                    SELECT v.record_id, v.distance
                    FROM vec_records v
                    INNER JOIN records r ON v.record_id = r.record_id
                    WHERE {where_clause}
                    AND v.embedding MATCH ?
                    ORDER BY v.distance
                    LIMIT ?
                """
                params.extend([blob, limit])
            else:
                query = """
                    SELECT record_id, distance
                    FROM vec_records
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                """
                params = [blob, limit]

            with self._db_lock:
                cur = self._db.execute(query, params)
                return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("Filtered vec search failed: %s", exc)
            return []

    def update_access(self, record_id: str) -> bool:
        """Increment access_count and set last_accessed to now.

        Returns True if the record existed and was updated, False otherwise.
        """
        now = datetime.now(UTC).isoformat()
        with self._write_lock:
            cur = self._db.execute(
                """
                UPDATE records
                SET access_count = access_count + 1,
                    last_accessed = ?
                WHERE record_id = ?
                """,
                (now, record_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def update_access_batch(self, record_ids: list[str]) -> None:
        """Batch-increment access_count for multiple records in one transaction."""
        if not record_ids:
            return
        now = datetime.now(UTC).isoformat()
        with self._write_lock:
            self._db.executemany(
                """
                UPDATE records
                SET access_count = access_count + 1,
                    last_accessed = ?
                WHERE record_id = ?
                """,
                [(now, rid) for rid in record_ids],
            )
            self._db.commit()

    def update_tier(self, record_id: str, tier: str) -> None:
        """Update the tier column for a record."""
        with self._write_lock:
            self._db.execute(
                "UPDATE records SET tier = ? WHERE record_id = ?",
                (tier, record_id),
            )
            self._db.commit()

    def update_tiers_batch(self, updates: list[tuple[str, str]]) -> None:
        """Batch-update tiers: [(record_id, new_tier), ...] in one transaction.

        Each tuple is (record_id, new_tier).  The comprehension swaps the order
        to match the SQL parameter order (SET tier=? WHERE record_id=?).
        """
        if not updates:
            return
        with self._write_lock:
            self._db.executemany(
                "UPDATE records SET tier = ? WHERE record_id = ?",
                [(new_tier, record_id) for record_id, new_tier in updates],
            )
            self._db.commit()

    def get_records_batch(self, record_ids: list[str]) -> list[dict]:
        """Fetch multiple records by ID in a single query."""
        if not record_ids:
            return []
        placeholders = ",".join("?" for _ in record_ids)
        with self._db_lock:
            cur = self._db.execute(
                f"SELECT * FROM records WHERE record_id IN ({placeholders})",
                record_ids,
            )
            return [dict(row) for row in cur.fetchall()]

    def get_all_records_for_tier_maintenance(self) -> list[dict]:
        """Fetch all records with only the columns needed for tier classification."""
        with self._db_lock:
            cur = self._db.execute(
                "SELECT record_id, ts, access_count, confidence, tier FROM records"
            )
            return [dict(row) for row in cur.fetchall()]

    def get_all_record_ids(self) -> list[str]:
        """List all record IDs (for tier management)."""
        with self._db_lock:
            cur = self._db.execute("SELECT record_id FROM records")
            return [row[0] for row in cur.fetchall()]

    def count_records(self) -> int:
        """Return total record count."""
        with self._db_lock:
            cur = self._db.execute("SELECT COUNT(*) FROM records")
            return cur.fetchone()[0]

    def wal_checkpoint(self) -> None:
        """Run a passive WAL checkpoint to prevent unbounded WAL growth.

        Safe to call periodically from the daemon loop.  PASSIVE mode
        does not block concurrent readers.
        """
        try:
            with self._write_lock:
                self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                logger.debug("WAL checkpoint completed")
        except Exception as exc:
            logger.warning("WAL checkpoint failed: %s", exc)

    def close(self) -> None:
        """Close the database connection (idempotent).

        Acquires both locks to ensure no in-flight reads or writes
        touch the connection after close.
        """
        with self._write_lock:
            with self._db_lock:
                if self._closed:
                    return
                self._closed = True
                try:
                    self._db.close()
                except Exception as exc:
                    logger.debug("Failed to close MemoryEngine database connection: %s", exc)

    def __enter__(self) -> "MemoryEngine":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as exc:
            logger.debug("MemoryEngine __del__ cleanup failed: %s", exc)
