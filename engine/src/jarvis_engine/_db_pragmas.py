"""Shared SQLite PRAGMA configuration, connection helpers, and query utilities.

Single source of truth for database connection tuning and FTS5 query
sanitization. All modules that open SQLite connections should use
:func:`connect_db` (preferred) or :func:`configure_sqlite` instead of
issuing PRAGMAs inline.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Union


def configure_sqlite(
    conn: Union[sqlite3.Connection, Any],
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
        conn.execute("PRAGMA cache_size=-64000")  # 64 MB
        conn.execute("PRAGMA mmap_size=268435456")  # 256 MB


def connect_db(
    db_path: Union[str, Path],
    *,
    full: bool = False,
    check_same_thread: bool = True,
    timeout: float = 5.0,
) -> sqlite3.Connection:
    """Open a SQLite connection with standard PRAGMAs and Row factory.

    Parameters
    ----------
    db_path:
        Path to the database file.
    full:
        Passed to :func:`configure_sqlite`.
    check_same_thread:
        Passed to ``sqlite3.connect``.
    timeout:
        Busy-wait timeout for ``sqlite3.connect``.
    """
    conn = sqlite3.connect(
        str(db_path), timeout=timeout, check_same_thread=check_same_thread
    )
    conn.row_factory = sqlite3.Row
    configure_sqlite(conn, full=full)
    return conn


# ---------------------------------------------------------------------------
# FTS5 query sanitization (canonical home; re-exported by _shared.py)
# ---------------------------------------------------------------------------

# FTS5 special characters that must be escaped in user queries.
# Includes: " * ( ) { } [ ] : ^ ~ + - ' (all FTS5 query syntax chars).
FTS5_SPECIAL_RE = re.compile(r"""["\*\(\)\{\}\[\]:^~+\-']""")
FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH to prevent injection.

    Strips FTS5 special characters that could alter query semantics
    and removes FTS5 boolean operators.
    """
    sanitized = FTS5_SPECIAL_RE.sub(" ", query)
    # Remove FTS5 boolean operators to prevent query injection
    tokens = sanitized.split()
    tokens = [t for t in tokens if t.upper() not in FTS5_KEYWORDS]
    return " ".join(tokens).strip()


def placeholder_csv(count: int) -> str:
    """Return a bounded SQLite placeholder list for IN clauses.

    Raises ValueError if *count* is outside [1, 900].
    """
    if count <= 0 or count > 900:
        raise ValueError(f"placeholder count must be between 1 and 900, got {count}")
    return ",".join("?" for _ in range(count))
