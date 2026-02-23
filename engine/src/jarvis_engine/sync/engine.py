"""Sync engine: diff computation, incoming change application, conflict resolution.

Uses the changelog tables to compute outgoing diffs and apply incoming changes
with field-level conflict resolution (desktop wins ties, DELETE wins over UPDATE).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any

from jarvis_engine.sync.changelog import (
    _TRACKED_TABLES,
    compute_diff,
    get_sync_cursor,
    update_sync_cursor,
)

logger = logging.getLogger(__name__)


class SyncEngine:
    """Bidirectional sync engine with field-level conflict resolution."""

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        device_id: str = "desktop",
    ) -> None:
        self._db = db
        self._write_lock = write_lock
        self._device_id = device_id

    def compute_outgoing(self, target_device_id: str, limit: int = 500) -> dict[str, Any]:
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

    def apply_incoming(self, changes: dict[str, Any], source_device_id: str) -> dict[str, Any]:
        """Apply remote changes within a single transaction.

        Returns ``{"applied": N, "conflicts_resolved": N, "errors": []}``.
        """
        applied = 0
        conflicts_resolved = 0
        errors: list[str] = []

        incoming_changes = changes.get("changes", changes)
        incoming_cursors = changes.get("cursors", {})

        desktop_is_local = self._device_id == "desktop"

        with self._write_lock:
            try:
                for table_name, entries in incoming_changes.items():
                    if table_name not in _TRACKED_TABLES:
                        errors.append(f"Unknown table: {table_name}")
                        continue

                    spec = _TRACKED_TABLES[table_name]
                    pk = spec["pk"]

                    for entry in entries:
                        row_id = str(entry.get("row_id", ""))
                        if not row_id:
                            errors.append(f"Missing row_id in {table_name} entry")
                            continue

                        # Check for local conflict: same row modified locally since
                        # last sync from source_device_id
                        source_cursor = get_sync_cursor(
                            self._db, source_device_id, table_name,
                        )
                        local_conflict = self._find_local_conflict(
                            table_name, row_id, source_cursor,
                        )

                        if local_conflict:
                            resolved = self._resolve_conflict(
                                local_conflict, entry, desktop_is_local,
                            )
                            self._apply_single_change(table_name, pk, resolved)
                            conflicts_resolved += 1
                        else:
                            self._apply_single_change(table_name, pk, entry)

                        applied += 1

                # Update cursors for source device
                for table_name, version in incoming_cursors.items():
                    if table_name in _TRACKED_TABLES:
                        # Use a no-op lock since we already hold _write_lock
                        self._db.execute(
                            "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
                            "VALUES (?, ?, ?, datetime('now')) "
                            "ON CONFLICT(device_id, table_name) DO UPDATE SET "
                            "last_version = excluded.last_version, last_sync_ts = excluded.last_sync_ts",
                            (source_device_id, table_name, version),
                        )

                self._db.commit()
            except Exception as exc:
                self._db.rollback()
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
            "fields_changed": json.loads(row[4]) if row[4] else [],
            "old_values": json.loads(row[5]) if row[5] else {},
            "new_values": json.loads(row[6]) if row[6] else {},
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
        """Field-level merge. DELETE always wins. Desktop wins ties."""
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
                # Both changed the same field -- desktop wins ties
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

    def _apply_single_change(
        self, table_name: str, pk: str, entry: dict[str, Any],
    ) -> None:
        """Execute INSERT/UPDATE/DELETE based on *entry* operation."""
        operation = entry.get("operation", "").upper()
        row_id = entry.get("row_id", "")
        new_values = entry.get("new_values", {})
        fields_changed = [f for f in entry.get("fields_changed", []) if f]

        if operation == "INSERT":
            if not new_values:
                return
            cols = [pk] + list(new_values.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = [row_id] + list(new_values.values())
            self._db.execute(
                "INSERT OR IGNORE INTO " + table_name
                + " (" + col_names + ") VALUES (" + placeholders + ")",
                values,
            )

        elif operation == "UPDATE":
            if not fields_changed or not new_values:
                return
            set_parts = []
            values = []
            for field in fields_changed:
                if field in new_values:
                    set_parts.append(field + " = ?")
                    values.append(new_values[field])
            if not set_parts:
                return
            values.append(row_id)
            self._db.execute(
                "UPDATE " + table_name + " SET "
                + ", ".join(set_parts)
                + " WHERE " + pk + " = ?",
                values,
            )

        elif operation == "DELETE":
            self._db.execute(
                "DELETE FROM " + table_name + " WHERE " + pk + " = ?",
                (row_id,),
            )

    def sync_status(self) -> dict[str, Any]:
        """Return cursors for all devices/tables and total changelog size."""
        cur = self._db.execute(
            "SELECT device_id, table_name, last_version, last_sync_ts "
            "FROM _sync_cursor ORDER BY device_id, table_name",
        )
        cursors: list[dict[str, Any]] = []
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
