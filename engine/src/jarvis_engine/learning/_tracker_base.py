from __future__ import annotations

import sqlite3
import threading

from jarvis_engine.learning.provenance import LearningProvenanceStore


class LearningTrackerBase:
    """Shared DB/lock wiring for learning tracker modules."""

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock | None = None,
        db_lock: threading.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock or threading.Lock()
        self._db_lock = db_lock or threading.Lock()
        self._provenance_store = LearningProvenanceStore(
            db=self._db,
            write_lock=self._write_lock,
            db_lock=self._db_lock,
        )
