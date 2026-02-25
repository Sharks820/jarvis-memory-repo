"""Knowledge graph regression checker.

Captures graph metrics (node count, edge count, locked count, WL hash)
and compares snapshots to detect regressions (lost nodes, lost edges,
lost locked facts, unexpected hash changes).  Supports graph backup/restore
and node-level diff between snapshots.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from jarvis_engine._compat import UTC
from typing import TYPE_CHECKING

from jarvis_engine._shared import safe_int as _safe_int

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)

_MAX_BACKUPS = 10

# Consistent hash for empty graphs (SHA-256 of "empty_knowledge_graph")
_EMPTY_GRAPH_HASH = hashlib.sha256(b"empty_knowledge_graph").hexdigest()[:32]


class RegressionChecker:
    """Captures and compares knowledge graph metrics between snapshots."""

    def __init__(self, kg: "KnowledgeGraph") -> None:
        self._kg = kg

    def capture_metrics(self) -> dict:
        """Build a metrics snapshot from the current knowledge graph state.

        Returns dict with: node_count, edge_count, locked_count, graph_hash,
        captured_at, and node_labels (dict mapping node_id -> label).
        """
        G = self._kg.to_networkx()

        node_count = G.number_of_nodes()
        edge_count = G.number_of_edges()
        locked_count = self._kg.count_locked()

        # Capture node_id -> label mapping for diff support
        node_labels: dict[str, str] = {}
        for nid, attrs in G.nodes(data=True):
            node_labels[str(nid)] = str(attrs.get("label", ""))

        if node_count == 0:
            graph_hash = _EMPTY_GRAPH_HASH
        else:
            try:
                import networkx as nx

                graph_hash = nx.weisfeiler_lehman_graph_hash(
                    G,
                    node_attr="label",
                    edge_attr="relation",
                    iterations=3,
                    digest_size=16,
                )
            except Exception as exc:
                logger.warning("WL hash computation failed: %s", exc)
                graph_hash = _EMPTY_GRAPH_HASH

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "locked_count": locked_count,
            "graph_hash": graph_hash,
            "node_labels": node_labels,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def backup_graph(self, tag: str = "") -> Path:
        """Copy the SQLite DB to a timestamped backup file.

        Backups are stored under ``.planning/runtime/kg_backups/``.
        Keeps at most ``_MAX_BACKUPS`` files, auto-pruning the oldest.

        Returns the Path of the newly created backup file.
        """
        backup_dir = Path(".planning/runtime/kg_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        suffix = f"_{tag}" if tag else ""
        backup_name = f"{ts}{suffix}.db"
        backup_path = backup_dir / backup_name

        # Use the DB path from the underlying engine
        src_path = Path(self._kg._engine._db_path)
        shutil.copy2(str(src_path), str(backup_path))
        logger.info("Knowledge graph backed up to %s", backup_path)

        # Auto-prune oldest backups beyond _MAX_BACKUPS
        existing = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime)
        while len(existing) > _MAX_BACKUPS:
            oldest = existing.pop(0)
            try:
                oldest.unlink()
                logger.debug("Pruned old backup: %s", oldest)
            except OSError as exc:
                logger.warning("Failed to prune backup %s: %s", oldest, exc)

        return backup_path

    def restore_graph(self, backup_path: Path) -> bool:
        """Restore the knowledge graph from a backup file.

        Copies the backup over the live DB file, then reinitializes
        the KG schema (which re-opens tables via ``_ensure_schema``).

        Returns True on success, False on failure.
        """
        if not backup_path.exists():
            logger.error("Backup file does not exist: %s", backup_path)
            return False

        dst_path = Path(self._kg._engine._db_path)
        try:
            shutil.copy2(str(backup_path), str(dst_path))
            # Reinitialize schema on the live connection
            self._kg._ensure_schema()
            logger.info("Knowledge graph restored from %s", backup_path)
            return True
        except Exception as exc:
            logger.error("Failed to restore graph from %s: %s", backup_path, exc)
            return False

    # ------------------------------------------------------------------
    # Node-level diff
    # ------------------------------------------------------------------

    def node_diff(self, snapshot_before: dict, snapshot_after: dict) -> dict:
        """Compute node-level changes between two metric snapshots.

        Compares the ``node_labels`` dicts present in each snapshot.

        Returns:
            Dict with ``added`` (list of "node_id:label" strings),
            ``removed`` (list), and ``modified`` (list -- label changed
            for the same node_id).
        """
        before_labels: dict[str, str] = snapshot_before.get("node_labels", {})
        after_labels: dict[str, str] = snapshot_after.get("node_labels", {})

        before_ids = set(before_labels.keys())
        after_ids = set(after_labels.keys())

        added = sorted(
            f"{nid}:{after_labels[nid]}" for nid in (after_ids - before_ids)
        )
        removed = sorted(
            f"{nid}:{before_labels[nid]}" for nid in (before_ids - after_ids)
        )
        modified = sorted(
            f"{nid}:{before_labels[nid]}->{after_labels[nid]}"
            for nid in (before_ids & after_ids)
            if before_labels[nid] != after_labels[nid]
        )

        return {"added": added, "removed": removed, "modified": modified}

    def compare(self, previous: dict | None, current: dict) -> dict:
        """Compare two metric snapshots and report discrepancies.

        Args:
            previous: Previous metrics snapshot (or None for baseline).
            current: Current metrics snapshot.

        Returns:
            Dict with status ('pass', 'warn', 'fail', 'baseline'),
            discrepancies list, and both metric snapshots.
        """
        if previous is None:
            return {
                "status": "baseline",
                "message": "Baseline established, no comparison available.",
                "discrepancies": [],
                "current": current,
                "previous": None,
            }

        discrepancies = []

        prev_nodes = _safe_int(previous.get("node_count", 0))
        curr_nodes = _safe_int(current.get("node_count", 0))
        if curr_nodes < prev_nodes:
            discrepancies.append({
                "type": "node_loss",
                "severity": "fail",
                "previous": prev_nodes,
                "current": curr_nodes,
                "lost": prev_nodes - curr_nodes,
                "message": f"Node count decreased from {prev_nodes} to {curr_nodes} (lost {prev_nodes - curr_nodes})",
            })

        prev_edges = _safe_int(previous.get("edge_count", 0))
        curr_edges = _safe_int(current.get("edge_count", 0))
        if curr_edges < prev_edges:
            discrepancies.append({
                "type": "edge_loss",
                "severity": "fail",
                "previous": prev_edges,
                "current": curr_edges,
                "lost": prev_edges - curr_edges,
                "message": f"Edge count decreased from {prev_edges} to {curr_edges} (lost {prev_edges - curr_edges})",
            })

        prev_locked = _safe_int(previous.get("locked_count", 0))
        curr_locked = _safe_int(current.get("locked_count", 0))
        if curr_locked < prev_locked:
            discrepancies.append({
                "type": "locked_fact_loss",
                "severity": "critical",
                "previous": prev_locked,
                "current": curr_locked,
                "lost": prev_locked - curr_locked,
                "message": f"Locked fact count decreased from {prev_locked} to {curr_locked} (CRITICAL: lost {prev_locked - curr_locked} locked facts)",
            })

        prev_hash = previous.get("graph_hash", "")
        curr_hash = current.get("graph_hash", "")
        if prev_hash and curr_hash and prev_hash != curr_hash:
            # Hash changed -- check if counts also increased (expected growth)
            if curr_nodes <= prev_nodes and curr_edges <= prev_edges:
                discrepancies.append({
                    "type": "graph_hash_change",
                    "severity": "warn",
                    "previous_hash": prev_hash,
                    "current_hash": curr_hash,
                    "message": "Graph hash changed without count increase -- possible modification of existing data",
                })

        # Determine overall status
        if not discrepancies:
            status = "pass"
        elif any(d["severity"] == "critical" for d in discrepancies):
            status = "fail"
        elif any(d["severity"] == "fail" for d in discrepancies):
            status = "fail"
        else:
            status = "warn"

        result: dict = {
            "status": status,
            "discrepancies": discrepancies,
            "previous": previous,
            "current": current,
        }

        # Include node-level diff when regression is detected
        if status in ("fail", "warn"):
            result["node_diff"] = self.node_diff(previous, current)

        return result
