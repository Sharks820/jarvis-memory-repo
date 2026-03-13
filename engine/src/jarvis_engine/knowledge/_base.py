"""Shared base class for knowledge graph managers that need DB + write lock."""

from __future__ import annotations

__all__ = ["upsert_fts_kg", "delete_fts_kg", "KGManagerBase"]

import logging
import sqlite3
import threading
from typing import Protocol

_logger = logging.getLogger(__name__)


def upsert_fts_kg(
    conn: sqlite3.Connection,
    node_id: str,
    label: str,
) -> None:
    """Update the ``fts_kg_nodes`` FTS5 index for a single node.

    FTS5 content tables have no UPDATE — the canonical pattern is
    DELETE + INSERT.  Silently no-ops if the FTS5 table does not exist.

    Contract: the caller MUST already hold whatever write lock protects
    *conn*.  This function does NOT acquire any lock.
    """
    try:
        conn.execute("DELETE FROM fts_kg_nodes WHERE node_id = ?", (node_id,))
        conn.execute(
            "INSERT INTO fts_kg_nodes(node_id, label) VALUES (?, ?)",
            (node_id, label),
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            _logger.debug(
                "FTS5 table not available, skipping index update for node %s",
                node_id,
            )
        else:
            raise


def delete_fts_kg(
    conn: sqlite3.Connection,
    node_id: str,
) -> None:
    """Remove a node from the ``fts_kg_nodes`` FTS5 index.

    Silently no-ops if the FTS5 table does not exist.

    Contract: the caller MUST already hold whatever write lock protects
    *conn*.
    """
    try:
        conn.execute("DELETE FROM fts_kg_nodes WHERE node_id = ?", (node_id,))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        _logger.debug(
            "FTS5 table not available, skipping delete for node %s",
            node_id,
        )


class KGManagerBase:
    """Common initialisation for knowledge graph sub-managers.

    Stores references to the shared SQLite connection, write lock,
    DB lock and optional KnowledgeGraph back-reference.
    """

    class _KnowledgeGraphBackref(Protocol):
        _mutation_counter: int

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        db_lock: threading.Lock | None = None,
        kg: "_KnowledgeGraphBackref | None" = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock
        self._db_lock = db_lock or threading.Lock()
        self._kg = kg
