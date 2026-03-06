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
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine.knowledge._base import KGManagerBase

logger = logging.getLogger(__name__)


class ContradictionManager(KGManagerBase):
    """Manages contradiction quarantine entries for owner review."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_fts_index(self, node_id: str, label: str) -> None:
        """Update fts_kg_nodes for a node. Silently no-ops if table missing."""
        try:
            self._db.execute(
                "DELETE FROM fts_kg_nodes WHERE node_id = ?", (node_id,)
            )
            self._db.execute(
                "INSERT INTO fts_kg_nodes(node_id, label) VALUES (?, ?)",
                (node_id, label),
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                logger.debug("FTS5 table not available, skipping index update for node %s", node_id)
            else:
                raise

    def _update_vec_embedding(self, node_id: str, label: str) -> None:
        """Update vec_kg_nodes embedding for a node. Silently no-ops if unavailable."""
        if self._kg is None:
            return
        embed_service = getattr(self._kg, "_embed_service", None)
        vec_available = getattr(self._kg, "_vec_available", False)
        if embed_service is None or not vec_available:
            return
        try:
            import struct
            embedding = embed_service.embed(label, prefix="search_document")
            if len(embedding) == 768:
                blob = struct.pack(f"{len(embedding)}f", *embedding)
                self._db.execute(
                    "DELETE FROM vec_kg_nodes WHERE node_id = ?", (node_id,)
                )
                self._db.execute(
                    "INSERT INTO vec_kg_nodes(node_id, embedding) VALUES (?, ?)",
                    (node_id, blob),
                )
        except Exception as exc:
            logger.debug("Vec embedding update for node %s failed: %s", node_id, exc)

    # ------------------------------------------------------------------
    # List operations
    # ------------------------------------------------------------------

    def list_pending(self, limit: int = 20) -> list[dict]:
        """List pending contradictions, most recent first."""
        with self._db_lock:
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
        with self._db_lock:
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

        with self._write_lock:
            # Load the contradiction inside write lock to prevent TOCTOU race
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
            contradiction["status"] = str(contradiction.get("status", "")).strip()
            contradiction["node_id"] = str(contradiction.get("node_id", ""))
            contradiction["existing_value"] = str(contradiction.get("existing_value", ""))
            contradiction["incoming_value"] = str(contradiction.get("incoming_value", ""))
            contradiction["incoming_confidence"] = float(
                contradiction.get("incoming_confidence", 0.0) or 0.0,
            )
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
            incoming_confidence = contradiction["incoming_confidence"]
            now = datetime.now(UTC).isoformat()
            # Load current node for history
            node_row = self._db.execute(
                "SELECT label, history FROM kg_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()

            if node_row is None and resolution != "keep_old":
                return {
                    "success": False,
                    "node_id": node_id,
                    "resolution": resolution,
                    "message": f"Node {node_id} no longer exists; cannot apply {resolution}.",
                }

            current_label = node_row["label"] if node_row else existing_value
            history = []
            if node_row:
                try:
                    history = json.loads(node_row["history"])
                except (json.JSONDecodeError, TypeError):
                    history = []

            if resolution == "accept_new":
                # Replace value, unlock node, reset confidence to incoming
                self._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, locked = 0, locked_at = NULL, locked_by = NULL,
                           confidence = ?, updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (incoming_value, incoming_confidence, node_id),
                )
                # Update FTS5 + vec indexes (defensive — no-ops if tables missing)
                self._update_fts_index(node_id, incoming_value)
                self._update_vec_embedding(node_id, incoming_value)
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
                # Set merge_value on the node, unlock it (same as accept_new)
                self._db.execute(
                    """UPDATE kg_nodes
                       SET label = ?, locked = 0, locked_at = NULL, locked_by = NULL,
                           updated_at = datetime('now')
                       WHERE node_id = ?""",
                    (merge_value, node_id),
                )
                # Update FTS5 + vec indexes (defensive — no-ops if tables missing)
                self._update_fts_index(node_id, merge_value)
                self._update_vec_embedding(node_id, merge_value)
                history.append({
                    "action": "merge",
                    "previous_value": current_label,
                    "new_value": merge_value,
                    "resolved_at": now,
                })

            # Update history on the node (guard against missing node)
            if node_row is not None:
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

            # Invalidate NetworkX cache if kg_nodes was modified
            if resolution in ("accept_new", "merge") and self._kg is not None:
                counter = getattr(self._kg, "_mutation_counter", 0)
                if isinstance(counter, int):
                    setattr(self._kg, "_mutation_counter", counter + 1)

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
