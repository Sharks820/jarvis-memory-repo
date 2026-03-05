from __future__ import annotations

import sqlite3
import threading


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
