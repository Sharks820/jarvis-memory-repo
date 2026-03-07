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

    def apply_incoming(self, changes: dict[str, Any], source_device_id: str) -> IncomingSyncResult:
        """Apply remote changes within a single transaction.

        Only successfully applied operations are counted.  Cursors are only
        advanced to the max ``__version`` among *successfully* applied ops
        per table, so unrecognized operations are never skipped permanently.

        Returns ``{"applied": N, "conflicts_resolved": N, "errors": []}``.
        """
        applied = 0
        conflicts_resolved = 0
        errors: list[str] = []

        incoming_changes = changes.get("changes", changes)
        incoming_cursors = changes.get("cursors", {})

        desktop_is_local = self._device_id == "desktop"

        # Track the max successfully applied __version per table so cursors
        # are only advanced for ops that actually succeeded.
        success_max_version: dict[str, int] = {}

        with self._write_lock:
            try:
                for table_name, entries in incoming_changes.items():
                    if table_name not in _TRACKED_TABLES:
                        errors.append(f"Unknown table: {table_name}")
                        continue

                    spec = _TRACKED_TABLES[table_name]
                    pk = spec["pk"]

                    # Fetch cursor once per table (same for all entries)
                    source_cursor = get_sync_cursor(
                        self._db, source_device_id, table_name,
                    )

                    for entry in entries:
                        row_id = str(entry.get("row_id") or "")
                        if not row_id:
                            errors.append(f"Missing row_id in {table_name} entry")
                            continue
                        if len(row_id) > 256:
                            errors.append(f"Invalid row_id in {table_name} entry")
                            continue

                        # Check for local conflict: same row modified locally since
                        # last sync from source_device_id
                        local_conflict = self._find_local_conflict(
                            table_name, row_id, source_cursor,
                        )

                        if local_conflict:
                            resolved = self._resolve_conflict(
                                local_conflict, entry, desktop_is_local,
                            )
                            ok = self._apply_single_change(table_name, pk, resolved)
                            if ok:
                                conflicts_resolved += 1
                        else:
                            ok = self._apply_single_change(table_name, pk, entry)

                        if ok:
                            applied += 1
                            # Track max version of successfully applied ops
                            entry_version = entry.get("__version")
                            if entry_version is not None:
                                cur_max = success_max_version.get(table_name)
                                if cur_max is None or entry_version > cur_max:
                                    success_max_version[table_name] = entry_version
                        else:
                            op = entry.get("operation", "<missing>")
                            errors.append(
                                f"Skipped unknown operation {op!r} for "
                                f"{table_name} row {row_id}"
                            )

                # Update cursors for source device — only advance to the max
                # version of successfully applied ops.  If an incoming cursor
                # is provided but no ops for that table succeeded, fall back
                # to the incoming cursor value (all ops were valid table-level
                # entries that passed the _TRACKED_TABLES check).
                for table_name, version in incoming_cursors.items():
                    if table_name not in _TRACKED_TABLES:
                        continue
                    # Use success_max_version if we have it, otherwise use the
                    # incoming cursor only when ALL ops for that table succeeded
                    # (i.e. the table had no entries or had no failures).
                    effective_version = success_max_version.get(table_name)
                    if effective_version is None:
                        # No ops applied for this table — check if the table
                        # had entries at all; if not, use the incoming cursor.
                        if table_name not in incoming_changes:
                            effective_version = version
                        else:
                            # Table had entries but none succeeded — do NOT
                            # advance the cursor.
                            continue
                    self._db.execute(
                        "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
                        "VALUES (?, ?, ?, datetime('now')) "
                        "ON CONFLICT(device_id, table_name) DO UPDATE SET "
                        "last_version = excluded.last_version, last_sync_ts = excluded.last_sync_ts",
                        (source_device_id, table_name, effective_version),
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

        # Defense-in-depth: table_name must be in _TRACKED_TABLES (validated
        # by the caller) and must be a simple identifier — never user input.
        if table_name not in _TRACKED_TABLES:
            raise ValueError(f"Unknown table: {table_name}")
        if not table_name.isidentifier():
            raise ValueError(f"Invalid table name: {table_name}")

        # Normalize pk to a list for uniform handling below.
        pk_cols: list[str] = pk if isinstance(pk, list) else [pk]
        is_composite = len(pk_cols) > 1

        # Split row_id into parts for composite PKs ("tone:casual" → ["tone", "casual"]).
        if is_composite:
            pk_values = row_id.split(":", len(pk_cols) - 1)
            if len(pk_values) != len(pk_cols):
                logger.warning(
                    "Composite row_id %r has %d parts but pk has %d columns — skipping",
                    row_id, len(pk_values), len(pk_cols),
                )
                return False
        else:
            pk_values = [row_id]

        # WHERE clause for UPDATE/DELETE: "col1 = ? AND col2 = ?" for composite,
        # "pk = ?" for simple.
        where_clause = " AND ".join(col + " = ?" for col in pk_cols)

        # Validate all field/column names against the known schema to prevent
        # SQL injection via crafted sync payloads.
        allowed_fields = set(_TRACKED_TABLES[table_name]["fields"])
        allowed_fields.update(pk_cols)

        if operation == "INSERT":
            if not new_values:
                return True
            # Filter to only allowed columns
            safe_values = {k: v for k, v in new_values.items() if k in allowed_fields}
            if not safe_values:
                return True
            # Build column list: PK columns first (using split row_id values),
            # then remaining value columns.
            pk_set = set(pk_cols)
            extra_cols = [k for k in safe_values if k not in pk_set]
            cols = pk_cols + extra_cols
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = pk_values + [safe_values[k] for k in extra_cols]
            self._db.execute(
                "INSERT OR IGNORE INTO " + table_name
                + " (" + col_names + ") VALUES (" + placeholders + ")",
                values,
            )

        elif operation == "UPDATE":
            if not fields_changed or not new_values:
                return True
            set_parts: list[str] = []
            values: list[Any] = []
            for field in fields_changed:
                if field not in allowed_fields:
                    continue  # Skip unknown fields
                if field in new_values:
                    set_parts.append(field + " = ?")
                    values.append(new_values[field])
            if not set_parts:
                return True
            values.extend(pk_values)
            self._db.execute(
                "UPDATE " + table_name + " SET "
                + ", ".join(set_parts)
                + " WHERE " + where_clause,
                values,
            )

        elif operation == "DELETE":
            self._db.execute(
                "DELETE FROM " + table_name + " WHERE " + where_clause,
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
