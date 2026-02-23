"""Contradiction management for the knowledge graph.

Lists pending contradictions and resolves them via owner decision:
- accept_new: replace locked value with incoming, unlock node
- keep_old: discard incoming value, keep existing
- merge: set a user-supplied merged value on the node
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class ContradictionManager:
    """Manages contradiction quarantine entries for owner review."""

    def __init__(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        self._db = db
        self._write_lock = write_lock

    # ------------------------------------------------------------------
    # List operations
    # ------------------------------------------------------------------

    def list_pending(self, limit: int = 20) -> list[dict]:
        """List pending contradictions, most recent first."""
        cur = self._db.execute(
            """SELECT * FROM kg_contradictions
               WHERE status = 'pending'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def list_all(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """List contradictions with optional status filter."""
        if status:
            cur = self._db.execute(
                """SELECT * FROM kg_contradictions
                   WHERE status = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (status, limit),
            )
        else:
            cur = self._db.execute(
                """SELECT * FROM kg_contradictions
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            )
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        contradiction_id: int,
        resolution: str,
        merge_value: str = "",
    ) -> dict:
        """Resolve a contradiction by owner decision.

        Args:
            contradiction_id: The ID of the contradiction to resolve.
            resolution: One of 'accept_new', 'keep_old', 'merge'.
            merge_value: Required when resolution is 'merge'.

        Returns:
            Dict with success, node_id, resolution, message.
        """
        if resolution not in ("accept_new", "keep_old", "merge"):
            return {
                "success": False,
                "node_id": "",
                "resolution": resolution,
                "message": f"Invalid resolution: {resolution}. Must be accept_new, keep_old, or merge.",
            }

        if resolution == "merge" and not merge_value.strip():
            return {
                "success": False,
                "node_id": "",
                "resolution": resolution,
                "message": "merge_value is required when resolution is 'merge'.",
            }

        # Load the contradiction
        row = self._db.execute(
            "SELECT * FROM kg_contradictions WHERE contradiction_id = ?",
            (contradiction_id,),
        ).fetchone()

        if row is None:
            return {
                "success": False,
                "node_id": "",
                "resolution": resolution,
                "message": f"Contradiction {contradiction_id} not found.",
            }

        contradiction = dict(row)
        if contradiction["status"] != "pending":
            return {
                "success": False,
                "node_id": contradiction["node_id"],
                "resolution": resolution,
                "message": f"Contradiction {contradiction_id} is already resolved.",
            }

        node_id = contradiction["node_id"]
        existing_value = contradiction["existing_value"]
        incoming_value = contradiction["incoming_value"]
        now = datetime.now(UTC).isoformat()

        with self._write_lock:
            # Load current node for history
            node_row = self._db.execute(
                "SELECT label, history FROM kg_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()

            current_label = node_row["label"] if node_row else existing_value
            history = []
            if node_row:
                try:
                    history = json.loads(node_row["history"])
                except (json.JSONDecodeError, TypeError):
                    history = []

            if resolution == "accept_new":
                # Replace value, unlock node (needs re-confirmation to lock again)
                self._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, locked = 0, locked_at = NULL, locked_by = NULL,
                           updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (incoming_value, node_id),
                )
                history.append({
                    "action": "accept_new",
                    "previous_value": current_label,
                    "new_value": incoming_value,
                    "resolved_at": now,
                })

            elif resolution == "keep_old":
                # No node change
                history.append({
                    "action": "keep_old",
                    "previous_value": current_label,
                    "new_value": incoming_value,
                    "resolved_at": now,
                })

            elif resolution == "merge":
                # Set merge_value on the node
                self._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (merge_value, node_id),
                )
                history.append({
                    "action": "merge",
                    "previous_value": current_label,
                    "new_value": merge_value,
                    "resolved_at": now,
                })

            # Update history on the node
            self._db.execute(
                "UPDATE kg_nodes SET history = ? WHERE node_id = ?",
                (json.dumps(history[-50:]), node_id),
            )

            # Mark contradiction as resolved
            self._db.execute(
                """UPDATE kg_contradictions
                   SET status = 'resolved', resolution = ?, resolved_at = datetime('now')
                   WHERE contradiction_id = ?""",
                (resolution, contradiction_id),
            )
            self._db.commit()

        logger.info(
            "Contradiction %d resolved: %s for node %s",
            contradiction_id,
            resolution,
            node_id,
        )

        return {
            "success": True,
            "node_id": node_id,
            "resolution": resolution,
            "message": f"Contradiction {contradiction_id} resolved via {resolution}.",
        }
