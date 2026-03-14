"""Entity resolution for the knowledge graph.

Detects near-duplicate fact nodes by label similarity (string and optional
embedding), then merges them by transferring edges and recording history.
Thread safety: all writes go through KnowledgeGraph._write_lock.
"""

from __future__ import annotations

import difflib
import logging
import sqlite3
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any

from jarvis_engine._constants import EMBEDDING_DIM
from jarvis_engine.knowledge._base import upsert_fts_kg, delete_fts_kg

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# Data classes


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


# Entity resolver

#: Maximum time in seconds allowed for O(N²) string comparison before a
#: warning is logged recommending the embed_service for vector mode.
_STRING_COMPARISON_WARNING_THRESHOLD_S: float = 5.0


class EntityResolver:
    """Detects and merges near-duplicate nodes in the knowledge graph."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        embed_service: Any | None = None,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._kg = kg
        self._embed_service = embed_service
        self._threshold = similarity_threshold

    # Schema -- ensure merge history table exists

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

    # Duplicate detection

    # Default number of nearest neighbours to retrieve per node when using
    # vector-based candidate retrieval.  Kept small to bound total comparisons
    # to O(N * K) instead of O(N^2).
    _VEC_TOP_K: int = 10

    def find_duplicates(
        self,
        branch: str | None = None,
        limit: int = 100,
        top_k: int | None = None,
    ) -> list[MergeCandidate]:
        """Find near-duplicate node pairs within each node_type group.

        When ``embed_service`` is available, candidate retrieval uses vector
        similarity (O(N*K)) instead of all-pairs string comparison (O(N^2)).
        The expensive ``SequenceMatcher`` is only run on the top-K vector
        candidates per node, making this scalable to large graphs.

        When ``embed_service`` is None, falls back to the original all-pairs
        string-comparison approach with a 500-node safety cap per group.

        Args:
            branch: If provided, only consider nodes whose node_type matches.
                    Maps to the ``node_type`` column in kg_nodes.
            limit:  Maximum number of candidates to return.
            top_k:  Number of nearest neighbours per node for vector retrieval.
                    Defaults to ``_VEC_TOP_K`` (10).

        Returns:
            List of MergeCandidate sorted by similarity descending.
        """
        if top_k is None:
            top_k = self._VEC_TOP_K

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

            if self._embed_service is not None:
                # --- Vector-based candidate retrieval (O(N*K)) ---
                self._find_duplicates_vector(members, candidates, top_k)
            else:
                # --- Fallback: all-pairs string comparison (O(N^2)) ---
                if n > 200:
                    logger.warning(
                        "Skipping duplicate detection for node_type group with %d nodes "
                        "(exceeds 200-node safety limit — provide embed_service for vector mode)",
                        n,
                    )
                    continue
                self._find_duplicates_string(members, candidates)

        # Sort by similarity descending, then cap
        candidates.sort(key=lambda c: c.similarity, reverse=True)
        return candidates[:limit]

    def _find_duplicates_vector(
        self,
        members: list[tuple[str, str]],
        candidates: list[MergeCandidate],
        top_k: int,
    ) -> None:
        """Vector-based candidate retrieval: embed all nodes, then for each
        node find the top-K most similar neighbours and run SequenceMatcher
        only on those pairs.

        Reduces O(N^2) to O(N*K) where K is ``top_k``.
        """
        embed_cache = self._embed_all_nodes(members)

        if not embed_cache:
            # All embeddings failed -- fall back to string-only for this group
            if len(members) <= 200:
                self._find_duplicates_string(members, candidates)
            return

        label_map = {nid: lbl for nid, lbl in members}
        embedded_ids = list(embed_cache.keys())
        embedded_vecs = [embed_cache[nid] for nid in embedded_ids]

        seen_pairs: set[tuple[str, str]] = set()

        try:
            import numpy as _np

            self._find_neighbours_numpy(
                _np,
                embedded_ids,
                embedded_vecs,
                label_map,
                top_k,
                seen_pairs,
                candidates,
            )
        except ImportError:
            self._find_neighbours_python(
                embedded_ids,
                embedded_vecs,
                label_map,
                top_k,
                seen_pairs,
                candidates,
            )

        # Also check nodes that failed embedding -- compare them string-only
        # against each other and against the top vector candidates.
        non_embedded = [(nid, lbl) for nid, lbl in members if nid not in embed_cache]
        if non_embedded and len(non_embedded) <= 200:
            self._find_duplicates_string(non_embedded, candidates)

    def _embed_all_nodes(
        self,
        members: list[tuple[str, str]],
    ) -> dict[str, list[float]]:
        """Embed all node labels, using batch call with individual fallback.

        Returns a dict mapping node_id to embedding vector.  May return an
        empty dict if all embeddings fail.
        """
        assert self._embed_service is not None  # caller guards this
        embed_cache: dict[str, list[float]] = {}
        all_ids = [nid for nid, _lbl in members]
        all_labels = [lbl for _nid, lbl in members]
        try:
            if hasattr(self._embed_service, "embed_batch"):
                batch_results = self._embed_service.embed_batch(all_labels)
                for nid, vec in zip(all_ids, batch_results):
                    embed_cache[nid] = vec
            else:
                for node_id, label in members:
                    embed_cache[node_id] = self._embed_service.embed(label)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.debug(
                "Batch embedding failed, falling back to individual calls: %s", exc
            )
            embed_cache.clear()
            for node_id, label in members:
                try:
                    embed_cache[node_id] = self._embed_service.embed(label)
                except (RuntimeError, ValueError, OSError) as exc2:
                    logger.debug(
                        "Embedding failed for node %s (%r): %s", node_id, label, exc2
                    )
        return embed_cache

    def _evaluate_pair(
        self,
        id_a: str,
        id_b: str,
        embed_sim: float,
        label_map: dict[str, str],
        seen_pairs: set[tuple[str, str]],
        candidates: list[MergeCandidate],
    ) -> None:
        """Evaluate a single pair of nodes, adding to candidates if above threshold."""
        pair_key = (min(id_a, id_b), max(id_a, id_b))
        if pair_key in seen_pairs:
            return
        seen_pairs.add(pair_key)

        label_a = label_map[id_a]
        label_b = label_map[id_b]

        string_sim = difflib.SequenceMatcher(
            None, label_a.lower(), label_b.lower()
        ).ratio()

        combined = max(string_sim, embed_sim)
        reason = "embedding" if embed_sim > string_sim else "string"

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

    def _find_neighbours_numpy(
        self,
        _np: ModuleType,
        embedded_ids: list[str],
        embedded_vecs: list[list[float]],
        label_map: dict[str, str],
        top_k: int,
        seen_pairs: set[tuple[str, str]],
        candidates: list[MergeCandidate],
    ) -> None:
        """Find top-K neighbours using numpy vectorized cosine similarity."""
        n = len(embedded_ids)
        mat = _np.array(embedded_vecs, dtype=_np.float32)
        norms = _np.linalg.norm(mat, axis=1, keepdims=True)
        norms = _np.where(norms == 0, 1.0, norms)
        normed = mat / norms
        sim_matrix = normed @ normed.T
        _np.fill_diagonal(sim_matrix, -1.0)

        for i in range(n):
            id_a = embedded_ids[i]
            row = sim_matrix[i]
            if top_k < n - 1:
                top_indices = _np.argpartition(row, -top_k)[-top_k:]
            else:
                top_indices = _np.arange(n)
                top_indices = top_indices[top_indices != i]

            for j in top_indices:
                self._evaluate_pair(
                    id_a,
                    embedded_ids[j],
                    float(row[j]),
                    label_map,
                    seen_pairs,
                    candidates,
                )

    def _find_neighbours_python(
        self,
        embedded_ids: list[str],
        embedded_vecs: list[list[float]],
        label_map: dict[str, str],
        top_k: int,
        seen_pairs: set[tuple[str, str]],
        candidates: list[MergeCandidate],
    ) -> None:
        """Find top-K neighbours using pure-Python pairwise cosine similarity."""
        n = len(embedded_ids)
        for i in range(n):
            id_a = embedded_ids[i]
            vec_a = embedded_vecs[i]

            scored: list[tuple[float, int]] = []
            for j in range(n):
                if i == j:
                    continue
                sim = self._cosine_similarity(vec_a, embedded_vecs[j])
                scored.append((sim, j))

            scored.sort(key=lambda x: x[0], reverse=True)
            top_neighbours = scored[:top_k]

            for embed_sim, j in top_neighbours:
                self._evaluate_pair(
                    id_a,
                    embedded_ids[j],
                    embed_sim,
                    label_map,
                    seen_pairs,
                    candidates,
                )

    def _find_duplicates_string(
        self,
        members: list[tuple[str, str]],
        candidates: list[MergeCandidate],
    ) -> None:
        """All-pairs string comparison using SequenceMatcher. O(N^2)."""
        n = len(members)
        t0 = _time.monotonic()
        for i in range(n):
            for j in range(i + 1, n):
                id_a, label_a = members[i]
                id_b, label_b = members[j]

                string_sim = difflib.SequenceMatcher(
                    None, label_a.lower(), label_b.lower()
                ).ratio()

                if string_sim >= self._threshold:
                    candidates.append(
                        MergeCandidate(
                            node_a_id=id_a,
                            node_b_id=id_b,
                            label_a=label_a,
                            label_b=label_b,
                            similarity=string_sim,
                            merge_reason="string",
                        )
                    )
        elapsed = _time.monotonic() - t0
        if elapsed > _STRING_COMPARISON_WARNING_THRESHOLD_S:
            logger.warning(
                "String duplicate detection took %.1fs for %d nodes — "
                "consider providing embed_service for faster vector mode",
                elapsed,
                n,
            )

    # Merge

    def _precompute_merge_embedding(self, label: str) -> bytes | None:
        """Pre-compute vec embedding blob for a label WITHOUT holding any lock.

        Returns packed blob ready for DB insertion, or ``None`` when the
        embedding service is unavailable or the computation fails.
        """
        if self._embed_service is None or not getattr(
            self._kg, "_vec_available", False
        ):
            return None
        try:
            import struct

            embedding = self._embed_service.embed(label, prefix="search_document")
            if len(embedding) == EMBEDDING_DIM:
                return struct.pack(f"{len(embedding)}f", *embedding)
        except (RuntimeError, TypeError, ValueError, OSError) as exc:
            logger.debug("Vec embedding pre-compute for merge label failed: %s", exc)
        return None

    def merge_nodes(
        self,
        keep_id: str,
        remove_id: str,
        *,
        canonical_label: str | None = None,
        _lock_held: bool = False,
    ) -> bool:
        """Merge *remove_id* into *keep_id*, transferring all edges.

        Acquires _write_lock for the entire operation unless *_lock_held*
        is True (used internally by auto_resolve to avoid deadlock when
        the caller already holds the lock).  Creates the
        ``kg_merge_history`` table on first use.

        Embedding is computed BEFORE acquiring the write lock to avoid holding
        the lock during the potentially slow embedding model call.

        Args:
            keep_id:         Node ID to retain.
            remove_id:       Node ID to delete after transfer.
            canonical_label: If provided, set as the label on *keep_id*.
            _lock_held:      If True, caller already holds _write_lock.

        Returns:
            True on success, False if either node is missing.
        """
        self._ensure_merge_history()

        # Pre-compute embedding outside the lock.  When canonical_label is
        # provided we know the final label; otherwise we pre-read the keep
        # node's current label for embedding (the lock will verify it hasn't
        # changed before using it).
        embed_label = canonical_label
        if embed_label is None:
            with self._kg.db_lock:
                row = self._kg.db.execute(
                    "SELECT label FROM kg_nodes WHERE node_id = ?", (keep_id,)
                ).fetchone()
            embed_label = row[0] if row else None

        embedding_blob: bytes | None = None
        if embed_label is not None:
            embedding_blob = self._precompute_merge_embedding(embed_label)

        return self._merge_nodes_impl(
            keep_id,
            remove_id,
            canonical_label=canonical_label,
            _lock_held=_lock_held,
            _embedding_blob=embedding_blob,
        )

    def _merge_nodes_impl(
        self,
        keep_id: str,
        remove_id: str,
        *,
        canonical_label: str | None = None,
        _lock_held: bool = False,
        _embedding_blob: bytes | None = None,
    ) -> bool:
        """Internal merge implementation. Acquires _write_lock unless _lock_held."""
        if _lock_held:
            return self._merge_nodes_core(
                keep_id,
                remove_id,
                canonical_label=canonical_label,
                _embedding_blob=_embedding_blob,
            )
        with self._kg.write_lock:
            return self._merge_nodes_core(
                keep_id,
                remove_id,
                canonical_label=canonical_label,
                _embedding_blob=_embedding_blob,
            )

    def _merge_nodes_core(
        self,
        keep_id: str,
        remove_id: str,
        *,
        canonical_label: str | None = None,
        _embedding_blob: bytes | None = None,
    ) -> bool:
        """Core merge logic. Caller MUST hold _write_lock.

        Args:
            _embedding_blob: Pre-computed vec embedding blob for the keep
                node's new label.  When ``None`` the vec index update is
                skipped (embedding should have been pre-computed outside
                the lock by the caller).
        """
        # Verify both nodes exist and are not locked
        keep_row, remove_row = self._validate_merge_nodes(keep_id, remove_id)
        if keep_row is None or remove_row is None:
            return False

        keep_label = keep_row[0]
        keep_conf = keep_row[1]
        remove_label = remove_row[0]
        remove_conf = remove_row[1]

        # Transfer all edges from remove_id to keep_id
        edges_transferred = self._transfer_edges(keep_id, remove_id)

        # Update the kept node's label, confidence, FTS, and vec indexes
        new_label = canonical_label if canonical_label is not None else keep_label
        new_conf = max(keep_conf, remove_conf)
        self._update_kept_node(keep_id, new_label, new_conf, _embedding_blob)

        # Delete the removed node and all its index entries
        self._delete_merged_node(remove_id)

        # Record in merge history, commit, and invalidate cache
        self._record_merge_history(
            keep_id,
            remove_id,
            keep_label,
            remove_label,
            canonical_label,
            edges_transferred,
        )

        logger.info(
            "Merged node %s into %s (edges transferred: %d)",
            remove_id,
            keep_id,
            edges_transferred,
        )
        return True

    def _validate_merge_nodes(
        self,
        keep_id: str,
        remove_id: str,
    ) -> tuple[tuple | None, tuple | None]:
        """Verify both nodes exist and are not locked.

        Returns (keep_row, remove_row) where each is
        (label, confidence, locked) or None if missing/locked.
        """
        keep_row = self._kg.db.execute(
            "SELECT label, confidence, locked FROM kg_nodes WHERE node_id = ?",
            (keep_id,),
        ).fetchone()
        remove_row = self._kg.db.execute(
            "SELECT label, confidence, locked FROM kg_nodes WHERE node_id = ?",
            (remove_id,),
        ).fetchone()

        if keep_row is None or remove_row is None:
            return (None, None)

        if keep_row[2] or remove_row[2]:
            logger.warning(
                "Refusing to merge locked nodes: keep=%s (locked=%s), remove=%s (locked=%s)",
                keep_id,
                bool(keep_row[2]),
                remove_id,
                bool(remove_row[2]),
            )
            return (None, None)

        return (keep_row, remove_row)

    def _transfer_edges(self, keep_id: str, remove_id: str) -> int:
        """Transfer all edges from remove_id to keep_id, skipping self-loops.

        Returns the number of edges actually transferred.
        """
        edges_transferred = 0

        # Transfer outgoing edges FROM remove_id -> keep_id
        outgoing = self._kg.db.execute(
            "SELECT target_id, relation, confidence, source_record "
            "FROM kg_edges WHERE source_id = ?",
            (remove_id,),
        ).fetchall()
        for target_id, relation, conf, src in outgoing:
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
        for source_id, relation, conf, src in incoming:
            if source_id == keep_id:
                continue
            cur = self._kg.db.execute(
                "INSERT OR IGNORE INTO kg_edges "
                "(source_id, target_id, relation, confidence, source_record) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, keep_id, relation, conf, src),
            )
            edges_transferred += cur.rowcount

        return edges_transferred

    def _update_kept_node(
        self,
        keep_id: str,
        new_label: str,
        new_conf: float,
        embedding_blob: bytes | None,
    ) -> None:
        """Update the kept node's label, confidence, and search indexes."""
        self._kg.db.execute(
            "UPDATE kg_nodes SET label = ?, confidence = ?, "
            "updated_at = datetime('now') WHERE node_id = ?",
            (new_label, new_conf, keep_id),
        )

        upsert_fts_kg(self._kg.db, keep_id, new_label)

        if embedding_blob is not None:
            try:
                self._kg.db.execute(
                    "DELETE FROM vec_kg_nodes WHERE node_id = ?", (keep_id,)
                )
                self._kg.db.execute(
                    "INSERT INTO vec_kg_nodes(node_id, embedding) VALUES (?, ?)",
                    (keep_id, embedding_blob),
                )
            except (sqlite3.Error, ValueError) as exc:
                logger.debug(
                    "Vec embedding update for merged node %s failed: %s", keep_id, exc
                )

    def _delete_merged_node(self, remove_id: str) -> None:
        """Delete the merged-away node and all its edge/index entries."""
        self._kg.db.execute(
            "DELETE FROM kg_edges WHERE source_id = ? OR target_id = ?",
            (remove_id, remove_id),
        )

        delete_fts_kg(self._kg.db, remove_id)
        if getattr(self._kg, "_vec_available", False):
            try:
                self._kg.db.execute(
                    "DELETE FROM vec_kg_nodes WHERE node_id = ?", (remove_id,)
                )
            except sqlite3.Error as exc:
                logger.debug(
                    "Vec embedding delete for removed node %s failed: %s",
                    remove_id,
                    exc,
                )

        self._kg.db.execute("DELETE FROM kg_nodes WHERE node_id = ?", (remove_id,))

    def _record_merge_history(
        self,
        keep_id: str,
        remove_id: str,
        keep_label: str,
        remove_label: str,
        canonical_label: str | None,
        edges_transferred: int,
    ) -> None:
        """Write merge history record, commit, and invalidate cache."""
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
        self._kg.invalidate_cache()

    # Auto-resolve

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

        # Ensure merge history table exists before acquiring _write_lock
        # to avoid nested lock acquisition inside _merge_nodes_core.
        self._ensure_merge_history()

        for cand in candidates:
            if cand.node_a_id in removed or cand.node_b_id in removed:
                continue

            try:
                # Pre-compute embedding OUTSIDE the lock.  We pre-read the
                # likely keeper's label (pick_keeper uses confidence then edge
                # count, so we optimistically pick outside the lock and the
                # lock will re-verify).  Even if the final keeper differs, the
                # worst case is the embedding is unused -- correctness is not
                # affected, only performance.
                keeper_id, _ = self._pick_keeper(cand.node_a_id, cand.node_b_id)
                with self._kg.db_lock:
                    lbl_row = self._kg.db.execute(
                        "SELECT label FROM kg_nodes WHERE node_id = ?",
                        (keeper_id,),
                    ).fetchone()
                embedding_blob: bytes | None = None
                if lbl_row is not None:
                    embedding_blob = self._precompute_merge_embedding(lbl_row[0])

                # Acquire _write_lock around both pick and merge to prevent
                # TOCTOU race where node data changes between selection and merge.
                with self._kg.write_lock:
                    keep_id, remove_id = self._pick_keeper_unlocked(
                        cand.node_a_id,
                        cand.node_b_id,
                    )
                    ok = self._merge_nodes_core(
                        keep_id,
                        remove_id,
                        _embedding_blob=embedding_blob
                        if keep_id == keeper_id
                        else None,
                    )
                if ok:
                    result.merges_applied += 1
                    removed.add(remove_id)
                else:
                    result.errors.append(
                        f"Merge failed (missing node): {keep_id} <- {remove_id}"
                    )
            except (sqlite3.Error, ValueError, OSError) as exc:
                result.errors.append(
                    f"Merge error ({cand.node_a_id} <- {cand.node_b_id}): {exc}"
                )

        return result

    # Internal helpers

    def _pick_keeper(self, id_a: str, id_b: str) -> tuple[str, str]:
        """Choose which node to keep based on confidence then edge count.

        Returns (keep_id, remove_id).  Acquires db_lock for standalone use.
        """
        with self._kg.db_lock:
            return self._pick_keeper_unlocked(id_a, id_b)

    def _pick_keeper_unlocked(self, id_a: str, id_b: str) -> tuple[str, str]:
        """Choose which node to keep. Caller MUST hold _write_lock or _db_lock.

        Returns (keep_id, remove_id).
        """
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
        """Compute cosine similarity between two vectors.

        Uses numpy for vectorized computation when available, with a
        pure-Python fallback.  Returns 0.0 if vectors have different
        dimensions or either is zero-norm.
        """
        if len(a) != len(b):
            return 0.0
        try:
            import numpy as np

            a_arr = np.array(a, dtype=np.float32)
            b_arr = np.array(b, dtype=np.float32)
            norm_a = np.linalg.norm(a_arr)
            norm_b = np.linalg.norm(b_arr)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
        except ImportError:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5
            norm_b = sum(x * x for x in b) ** 0.5
            if norm_a < 1e-12 or norm_b < 1e-12:
                return 0.0
            return dot / (norm_a * norm_b)
