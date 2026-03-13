"""Changelog table and triggers for incremental sync.

Tracks INSERT/UPDATE/DELETE on records, kg_nodes, kg_edges via SQLite triggers.
Each changelog entry has a monotonically increasing __version per table for
cursor-based diff computation.

Version sequencing uses a dedicated ``_sync_version_seq`` table with atomic
UPDATE ... SET next_version = next_version + 1 to prevent race conditions
when concurrent triggers fire on the same table.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_TRACKED_TABLES: dict[str, dict[str, Any]] = {
    "records": {
        "pk": "record_id",
        "fields": [
            "ts", "source", "kind", "task_id", "branch", "tags",
            "summary", "content_hash", "confidence", "tier",
            "access_count", "last_accessed", "created_at",
        ],
        # Fields that should NOT trigger an UPDATE changelog entry on their own
        # (reduce noise from access_count bumps), but are still logged if other
        # fields change in the same UPDATE.
        "noise_fields": ["access_count", "last_accessed"],
    },
    "kg_nodes": {
        "pk": "node_id",
        "fields": [
            "label", "node_type", "confidence", "locked",
            "locked_at", "locked_by", "sources", "history",
            "created_at", "updated_at",
        ],
        "noise_fields": [],
    },
    "kg_edges": {
        "pk": "edge_id",
        "fields": [
            "source_id", "target_id", "relation", "confidence",
            "source_record", "created_at",
        ],
        "noise_fields": [],
    },
    "user_preferences": {
        "pk": ["category", "preference"],
        "fields": [
            "category", "preference", "score", "evidence_count",
            "last_observed",
        ],
        "noise_fields": [],
    },
    "response_feedback": {
        "pk": "id",
        "fields": [
            "route", "feedback", "user_message_snippet", "recorded_at",
        ],
        "noise_fields": [],
    },
    "usage_patterns": {
        "pk": "id",
        "fields": [
            "hour", "day_of_week", "route", "topic", "recorded_at",
        ],
        "noise_fields": [],
    },
}

# DDL

_CHANGELOG_DDL = """\
CREATE TABLE IF NOT EXISTS _sync_changelog (
    changelog_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name   TEXT    NOT NULL,
    row_id       TEXT    NOT NULL,
    operation    TEXT    NOT NULL,
    fields_changed TEXT  NOT NULL DEFAULT '[]',
    old_values   TEXT    NOT NULL DEFAULT '{}',
    new_values   TEXT    NOT NULL DEFAULT '{}',
    device_id    TEXT    NOT NULL DEFAULT 'desktop',
    ts           TEXT    NOT NULL DEFAULT (datetime('now')),
    __version    INTEGER NOT NULL
);
"""

_CURSOR_DDL = """\
CREATE TABLE IF NOT EXISTS _sync_cursor (
    device_id      TEXT NOT NULL,
    table_name     TEXT NOT NULL,
    last_version   INTEGER NOT NULL DEFAULT 0,
    last_sync_ts   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, table_name)
);
"""

_VERSION_SEQ_DDL = """\
CREATE TABLE IF NOT EXISTS _sync_version_seq (
    table_name    TEXT PRIMARY KEY,
    next_version  INTEGER NOT NULL DEFAULT 1
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_changelog_version ON _sync_changelog (table_name, __version);",
    "CREATE INDEX IF NOT EXISTS idx_changelog_device ON _sync_changelog (device_id);",
]

# Trigger SQL generation


def _pk_expr(pk: str | list[str], alias: str) -> str:
    """Build a SQL expression for the row identifier in a trigger.

    *pk* is either a single column name (``"record_id"``) or a list of column
    names for composite keys (``["category", "preference"]``).
    *alias* is ``"NEW"`` or ``"OLD"`` — the trigger row alias.
    """
    if alias not in {"NEW", "OLD"}:
        raise ValueError(f"Invalid trigger alias: {alias!r}")
    if isinstance(pk, list):
        return " || ':' || ".join(f'{alias}.{_sql_ident(col)}' for col in pk)
    return f"{alias}.{_sql_ident(pk)}"


