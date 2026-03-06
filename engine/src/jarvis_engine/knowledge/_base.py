"""Shared base class for knowledge graph managers that need DB + write lock."""

from __future__ import annotations

import sqlite3
import threading


class KGManagerBase:
    """Common initialisation for knowledge graph sub-managers.

    Stores references to the shared SQLite connection, write lock,
    DB lock and optional KnowledgeGraph back-reference.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        db_lock: threading.Lock | None = None,
        kg: "object | None" = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock
        self._db_lock = db_lock or threading.Lock()
        self._kg = kg
