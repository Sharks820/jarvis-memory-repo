"""SQLite + FTS5 + sqlite-vec memory engine.

Provides ACID-transactional storage for memory records with:
- Full-text keyword search via FTS5
- Semantic similarity search via sqlite-vec KNN
- WAL mode for concurrent access from daemon + API + CLI
- Write-lock serialization via threading.Lock
- Graceful degradation when sqlite-vec is unavailable
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 768


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

        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA foreign_keys=ON")

        # Load sqlite-vec extension (graceful degradation)
        try:
            import sqlite_vec

            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
            self._db.enable_load_extension(False)
        except Exception as exc:
            self._vec_available = False
            logger.warning("sqlite-vec unavailable, falling back to FTS5-only search: %s", exc)

        self._init_schema()

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
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_records
                        USING vec0(record_id TEXT PRIMARY KEY, embedding float[768])
                    """
                )
            except Exception as exc:
                self._vec_available = False
                logger.warning("Failed to create vec_records table: %s", exc)

        self._db.commit()

    def insert_record(
        self,
        record: dict,
        embedding: list[float] | None = None,
    ) -> bool:
        """Insert a record into records, fts_records, and vec_records.

        Returns True if inserted, False if duplicate (content_hash collision).
        Uses INSERT OR IGNORE for dedup via UNIQUE constraint on content_hash.
        """
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
                        record.get("tags", "[]") if isinstance(record.get("tags"), str) else str(record.get("tags", "[]")),
                        record["summary"],
                        record["content_hash"],
                        record.get("confidence", 0.72),
                        record.get("tier", "warm"),
                        record.get("access_count", 0),
                        record.get("last_accessed", ""),
                    ),
                )

                if cur.rowcount == 0:
                    # Duplicate content_hash -- INSERT OR IGNORE did nothing
                    self._db.rollback()
                    return False

                # Insert into FTS5 (contentless standalone)
                cur.execute(
                    "INSERT INTO fts_records(record_id, summary) VALUES (?, ?)",
                    (record["record_id"], record["summary"]),
                )

                # Insert into vec_records if embedding provided and vec available
                if embedding is not None and self._vec_available:
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

    def get_record(self, record_id: str) -> dict | None:
        """Fetch a single record by ID."""
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
        cur = self._db.execute(
            "SELECT * FROM records WHERE content_hash = ?",
            (content_hash,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def search_fts(self, query: str, limit: int = 30) -> list[tuple[str, float]]:
        """FTS5 keyword search returning (record_id, rank) pairs.

        Rank is negative (more negative = more relevant in FTS5).
        """
        if not query.strip():
            return []
        try:
            cur = self._db.execute(
                """
                SELECT record_id, rank
                FROM fts_records
                WHERE fts_records MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("FTS5 search failed: %s", exc)
            return []

    def search_vec(
        self,
        query_embedding: list[float],
        limit: int = 30,
    ) -> list[tuple[str, float]]:
        """sqlite-vec KNN search returning (record_id, distance) pairs.

        Returns empty list if sqlite-vec is unavailable (graceful degradation).
        """
        if not self._vec_available:
            return []
        try:
            blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
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

    def update_access(self, record_id: str) -> None:
        """Increment access_count and set last_accessed to now."""
        now = datetime.now(UTC).isoformat()
        with self._write_lock:
            self._db.execute(
                """
                UPDATE records
                SET access_count = access_count + 1,
                    last_accessed = ?
                WHERE record_id = ?
                """,
                (now, record_id),
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

    def get_all_record_ids(self) -> list[str]:
        """List all record IDs (for tier management)."""
        cur = self._db.execute("SELECT record_id FROM records")
        return [row[0] for row in cur.fetchall()]

    def count_records(self) -> int:
        """Return total record count."""
        cur = self._db.execute("SELECT COUNT(*) FROM records")
        return cur.fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