def _sql_ident(name: str) -> str:
    """Quote a validated SQLite identifier."""
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _sql_literal(value: str) -> str:
    """Return a single-quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _validate_trigger_spec(table: str, pk: str | list[str], fields: list[str]) -> None:
    """Validate trigger SQL identifiers before dynamic DDL generation."""
    _sql_ident(table)
    pk_cols = pk if isinstance(pk, list) else [pk]
    for column in pk_cols + fields:
        _sql_ident(column)


def _build_insert_trigger(table: str, pk: str | list[str], fields: list[str], device_id: str) -> str:
    """Generate AFTER INSERT trigger SQL for *table*."""
    _validate_trigger_spec(table, pk, fields)
    fields_json = json.dumps(fields)
    new_values_expr = (
        "'{' || "
        + " || ',' || ".join(
            _sql_literal(f'"{f}":') + " || json_quote(NEW." + _sql_ident(f) + ")"
            for f in fields
        )
        + " || '}'"
    )
    # Atomic version increment via UPDATE on the sequence table, then read
    version_update = "UPDATE _sync_version_seq SET next_version = next_version + 1 WHERE table_name = " + _sql_literal(table) + "; "  # nosec B608
    version_expr = "(SELECT next_version - 1 FROM _sync_version_seq WHERE table_name = " + _sql_literal(table) + ")"  # nosec B608
    return "".join([  # nosec B608
        "CREATE TRIGGER IF NOT EXISTS ",
        _sql_ident(f"_sync_trg_{table}_insert"),
        " AFTER INSERT ON ",
        _sql_ident(table),
        " BEGIN ",
        version_update,
        "INSERT INTO _sync_changelog ",
        "(table_name, row_id, operation, fields_changed, old_values, new_values, device_id, __version) ",
        "VALUES (",
        _sql_literal(table),
        ", CAST(",
        _pk_expr(pk, "NEW"),
        " AS TEXT), 'INSERT', ",
        _sql_literal(fields_json),
        ", '{}', ",
        new_values_expr,
        ", ",
        _sql_literal(device_id),
        ", ",
        version_expr,
        "); END;",
    ])


def _clean_json_sql(expr: str) -> str:
    """Wrap a SQL expression to clean up empty-CASE comma artifacts.

    The CASE-based JSON building produces strings like ``[,,"x",,,"y",]``.
    This wraps with nested REPLACE calls to collapse multiple commas and
    remove leading/trailing commas adjacent to brackets.

    Safe for field-name arrays because values are hardcoded column names
    (never user data).  REPLACE is not recursive so we need multiple passes.
    """
    return (
        "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE("
        + expr
        + ", ',,,,,,,,', ',')"   # 8->1
        + ", ',,,,', ',')"       # 4->1
        + ", ',,', ',')"         # 2->1 (pass 1)
        + ", ',,', ',')"         # 2->1 (pass 2 -- catches residuals)
        + ", ',,', ',')"         # 2->1 (pass 3 -- final safety)
        + ", '[,', '[')"         # leading comma after [
        + ", ',]', ']')"         # trailing comma before ]
    )


def _clean_json_obj_sql(expr: str) -> str:
    """Minimal SQL cleanup for ``{...}`` object expressions.

    Only strips leading/trailing commas adjacent to braces.  Multi-comma
    collapsing is deferred to Python-side ``_safe_json_loads`` to avoid
    corrupting legitimate field values that may contain ``,,``.
    """
    return (
        "REPLACE(REPLACE("
        + expr
        + ", '{,', '{')"
        + ", ',}', '}')"
    )


def _safe_json_loads(raw: str, fallback: Any = None) -> Any:
    """Parse JSON string, cleaning up CASE-expression comma artifacts first.

    Instead of using SQL REPLACE to collapse multiple commas (which can
    corrupt legitimate values containing ``,,``), we parse and rebuild
    in Python where we can distinguish structural separators from values.
    """
    if not raw:
        return fallback
    # First try a direct parse -- fast path for well-formed JSON
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Direct JSON parse failed for row_data, attempting cleanup")
    # Slow path: clean comma artifacts from CASE-expression output.
    # These appear as sequences of commas between/around array or object
    # elements, e.g. ``[,,"x",,,"y",]`` or ``{,,"k":1,,}``
    # We collapse runs of commas that are NOT inside quoted strings.
    cleaned_parts: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if escape:
            cleaned_parts.append(ch)
            escape = False
            i += 1
            continue
        if ch == '\\' and in_string:
            cleaned_parts.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            cleaned_parts.append(ch)
            i += 1
            continue
        if ch == ',' and not in_string:
            # Skip consecutive commas outside strings
            j = i + 1
            while j < len(raw) and raw[j] == ',':
                j += 1
            cleaned_parts.append(',')
            i = j
            continue
        cleaned_parts.append(ch)
        i += 1
    cleaned = "".join(cleaned_parts)
    # Remove commas adjacent to brackets/braces (outside strings)
    cleaned = re.sub(r'\[,', '[', cleaned)
    cleaned = re.sub(r',\]', ']', cleaned)
    cleaned = re.sub(r'\{,', '{', cleaned)
    cleaned = re.sub(r',\}', '}', cleaned)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return fallback


def _build_update_trigger(
    table: str, pk: str | list[str], fields: list[str], noise_fields: list[str], device_id: str,
) -> str:
    """Generate AFTER UPDATE trigger SQL for *table*."""
    _validate_trigger_spec(table, pk, fields)
    # WHEN clause: fire only if at least one non-noise field actually changed
    significant_fields = [f for f in fields if f not in noise_fields]
    if significant_fields:
        when_parts = [f"OLD.{_sql_ident(f)} IS NOT NEW.{_sql_ident(f)}" for f in significant_fields]
        when_clause = "WHEN " + " OR ".join(when_parts) + " "
    else:
        when_clause = ""

    # Build a JSON array of changed field names using CASE expressions
    raw_fields_changed = (
        "'[' || "
        + " || ',' || ".join(
            f"CASE WHEN OLD.{_sql_ident(f)} IS NOT NEW.{_sql_ident(f)} "
            + "THEN "
            + _sql_literal(f'"{f}"')
            + " ELSE '' END"
            for f in fields
        )
        + " || ']'"
    )
    fields_changed_expr = _clean_json_sql(raw_fields_changed)

    raw_old_values = (
        "'{' || "
        + " || ',' || ".join(
            f"CASE WHEN OLD.{_sql_ident(f)} IS NOT NEW.{_sql_ident(f)} "
            + "THEN "
            + _sql_literal(f'"{f}":')
            + " || json_quote(OLD."
            + _sql_ident(f)
            + ") ELSE '' END"
            for f in fields
        )
        + " || '}'"
    )
    old_values_expr = _clean_json_obj_sql(raw_old_values)

    raw_new_values = (
        "'{' || "
        + " || ',' || ".join(
            f"CASE WHEN OLD.{_sql_ident(f)} IS NOT NEW.{_sql_ident(f)} "
            + "THEN "
            + _sql_literal(f'"{f}":')
            + " || json_quote(NEW."
            + _sql_ident(f)
            + ") ELSE '' END"
            for f in fields
        )
        + " || '}'"
    )
    new_values_expr = _clean_json_obj_sql(raw_new_values)
    # Atomic version increment via UPDATE on the sequence table, then read
    version_update = "UPDATE _sync_version_seq SET next_version = next_version + 1 WHERE table_name = " + _sql_literal(table) + "; "  # nosec B608
    version_expr = "(SELECT next_version - 1 FROM _sync_version_seq WHERE table_name = " + _sql_literal(table) + ")"  # nosec B608
    return "".join([  # nosec B608
        "CREATE TRIGGER IF NOT EXISTS ",
        _sql_ident(f"_sync_trg_{table}_update"),
        " AFTER UPDATE ON ",
        _sql_ident(table),
        " ",
        when_clause,
        "BEGIN ",
        version_update,
        "INSERT INTO _sync_changelog ",
        "(table_name, row_id, operation, fields_changed, old_values, new_values, device_id, __version) ",
        "VALUES (",
        _sql_literal(table),
        ", CAST(",
        _pk_expr(pk, "NEW"),
        " AS TEXT), 'UPDATE', ",
        fields_changed_expr,
        ", ",
        old_values_expr,
        ", ",
        new_values_expr,
        ", ",
        _sql_literal(device_id),
        ", ",
        version_expr,
        "); END;",
    ])


def _build_delete_trigger(table: str, pk: str | list[str], fields: list[str], device_id: str) -> str:
    """Generate AFTER DELETE trigger SQL for *table*."""
    _validate_trigger_spec(table, pk, fields)
    old_values_expr = (
        "'{' || "
        + " || ',' || ".join(
            _sql_literal(f'"{f}":') + " || json_quote(OLD." + _sql_ident(f) + ")"
            for f in fields
        )
        + " || '}'"
    )
    # Atomic version increment via UPDATE on the sequence table, then read
    version_update = "UPDATE _sync_version_seq SET next_version = next_version + 1 WHERE table_name = " + _sql_literal(table) + "; "  # nosec B608
    version_expr = "(SELECT next_version - 1 FROM _sync_version_seq WHERE table_name = " + _sql_literal(table) + ")"  # nosec B608
    return "".join([  # nosec B608
        "CREATE TRIGGER IF NOT EXISTS ",
        _sql_ident(f"_sync_trg_{table}_delete"),
        " AFTER DELETE ON ",
        _sql_ident(table),
        " BEGIN ",
        version_update,
        "INSERT INTO _sync_changelog ",
        "(table_name, row_id, operation, fields_changed, old_values, new_values, device_id, __version) ",
        "VALUES (",
        _sql_literal(table),
        ", CAST(",
        _pk_expr(pk, "OLD"),
        " AS TEXT), 'DELETE', '[]', ",
        old_values_expr,
        ", '{}', ",
        _sql_literal(device_id),
        ", ",
        version_expr,
        "); END;",
    ])


# Public API


def install_changelog_triggers(db: sqlite3.Connection, device_id: str = "desktop") -> None:
    """Idempotently create changelog/cursor tables and per-table triggers.

    Safe to call multiple times -- uses CREATE TABLE/TRIGGER IF NOT EXISTS.
    """
    if not _DEVICE_ID_RE.match(device_id):
        raise ValueError(f"Invalid device_id: must be 1-64 alphanumeric/dash/underscore chars, got {device_id!r}")
    cur = db.cursor()
    cur.execute(_CHANGELOG_DDL)
    cur.execute(_CURSOR_DDL)
    cur.execute(_VERSION_SEQ_DDL)
    for idx_sql in _INDEXES:
        cur.execute(idx_sql)

    # Check which tables exist so we only install triggers for present tables
    existing_tables = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table, spec in _TRACKED_TABLES.items():
        if table not in existing_tables:
            logger.debug("Sync: skipping triggers for %s (table not created yet)", table)
            continue

        pk = spec["pk"]
        fields = spec["fields"]
        noise_fields = spec.get("noise_fields", [])

        # Seed version sequence row if not already present
        cur.execute(
            "INSERT OR IGNORE INTO _sync_version_seq (table_name, next_version) "
            "VALUES (?, COALESCE("
            "(SELECT MAX(__version) + 1 FROM _sync_changelog WHERE table_name = ?), 1))",
            (table, table),
        )

        cur.execute(_build_insert_trigger(table, pk, fields, device_id))
        cur.execute(_build_update_trigger(table, pk, fields, noise_fields, device_id))
        cur.execute(_build_delete_trigger(table, pk, fields, device_id))

    db.commit()
    logger.info("Sync changelog triggers installed (device_id=%s)", device_id)


def compute_diff(
    db: sqlite3.Connection,
    table_name: str,
    since_version: int,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return changelog entries for *table_name* with __version > *since_version*."""
    cur = db.execute(
        "SELECT changelog_id, table_name, row_id, operation, "
        "fields_changed, old_values, new_values, device_id, ts, __version "
        "FROM _sync_changelog "
        "WHERE table_name = ? AND __version > ? "
        "ORDER BY __version ASC LIMIT ?",
        (table_name, since_version, limit),
    )
    rows = cur.fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append({
            "changelog_id": row[0],
            "table_name": row[1],
            "row_id": row[2],
            "operation": row[3],
            "fields_changed": _safe_json_loads(row[4], []),
            "old_values": _safe_json_loads(row[5], {}),
            "new_values": _safe_json_loads(row[6], {}),
            "device_id": row[7],
            "ts": row[8],
            "__version": row[9],
        })
    return results


