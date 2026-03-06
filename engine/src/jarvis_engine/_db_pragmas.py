"""Shared SQLite PRAGMA configuration.

Single source of truth for database connection tuning. All modules that open
SQLite connections should call :func:`configure_sqlite` instead of issuing
PRAGMAs inline.
"""

from __future__ import annotations

import sqlite3
from typing import Union


def configure_sqlite(
    conn: Union[sqlite3.Connection, "Any"],  # noqa: F821
    *,
    full: bool = False,
) -> None:
    """Apply consistent SQLite PRAGMAs.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.
    full:
        When *True* apply the complete performance configuration used by the
        primary MemoryEngine database (synchronous=NORMAL, 64 MB cache,
        256 MB mmap, foreign keys).  When *False* (default) only WAL mode and
        a 5-second busy timeout are set — suitable for lightweight secondary
        databases.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if full:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")       # 64 MB
        conn.execute("PRAGMA mmap_size=268435456")     # 256 MB
