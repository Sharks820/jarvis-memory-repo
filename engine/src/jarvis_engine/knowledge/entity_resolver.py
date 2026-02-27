"""Entity resolution for the knowledge graph.

Detects near-duplicate fact nodes by label similarity (string and optional
embedding), then merges them by transferring edges and recording history.
Thread safety: all writes go through KnowledgeGraph._write_lock.
"""

from __future__ import annotations

import difflib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MergeCandidate:
    """A pair of nodes that may refer to the same entity."""

    node_a_id: str
    node_b_id: str
    label_a: str
    label_b: str
    similarity: float
    merge_reason: str


@dataclass
class ResolutionResult:
    """Summary of an auto-resolve run."""

    candidates_found: int
    merges_applied: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity resolver
# ---------------------------------------------------------------------------


class EntityResolver:
    """Detects and merges near-duplicate nodes in the knowledge graph."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        embed_service: object | None = None,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._kg = kg
        self._embed_service = embed_service
        self._threshold = similarity_threshold

    # ------------------------------------------------------------------
    # Schema -- ensure merge history table exists
    # ------------------------------------------------------------------

    def _ensure_merge_history(self) -> None:
        """Create kg_merge_history table if it does not exist (idempotent)."""
        with self._kg.write_lock:
            self._kg.db.executescript("""
                CREATE TABLE IF NOT EXISTS kg_merge_history (
                    merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keep_id TEXT NOT NULL,
                    remove_id TEXT NOT NULL,
                    keep_label TEXT NOT NULL,
                    remove_label TEXT NOT NULL,
                    canonical_label TEXT DEFAULT NULL,
                    edges_transferred INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def find_duplicates(
        self,
        branch: str | None = None,
        limit: int = 100,
    ) -> list[MergeCandidate]:
        """Find near-duplicate node pairs within each node_type group.

        Args:
            branch: If provided, only consider nodes whose node_type matches.
                    Maps to the ``node_type`` column in kg_nodes.
            limit:  Maximum number of candidates to return.

        Returns:
            List of MergeCandidate sorted by similarity descending.
        """
        # Load nodes (grouped by node_type)
        with self._kg.db_lock:
            if branch is not None:
                cur = self._kg.db.execute(
                    "SELECT node_id, label, node_type FROM kg_nodes WHERE node_type = ?",
                    (branch,),
                )
            else:
                cur = self._kg.db.execute(
                    "SELECT node_id, label, node_type FROM kg_nodes"
                )
            rows = cur.fetchall()

        # Group by node_type so we only compare within the same category
        groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for row in rows:
            groups[row[2]].append((row[0], row[1]))

        candidates: list[MergeCandidate] = []

        for _node_type, members in groups.items():
            n = len(members)
            for i in range(n):
                for j in range(i + 1, n):
                    id_a, label_a = members[i]
                    id_b, label_b = members[j]

                    string_sim = difflib.SequenceMatcher(
                        None, label_a.lower(), label_b.lower()
                    ).ratio()

                    embed_sim = 0.0
                    reason = "string"
                    if self._embed_service is not None:
                        try:
                            vec_a = self._embed_service.embed(label_a)
                            vec_b = self._embed_service.embed(label_b)
                            embed_sim = self._cosine_similarity(vec_a, vec_b)
                        except Exception:
                            logger.debug(
                                "Embedding similarity failed for %r / %r",
                                label_a,
                                label_b,
                            )

                    combined = max(string_sim, embed_sim)
                    if embed_sim > string_sim:
                        reason = "embedding"

                    if combined >= self._threshold:
                        candidates.append(
                            MergeCandidate(
                                node_a_id=id_a,
                                node_b_id=id_b,
                                label_a=label_a,
                                label_b=label_b,
                                similarity=combined,
                                merge_reason=reason,
                            )
                        )

        # Sort by similarity descending, then cap
        candidates.sort(key=lambda c: c.similarity, reverse=True)
        return candidates[:limit]

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_nodes(
        self,
        keep_id: str,
        remove_id: str,
        *,
        canonical_label: str | None = None,
    ) -> bool:
        """Merge *remove_id* into *keep_id*, transferring all edges.

        Acquires _write_lock for the entire operation.  Creates the
        ``kg_merge_history`` table on first use.

        Args:
            keep_id:         Node ID to retain.
            remove_id:       Node ID to delete after transfer.
            canonical_label: If provided, set as the label on *keep_id*.

        Returns:
            True on success, False if either node is missing.
        """
        self._ensure_merge_history()

        with self._kg.write_lock:
            # Verify both nodes exist
            keep_row = self._kg.db.execute(
                "SELECT label, confidence FROM kg_nodes WHERE node_id = ?",
                (keep_id,),
            ).fetchone()
            remove_row = self._kg.db.execute(
                "SELECT label, confidence FROM kg_nodes WHERE node_id = ?",
                (remove_id,),
            ).fetchone()

            if keep_row is None or remove_row is None:
                return False

            keep_label = keep_row[0]
            keep_conf = keep_row[1]
            remove_label = remove_row[0]
            remove_conf = remove_row[1]

            edges_transferred = 0

            # Transfer outgoing edges FROM remove_id -> keep_id
            outgoing = self._kg.db.execute(
                "SELECT target_id, relation, confidence, source_record "
                "FROM kg_edges WHERE source_id = ?",
                (remove_id,),
            ).fetchall()
            for edge in outgoing:
                target_id, relation, conf, src = edge
                # Skip self-loops that would result from the merge
                if target_id == keep_id:
                    continue
                cur = self._kg.db.execute(
                    "INSERT OR IGNORE INTO kg_edges "
                    "(source_id, target_id, relation, confidence, source_record) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (keep_id, target_id, relation, conf, src),
                )
                edges_transferred += cur.rowcount

            # Transfer incoming edges TO remove_id -> keep_id
            incoming = self._kg.db.execute(
                "SELECT source_id, relation, confidence, source_record "
                "FROM kg_edges WHERE target_id = ?",
                (remove_id,),
            ).fetchall()
            for edge in incoming:
                source_id, relation, conf, src = edge
                if source_id == keep_id:
                    continue
                cur = self._kg.db.execute(
                    "INSERT OR IGNORE INTO kg_edges "
                    "(source_id, target_id, relation, confidence, source_record) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_id, keep_id, relation, conf, src),
                )
                edges_transferred += cur.rowcount

            # Update keep_id: canonical label + max confidence
            new_label = canonical_label if canonical_label is not None else keep_label
            new_conf = max(keep_conf, remove_conf)
            self._kg.db.execute(
                "UPDATE kg_nodes SET label = ?, confidence = ?, "
                "updated_at = datetime('now') WHERE node_id = ?",
                (new_label, new_conf, keep_id),
            )

            # Delete edges referencing remove_id, then the node itself
            self._kg.db.execute(
                "DELETE FROM kg_edges WHERE source_id = ? OR target_id = ?",
                (remove_id, remove_id),
            )
            self._kg.db.execute(
                "DELETE FROM kg_nodes WHERE node_id = ?", (remove_id,)
            )

            # Record in merge history
            self._kg.db.execute(
                "INSERT INTO kg_merge_history "
                "(keep_id, remove_id, keep_label, remove_label, "
                " canonical_label, edges_transferred) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    keep_id,
                    remove_id,
                    keep_label,
                    remove_label,
                    canonical_label,
                    edges_transferred,
                ),
            )

            self._kg.db.commit()
            # Invalidate NetworkX cache (bypassed add_fact/add_edge)
            self._kg._mutation_counter += 1

        logger.info(
            "Merged node %s into %s (edges transferred: %d)",
            remove_id,
            keep_id,
            edges_transferred,
        )
        return True

    # ------------------------------------------------------------------
    # Auto-resolve
    # ------------------------------------------------------------------

    def auto_resolve(
        self,
        branch: str | None = None,
        dry_run: bool = False,
    ) -> ResolutionResult:
        """Find and merge all duplicate pairs.

        For each candidate pair the node with higher confidence is kept.
        On a tie, the node with more total edges wins.

        Args:
            branch:  Optional node_type filter.
            dry_run: If True, return candidates without applying merges.

        Returns:
            ResolutionResult with counts and any errors encountered.
        """
        candidates = self.find_duplicates(branch=branch)
        result = ResolutionResult(candidates_found=len(candidates), merges_applied=0)

        if dry_run:
            return result

        # Track already-removed IDs to avoid double-merging
        removed: set[str] = set()

        for cand in candidates:
            if cand.node_a_id in removed or cand.node_b_id in removed:
                continue

            keep_id, remove_id = self._pick_keeper(cand.node_a_id, cand.node_b_id)

            try:
                ok = self.merge_nodes(keep_id, remove_id)
                if ok:
                    result.merges_applied += 1
                    removed.add(remove_id)
                else:
                    result.errors.append(
                        f"Merge failed (missing node): {keep_id} <- {remove_id}"
                    )
            except Exception as exc:
                result.errors.append(
                    f"Merge error ({keep_id} <- {remove_id}): {exc}"
                )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pick_keeper(self, id_a: str, id_b: str) -> tuple[str, str]:
        """Choose which node to keep based on confidence then edge count.

        Returns (keep_id, remove_id).
        """
        with self._kg.db_lock:
            row_a = self._kg.db.execute(
                "SELECT confidence FROM kg_nodes WHERE node_id = ?", (id_a,)
            ).fetchone()
            row_b = self._kg.db.execute(
                "SELECT confidence FROM kg_nodes WHERE node_id = ?", (id_b,)
            ).fetchone()

            conf_a = row_a[0] if row_a else 0.0
            conf_b = row_b[0] if row_b else 0.0

            if conf_a != conf_b:
                return (id_a, id_b) if conf_a >= conf_b else (id_b, id_a)

            # Tie-break: total edge count
            edges_a = self._kg.db.execute(
                "SELECT COUNT(*) FROM kg_edges WHERE source_id = ? OR target_id = ?",
                (id_a, id_a),
            ).fetchone()[0]
            edges_b = self._kg.db.execute(
                "SELECT COUNT(*) FROM kg_edges WHERE source_id = ? OR target_id = ?",
                (id_b, id_b),
            ).fetchone()[0]

        return (id_a, id_b) if edges_a >= edges_b else (id_b, id_a)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