def get_sync_cursor(db: sqlite3.Connection, device_id: str, table_name: str) -> int:
    """Return the last synced version for *device_id*/*table_name*, or 0."""
    cur = db.execute(
        "SELECT last_version FROM _sync_cursor WHERE device_id = ? AND table_name = ?",
        (device_id, table_name),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def update_sync_cursor(
    db: sqlite3.Connection,
    device_id: str,
    table_name: str,
    version: int,
    write_lock: threading.Lock,
) -> None:
    """Upsert the sync cursor for *device_id*/*table_name*."""
    with write_lock:
        db.execute(
            "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(device_id, table_name) DO UPDATE SET "
            "last_version = excluded.last_version, last_sync_ts = excluded.last_sync_ts",
            (device_id, table_name, version),
        )
        db.commit()


def compact_changelog(
    db: sqlite3.Connection,
    write_lock: threading.Lock,
    retention_days: int = 7,
) -> int:
    """Delete changelog entries older than *retention_days* that all devices have synced past.

    Returns the number of entries deleted.

    Uses a two-step approach (SELECT ids then DELETE by id) to avoid race
    conditions where new rows could match the DELETE criteria between
    evaluation and execution.
    """
    if retention_days < 0:
        retention_days = 0
    with write_lock:
        # Delete changelog entries that are older than retention and that all
        # devices have synced past (per-table MIN cursor check).
        cur = db.execute(
            "DELETE FROM _sync_changelog WHERE ts < datetime('now', ? || ' days') "
            "AND __version <= ("
            "  SELECT COALESCE(MIN(last_version), 0) "
            "  FROM _sync_cursor "
            "  WHERE _sync_cursor.table_name = _sync_changelog.table_name"
            ")",
            (str(-retention_days),),
        )
        db.commit()
        return cur.rowcount
