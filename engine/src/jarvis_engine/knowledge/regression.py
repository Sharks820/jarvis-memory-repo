"""Knowledge graph regression checker.

Captures graph metrics (node count, edge count, locked count, WL hash)
and compares snapshots to detect regressions (lost nodes, lost edges,
lost locked facts, unexpected hash changes).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from jarvis_engine._shared import safe_int as _safe_int

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)

# Consistent hash for empty graphs (SHA-256 of "empty_knowledge_graph")
_EMPTY_GRAPH_HASH = hashlib.sha256(b"empty_knowledge_graph").hexdigest()[:32]


class RegressionChecker:
    """Captures and compares knowledge graph metrics between snapshots."""

    def __init__(self, kg: "KnowledgeGraph") -> None:
        self._kg = kg

    def capture_metrics(self) -> dict:
        """Build a metrics snapshot from the current knowledge graph state.

        Returns dict with: node_count, edge_count, locked_count, graph_hash,
        captured_at.
        """
        G = self._kg.to_networkx()

        node_count = G.number_of_nodes()
        edge_count = G.number_of_edges()
        locked_count = self._kg.count_locked()

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
            "captured_at": datetime.now(UTC).isoformat(),
        }

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

        return {
            "status": status,
            "discrepancies": discrepancies,
            "previous": previous,
            "current": current,
        }
