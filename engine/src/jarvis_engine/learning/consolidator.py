"""Memory consolidation engine that clusters similar episodic records and
synthesises them into authoritative semantic facts.

Pipeline:
1. Query recent episodic records from MemoryEngine.
2. Compute/retrieve embeddings for each record's summary.
3. Greedy-cluster records by cosine similarity >= threshold.
4. For groups >= min_group_size, ask the LLM (or concatenate) to produce a
   single fact statement.
5. Store the new semantic record and tag originals with a back-reference.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from jarvis_engine._shared import sha256_hex

import numpy as np

from jarvis_engine._compat import UTC


def _parse_days_since(raw_date_str: str, now: datetime, default: float = 365.0) -> float:
    """Parse an ISO-8601 date string and return the number of days since *now*.

    Handles the ``Z`` UTC suffix and naive datetimes (assumes UTC).
    Returns *default* when the string is empty or unparseable.
    """
    raw = str(raw_date_str).strip() if raw_date_str else ""
    if not raw:
        return default
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return default


if TYPE_CHECKING:
    from jarvis_engine.gateway.models import GatewayResponse, ModelGateway
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

_CONSOLIDATION_PROMPT = (
    "You are a knowledge consolidation assistant. Below are several related "
    "memory snippets from a personal AI assistant's episodic memory. "
    "Synthesise them into a single authoritative fact statement. "
    "Be concise (1-3 sentences). Output ONLY the fact statement, nothing else."
    "\n\n--- MEMORY SNIPPETS ---\n{snippets}\n--- END ---"
)


@dataclass
class ConsolidationResult:
    """Statistics returned by a consolidation run."""

    groups_found: int = 0
    records_consolidated: int = 0
    new_facts_created: int = 0
    errors: list[str] = field(default_factory=list)


class MemoryConsolidator:
    """Clusters similar episodic memory records and consolidates them into
    authoritative semantic facts via LLM summarisation (or simple
    concatenation when no gateway is available).
    """

    def __init__(
        self,
        engine: "MemoryEngine",
        gateway: "ModelGateway | None" = None,
        embed_service: "EmbeddingService | None" = None,
        similarity_threshold: float = 0.75,
        min_group_size: int = 3,
    ) -> None:
        self._engine = engine
        self._gateway = gateway
        self._embed_service = embed_service
        self._similarity_threshold = similarity_threshold
        self._min_group_size = min_group_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consolidate(
        self,
        branch: str | None = None,
        max_groups: int = 20,
        dry_run: bool = False,
    ) -> ConsolidationResult:
        """Run the full consolidation pipeline.

        Args:
            branch: Restrict to a specific branch (``None`` = all branches).
            max_groups: Maximum number of groups to process in one run.
            dry_run: If ``True``, compute clusters but write nothing.

        Returns:
            :class:`ConsolidationResult` with statistics.
        """
        result = ConsolidationResult()

        # Step 1 -- fetch recent episodic records
        try:
            records = self._fetch_episodic_records(branch, limit=500)
        except Exception as exc:
            result.errors.append(f"fetch failed: {exc}")
            return result

        if not records:
            return result

        # Step 2 -- compute embeddings
        try:
            embeddings = self._compute_embeddings(records)
        except Exception as exc:
            result.errors.append(f"embedding failed: {exc}")
            return result

        if not embeddings:
            # Embedding service unavailable — cannot cluster meaningfully
            return result

        # Step 3 -- cluster
        try:
            groups = self._cluster_records(records, embeddings)
        except Exception as exc:
            result.errors.append(f"clustering failed: {exc}")
            return result

        result.groups_found = len(groups)

        # Step 4 -- consolidate each group (up to max_groups)
        for group_indices in groups[:max_groups]:
            group_records = [records[i] for i in group_indices]

            # Summarise via LLM (or concatenation fallback)
            try:
                summary = self._consolidate_group(group_records)
            except Exception as exc:
                result.errors.append(f"summarisation failed: {exc}")
                continue

            if not summary:
                continue

            if dry_run:
                result.records_consolidated += len(group_records)
                result.new_facts_created += 1
                continue

            # Store the new consolidated record
            try:
                new_id = self._store_consolidated(summary, group_records, branch)
            except Exception as exc:
                result.errors.append(f"store failed: {exc}")
                continue

            if not new_id:
                # Duplicate content hash -- no new record was created
                continue

            # Tag originals
            try:
                self._tag_originals(group_records, new_id)
            except Exception as exc:
                result.errors.append(f"tag failed: {exc}")
                # The consolidated record was still created, so count it.

            result.records_consolidated += len(group_records)
            result.new_facts_created += 1

        # Tier update pass: re-classify records by relevance (LEARN-06)
        # Reuse already-fetched records to avoid a duplicate DB query
        if not dry_run and records:
            try:
                tier_changes = self._update_tiers(records)
                if tier_changes > 0:
                    logger.info("Updated %d record tiers based on relevance scoring", tier_changes)
            except Exception as exc:
                logger.warning("Tier update pass failed: %s", exc)
                result.errors.append(f"tier update failed: {exc}")

        return result

    def _update_tiers(self, records: list[dict]) -> int:
        """Update record tiers based on relevance scoring.

        Uses engine.update_tiers_batch() for proper write locking and
        atomic commit semantics.

        Returns the number of records whose tier was changed.
        """
        try:
            from jarvis_engine.learning.relevance import (
                classify_tier_by_relevance,
                compute_relevance_score,
            )
        except ImportError:
            return 0

        now = datetime.now(UTC)
        batch: list[tuple[str, str]] = []  # (record_id, new_tier)

        for record in records:
            access_count = record.get("access_count", 0) or 0
            record_id = record.get("record_id")
            if not record_id:
                continue

            # Compute days since access and creation
            last_accessed_str = record.get("last_accessed", "") or ""
            created_str = record.get("ts", "") or record.get("created_at", "") or ""

            days_since_access = _parse_days_since(last_accessed_str, now)
            days_since_creation = _parse_days_since(created_str, now)

            relevance = compute_relevance_score(access_count, days_since_access, days_since_creation)
            new_tier = classify_tier_by_relevance(relevance, days_since_creation)
            current_tier = record.get("tier", "warm")

            if new_tier != current_tier:
                batch.append((record_id, new_tier))

        if batch:
            self._engine.update_tiers_batch(batch)

        return len(batch)

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_records(
        self,
        records: list[dict],
        embeddings: list[list[float]],
    ) -> list[list[int]]:
        """Greedy single-linkage clustering by cosine similarity.

        Returns a list of index-groups where every pair in the group has
        cosine similarity >= ``self._similarity_threshold``.  Only groups
        with >= ``self._min_group_size`` members are returned.
        """
        n = len(records)
        if n == 0:
            return []

        # Build cosine similarity matrix using numpy
        mat = np.array(embeddings, dtype=np.float64)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        # Guard against zero-norm vectors
        norms = np.where(norms == 0, 1.0, norms)
        normed = mat / norms
        sim_matrix = normed @ normed.T

        assigned: set[int] = set()
        groups: list[list[int]] = []

        for i in range(n):
            if i in assigned:
                continue
            group = [i]
            assigned.add(i)
            for j in range(i + 1, n):
                if j in assigned:
                    continue
                # Check similarity with ALL current group members
                if all(
                    sim_matrix[j, g] >= self._similarity_threshold for g in group
                ):
                    group.append(j)
                    assigned.add(j)
            if len(group) >= self._min_group_size:
                groups.append(group)

        return groups

    # ------------------------------------------------------------------
    # LLM summarisation (with concatenation fallback)
    # ------------------------------------------------------------------

    def _consolidate_group(self, records: list[dict]) -> str | None:
        """Produce a single fact statement for a group of related records.

        Uses the LLM gateway when available; otherwise falls back to
        simple concatenation of summaries.
        """
        summaries = [r.get("summary", "") for r in records if r.get("summary")]
        if not summaries:
            return None

        if self._gateway is not None:
            return self._llm_summarise(summaries)

        # Fallback: concatenate with separator
        return " | ".join(summaries)

    def _llm_summarise(self, summaries: list[str]) -> str | None:
        """Call the LLM gateway to produce a consolidated fact."""
        from jarvis_engine.temporal import get_datetime_prompt

        snippets = "\n".join(f"- {s}" for s in summaries)
        prompt = _CONSOLIDATION_PROMPT.format(snippets=snippets)

        messages = [
            {"role": "system", "content": get_datetime_prompt()},
            {"role": "user", "content": prompt},
        ]
        response: GatewayResponse = self._gateway.complete(  # type: ignore[union-attr]
            messages=messages,
            max_tokens=256,
        )
        text = response.text.strip()
        return text if text else None

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _fetch_episodic_records(
        self,
        branch: str | None,
        limit: int,
    ) -> list[dict]:
        """Query the most recent episodic records from the engine."""
        query = (
            "SELECT * FROM records WHERE kind = 'episodic'"
        )
        params: list[object] = []
        if branch:
            query += " AND branch = ?"
            params.append(branch)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self._engine.db_lock:
            cur = self._engine.db.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def _compute_embeddings(self, records: list[dict]) -> list[list[float]]:
        """Compute or retrieve embeddings for every record's summary.

        Returns empty list when embedding service is unavailable — callers
        must check for this and skip clustering.
        """
        texts = [r.get("summary", "") or "" for r in records]
        if self._embed_service is not None:
            return self._embed_service.embed_batch(texts, prefix="search_document")
        # No embedding service — cannot meaningfully cluster
        logger.warning("Consolidation skipped: embedding service unavailable")
        return []

    def _store_consolidated(
        self,
        summary: str,
        group_records: list[dict],
        branch: str | None,
    ) -> str:
        """Insert a new semantic record for the consolidated fact.

        Returns the new record's ID.
        """
        content_hash = sha256_hex(summary)
        id_material = f"consolidated|{content_hash}"
        record_id = sha256_hex(id_material)[:32]

        ts = datetime.now(UTC).isoformat()
        resolved_branch = branch or (
            group_records[0].get("branch", "general") if group_records else "general"
        )

        # Use the highest confidence from input records, floored at 0.85
        input_confidences = [
            r.get("confidence", 0.0) for r in group_records
            if isinstance(r.get("confidence"), (int, float))
        ]
        confidence = max(input_confidences, default=0.85)
        if confidence < 0.85:
            confidence = 0.85

        record = {
            "record_id": record_id,
            "ts": ts,
            "source": "consolidation",
            "kind": "semantic",
            "task_id": "",
            "branch": resolved_branch,
            "tags": json.dumps(["consolidated"]),
            "summary": summary[:200],
            "content_hash": content_hash,
            "confidence": confidence,
            "tier": "warm",
            "access_count": 0,
            "last_accessed": "",
        }

        embedding: list[float] | None = None
        if self._embed_service is not None:
            embedding = self._embed_service.embed(summary, prefix="search_document")

        inserted = self._engine.insert_record(record, embedding=embedding)
        if not inserted:
            return ""  # duplicate -- no new fact created
        return record_id

    def _tag_originals(self, records: list[dict], new_record_id: str) -> None:
        """Append ``consolidated_into:<id>`` to each original record's tags.

        Re-reads current tags from the database to avoid overwriting
        concurrent tag updates (the ``records`` dicts may be stale).
        """
        tag_value = f"consolidated_into:{new_record_id}"

        with self._engine.write_lock:
            for rec in records:
                rid = rec.get("record_id")
                if not rid:
                    continue

                # Read current tags from DB (not the stale snapshot)
                row = self._engine.db.execute(
                    "SELECT tags FROM records WHERE record_id = ?",
                    (rid,),
                ).fetchone()
                if row is None:
                    continue

                existing_tags_raw = row[0] if row else "[]"
                try:
                    parsed = (
                        json.loads(existing_tags_raw)
                        if isinstance(existing_tags_raw, str)
                        else existing_tags_raw
                    )
                    existing_tags = parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    existing_tags = []

                if tag_value not in existing_tags:
                    existing_tags.append(tag_value)

                self._engine.db.execute(
                    "UPDATE records SET tags = ? WHERE record_id = ?",
                    (json.dumps(existing_tags), rid),
                )
            self._engine.db.commit()
