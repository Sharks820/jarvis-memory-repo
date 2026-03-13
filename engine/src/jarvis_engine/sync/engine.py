"""Sync engine: diff computation, incoming change application, conflict resolution.

Uses the changelog tables to compute outgoing diffs and apply incoming changes
with field-level conflict resolution (desktop wins ties, DELETE wins over UPDATE).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any, TypedDict

from jarvis_engine.sync.changelog import (
    _TRACKED_TABLES,
    _sql_ident,
    _safe_json_loads,
    compute_diff,
    get_sync_cursor,
)

logger = logging.getLogger(__name__)


class OutgoingSyncPayload(TypedDict):
    """Outgoing diff payload for a target device."""

    changes: dict[str, list[dict[str, Any]]]
    cursors: dict[str, int]


class IncomingSyncResult(TypedDict):
    """Result of applying incoming sync changes."""

    applied: int
    conflicts_resolved: int
    errors: list[str]


class SyncCursorEntry(TypedDict):
    """A single sync cursor row."""

    device_id: str
    table_name: str
    last_version: int
    last_sync_ts: str


class SyncStatus(TypedDict):
    """Current sync status with cursors and changelog size."""

    cursors: list[SyncCursorEntry]
    changelog_size: int


class SyncEngine:
    """Bidirectional sync engine with field-level conflict resolution.

    Supports two conflict strategies:
    - ``"most_recent"``: timestamp-based, fairest — whichever change is newer wins.
      This respects phone data (context, location, interactions) equally.
    - ``"desktop_wins"``: legacy behavior — desktop always wins ties.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        device_id: str = "desktop",
        conflict_strategy: str = "most_recent",
    ) -> None:
        self._db = db
        self._write_lock = write_lock
        self._device_id = device_id
        self._conflict_strategy = conflict_strategy

    def compute_outgoing(self, target_device_id: str, limit: int = 500) -> OutgoingSyncPayload:
        """Compute changes to send to *target_device_id*.

        Returns ``{"changes": {table: [entries]}, "cursors": {table: max_version}}``.
        """
        changes: dict[str, list[dict[str, Any]]] = {}
        cursors: dict[str, int] = {}

        for table in _TRACKED_TABLES:
            since = get_sync_cursor(self._db, target_device_id, table)
            entries = compute_diff(self._db, table, since, limit=limit)
            if entries:
                changes[table] = entries
                cursors[table] = max(e["__version"] for e in entries)
            else:
                cursors[table] = since

        return {"changes": changes, "cursors": cursors}

    def _apply_table_entries(
        self,
        table_name: str,
        entries: list[dict[str, Any]],
        source_device_id: str,
        desktop_is_local: bool,
        errors: list[str],
        success_max_version: dict[str, int],
    ) -> tuple[int, int]:
        """Apply entries for one table.  Returns (applied, conflicts_resolved)."""
        spec = _TRACKED_TABLES[table_name]
        pk = spec["pk"]
        source_cursor = get_sync_cursor(self._db, source_device_id, table_name)

        applied = 0
        conflicts_resolved = 0
        for entry in entries:
            row_id = str(entry.get("row_id") or "")
            if not row_id:
                errors.append(f"Missing row_id in {table_name} entry")
                continue
            if len(row_id) > 256:
                errors.append(f"Invalid row_id in {table_name} entry")
                continue

            local_conflict = self._find_local_conflict(table_name, row_id, source_cursor)
            if local_conflict:
                resolved = self._resolve_conflict(local_conflict, entry, desktop_is_local)
                ok = self._apply_single_change(table_name, pk, resolved)
                if ok:
                    conflicts_resolved += 1
            else:
                ok = self._apply_single_change(table_name, pk, entry)

            if ok:
                applied += 1
                entry_version = entry.get("__version")
                if entry_version is not None:
                    cur_max = success_max_version.get(table_name)
                    if cur_max is None or entry_version > cur_max:
                        success_max_version[table_name] = entry_version
            else:
                op = entry.get("operation", "<missing>")
                errors.append(f"Skipped unknown operation {op!r} for {table_name} row {row_id}")

        return applied, conflicts_resolved

    def _advance_sync_cursors(
        self,
        incoming_cursors: dict[str, int],
        incoming_changes: dict[str, Any],
        success_max_version: dict[str, int],
        source_device_id: str,
    ) -> None:
        """Advance sync cursors for the source device based on applied ops."""
        for table_name, version in incoming_cursors.items():
            if table_name not in _TRACKED_TABLES:
                continue
            effective_version = success_max_version.get(table_name)
            if effective_version is None:
                if table_name not in incoming_changes:
                    effective_version = version
                else:
                    continue
            self._db.execute(
                "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(device_id, table_name) DO UPDATE SET "
                "last_version = excluded.last_version, last_sync_ts = excluded.last_sync_ts",
                (source_device_id, table_name, effective_version),
            )

    def apply_incoming(self, changes: dict[str, Any], source_device_id: str) -> IncomingSyncResult:
        """Apply remote changes within a single transaction.

        Only successfully applied operations are counted.  Cursors are only
        advanced to the max ``__version`` among *successfully* applied ops
        per table, so unrecognized operations are never skipped permanently.
        """
        applied = 0
        conflicts_resolved = 0
        errors: list[str] = []
        incoming_changes = changes.get("changes", changes)
        incoming_cursors = changes.get("cursors", {})
        desktop_is_local = self._device_id == "desktop"
        success_max_version: dict[str, int] = {}

        with self._write_lock:
            try:
                for table_name, entries in incoming_changes.items():
                    if table_name not in _TRACKED_TABLES:
                        errors.append(f"Unknown table: {table_name}")
                        continue
                    tbl_applied, tbl_conflicts = self._apply_table_entries(
                        table_name, entries, source_device_id,
                        desktop_is_local, errors, success_max_version,
                    )
                    applied += tbl_applied
                    conflicts_resolved += tbl_conflicts

                self._advance_sync_cursors(
                    incoming_cursors, incoming_changes,
                    success_max_version, source_device_id,
                )
                self._db.commit()
            except sqlite3.Error as exc:
                self._db.rollback()
                applied = 0
                conflicts_resolved = 0
                errors.append(str(exc))
                logger.error("Sync apply_incoming failed: %s", exc)

        return {
            "applied": applied,
            "conflicts_resolved": conflicts_resolved,
            "errors": errors,
        }

    def _find_local_conflict(
        self, table_name: str, row_id: str, since_version: int,
    ) -> dict[str, Any] | None:
        """Check if *row_id* has been modified locally since *since_version*."""
        cur = self._db.execute(
            "SELECT changelog_id, table_name, row_id, operation, "
            "fields_changed, old_values, new_values, device_id, ts, __version "
            "FROM _sync_changelog "
            "WHERE table_name = ? AND row_id = ? AND __version > ? "
            "AND device_id = ? "
            "ORDER BY __version DESC LIMIT 1",
            (table_name, row_id, since_version, self._device_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
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
        }

    def _resolve_conflict(
        self,
        local_entry: dict[str, Any],
        remote_entry: dict[str, Any],
        desktop_is_local: bool,
    ) -> dict[str, Any]:
        """Field-level merge with configurable conflict strategy.

        DELETE always wins over UPDATE regardless of strategy.

        Strategies for same-field conflicts:
        - ``"most_recent"``: whichever entry has the newer timestamp wins.
          This treats phone and desktop data equally — if you made a change
          on your phone at 3pm and the desktop had an older change from 2pm,
          the phone's change wins. Fair and intuitive.
        - ``"desktop_wins"``: legacy behavior, desktop always wins ties.
        """
        # DELETE always wins over UPDATE
        if local_entry.get("operation") == "DELETE":
            return local_entry
        if remote_entry.get("operation") == "DELETE":
            return remote_entry

        # Both are UPDATEs (or INSERT): merge field-by-field
        local_new = local_entry.get("new_values", {})
        remote_new = remote_entry.get("new_values", {})

        local_fields = set(f for f in local_entry.get("fields_changed", []) if f)
        remote_fields = set(f for f in remote_entry.get("fields_changed", []) if f)

        merged_new = {}
        all_fields = local_fields | remote_fields

        for field in all_fields:
            if field in local_fields and field not in remote_fields:
                merged_new[field] = local_new.get(field)
            elif field in remote_fields and field not in local_fields:
                merged_new[field] = remote_new.get(field)
            else:
                # Both changed the same field — use configured strategy
                if self._conflict_strategy == "most_recent":
                    # Compare timestamps: newer wins
                    local_ts = local_entry.get("ts", "")
                    remote_ts = remote_entry.get("ts", "")
                    if remote_ts > local_ts:
                        merged_new[field] = remote_new.get(field)
                    elif local_ts > remote_ts:
                        merged_new[field] = local_new.get(field)
                    else:
                        # Exact same timestamp — desktop wins as tiebreaker
                        if desktop_is_local:
                            merged_new[field] = local_new.get(field)
                        else:
                            merged_new[field] = remote_new.get(field)
                else:
                    # Legacy: desktop wins ties
                    if desktop_is_local:
                        merged_new[field] = local_new.get(field)
                    else:
                        merged_new[field] = remote_new.get(field)

        return {
            "table_name": local_entry.get("table_name", remote_entry.get("table_name")),
            "row_id": local_entry.get("row_id", remote_entry.get("row_id")),
            "operation": "UPDATE",
            "fields_changed": [f for f in all_fields if f],
            "old_values": local_entry.get("old_values", {}),
            "new_values": merged_new,
        }

    # _apply_single_change helpers

    @staticmethod
    def _validate_and_parse_pk(
        table_name: str, pk: str | list[str], row_id: str,
    ) -> tuple[list[str], list[str], str, set[str]] | None:
        """Validate table/pk and return (pk_cols, pk_values, where_clause, allowed_fields).

        Returns None if the composite row_id has the wrong number of parts.
        """
        if table_name not in _TRACKED_TABLES:
            raise ValueError(f"Unknown table: {table_name}")
        _sql_ident(table_name)

        pk_cols: list[str] = pk if isinstance(pk, list) else [pk]
        for column in pk_cols:
            _sql_ident(column)
        is_composite = len(pk_cols) > 1

        if is_composite:
            pk_values = row_id.split(":", len(pk_cols) - 1)
            if len(pk_values) != len(pk_cols):
                logger.warning(
                    "Composite row_id %r has %d parts but pk has %d columns — skipping",
                    row_id, len(pk_values), len(pk_cols),
                )
                return None
        else:
            pk_values = [row_id]

        where_clause = " AND ".join(f"{_sql_ident(col)} = ?" for col in pk_cols)
        allowed_fields = set(_TRACKED_TABLES[table_name]["fields"])
        allowed_fields.update(pk_cols)
        return pk_cols, pk_values, where_clause, allowed_fields

    def _apply_insert(
        self, table_name: str, pk_cols: list[str], pk_values: list[str],
        new_values: dict[str, Any], allowed_fields: set[str],
    ) -> None:
        """Execute an INSERT OR IGNORE for a sync change."""
        if not new_values:
            return
        safe_values = {k: v for k, v in new_values.items() if k in allowed_fields}
        if not safe_values:
            return
        pk_set = set(pk_cols)
        extra_cols = [k for k in safe_values if k not in pk_set]
        for column in extra_cols:
            _sql_ident(column)
        cols = pk_cols + extra_cols
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(_sql_ident(col) for col in cols)
        values = pk_values + [safe_values[k] for k in extra_cols]
        self._db.execute(
            "INSERT OR IGNORE INTO " + _sql_ident(table_name)
            + " (" + col_names + ") VALUES (" + placeholders + ")",  # nosec B608
            values,
        )

    def _apply_update(
        self, table_name: str, pk_values: list[str], where_clause: str,
        fields_changed: list[str], new_values: dict[str, Any],
        allowed_fields: set[str],
    ) -> None:
        """Execute an UPDATE for a sync change."""
        if not fields_changed or not new_values:
            return
        set_parts: list[str] = []
        values: list[Any] = []
        for field in fields_changed:
            if field not in allowed_fields:
                continue
            if field in new_values:
                set_parts.append(_sql_ident(field) + " = ?")
                values.append(new_values[field])
        if not set_parts:
            return
        values.extend(pk_values)
        sql = "UPDATE " + _sql_ident(table_name) + " SET " + ", ".join(set_parts) + " WHERE " + where_clause  # nosec B608
        self._db.execute(sql, values)

    def _apply_single_change(
        self, table_name: str, pk: str | list[str], entry: dict[str, Any],
    ) -> bool:
        """Execute INSERT/UPDATE/DELETE based on *entry* operation.

        Returns True if the operation was recognized and applied, False if the
        operation type is unknown.

        *pk* may be a single column name (``"record_id"``) or a list of column
        names for composite keys (``["category", "preference"]``).  For
        composite keys the ``row_id`` is ``":"``-separated.
        """
        operation = entry.get("operation", "").upper()
        row_id = entry.get("row_id", "")
        new_values = entry.get("new_values", {})
        fields_changed = [f for f in entry.get("fields_changed", []) if f]

        parsed = self._validate_and_parse_pk(table_name, pk, row_id)
        if parsed is None:
            return False
        pk_cols, pk_values, where_clause, allowed_fields = parsed

        if operation == "INSERT":
            self._apply_insert(table_name, pk_cols, pk_values, new_values, allowed_fields)
        elif operation == "UPDATE":
            self._apply_update(
                table_name, pk_values, where_clause,
                fields_changed, new_values, allowed_fields,
            )
        elif operation == "DELETE":
            self._db.execute(
                "DELETE FROM " + _sql_ident(table_name) + " WHERE " + where_clause,  # nosec B608
                pk_values,
            )
        else:
            logger.warning(
                "Unknown sync operation %r for table %s row %s — skipping",
                operation, table_name, row_id,
            )
            return False

        return True

    def sync_status(self) -> SyncStatus:
        """Return cursors for all devices/tables and total changelog size."""
        cur = self._db.execute(
            "SELECT device_id, table_name, last_version, last_sync_ts "
            "FROM _sync_cursor ORDER BY device_id, table_name",
        )
        cursors: list[SyncCursorEntry] = []
        for row in cur.fetchall():
            cursors.append({
                "device_id": row[0],
                "table_name": row[1],
                "last_version": row[2],
                "last_sync_ts": row[3],
            })

        count_cur = self._db.execute("SELECT COUNT(*) FROM _sync_changelog")
        total = count_cur.fetchone()[0]

        return {
            "cursors": cursors,
            "changelog_size": total,
        }
