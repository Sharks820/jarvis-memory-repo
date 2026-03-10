"""Cross-branch reasoning for connecting knowledge across life domains.

Provides semantic search that enriches results with connections found via the
knowledge graph, and an ingest-time function that creates cross-branch edges
when new facts share keywords with facts in other branches.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

# Minimum word length to count as a keyword for cross-branch matching.
_MIN_KEYWORD_LEN = 4

from jarvis_engine._constants import STOP_WORDS as _STOP_WORDS
from jarvis_engine._shared import extract_keywords as _extract_keywords_core


class CrossBranchQueryResult(TypedDict):
    """Result of a cross-branch semantic query."""

    direct_results: list[dict]
    cross_branch_connections: list[dict]
    branches_involved: list[str]


def cross_branch_query(
    query: str,
    engine: "MemoryEngine",
    kg: "KnowledgeGraph",
    embed_service: "EmbeddingService",
    k: int = 10,
) -> CrossBranchQueryResult:
    """Perform a cross-branch semantic query.

    1. Embed the query.
    2. Search for similar records via vector search.
    3. For each result, look up KG neighbors in other branches.
    4. Return combined direct + cross-branch results.

    Args:
        query: Natural language query.
        engine: MemoryEngine for vector search.
        kg: KnowledgeGraph for graph traversal.
        embed_service: EmbeddingService for query embedding.
        k: Maximum number of direct results.

    Returns:
        Dict with 'direct_results', 'cross_branch_connections', 'branches_involved'.
    """
    # Step 1: Embed query
    embedding = embed_service.embed(query, prefix="search_query")

    # Step 2: Vector search
    vec_results = engine.search_vec(embedding, limit=k * 2)

    # Step 3: Build direct results and find cross-branch connections
    G = kg.to_networkx(copy=False)
    direct_results = []
    cross_branch_connections = []
    branches_seen: set[str] = set()

    for record_id, distance in vec_results[:k]:
        direct_results.append(
            {
                "record_id": record_id,
                "distance": distance,
            }
        )

        # Look for KG neighbors in other branches
        # Check if this record has a provenance node in the graph
        provenance_id = f"ingest:{record_id}"
        if provenance_id not in G:
            continue

        # Get the branch from this node's neighbors
        source_branch = _extract_branch(provenance_id)
        if source_branch:
            branches_seen.add(source_branch)

        # Check neighbors for cross-branch connections
        for neighbor in G.neighbors(provenance_id):
            neighbor_branch = _extract_branch(neighbor)
            if neighbor_branch and neighbor_branch != source_branch:
                branches_seen.add(neighbor_branch)
                edge_data = G.edges[provenance_id, neighbor]
                cross_branch_connections.append(
                    {
                        "source": provenance_id,
                        "target": neighbor,
                        "source_branch": source_branch or "unknown",
                        "target_branch": neighbor_branch,
                        "relation": edge_data.get("relation", "related"),
                    }
                )

        # Also check predecessors (incoming edges)
        for predecessor in G.predecessors(provenance_id):
            pred_branch = _extract_branch(predecessor)
            if pred_branch and pred_branch != source_branch:
                branches_seen.add(pred_branch)
                edge_data = G.edges[predecessor, provenance_id]
                cross_branch_connections.append(
                    {
                        "source": predecessor,
                        "target": provenance_id,
                        "source_branch": pred_branch,
                        "target_branch": source_branch or "unknown",
                        "relation": edge_data.get("relation", "related"),
                    }
                )

    return {
        "direct_results": direct_results,
        "cross_branch_connections": cross_branch_connections,
        "branches_involved": sorted(branches_seen),
    }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure-Python fallback)."""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


# Minimum cosine similarity for embedding-based cross-branch edge creation.
_EMBEDDING_SIMILARITY_THRESHOLD = 0.75


def create_cross_branch_edges(
    kg: "KnowledgeGraph",
    new_fact_id: str,
    record_id: str,
    embed_service: "EmbeddingService | None" = None,
) -> int:
    """Create cross-branch edges between a new fact and existing facts in other branches.

    Extracts keywords from the new fact's label, searches for matching facts
    in other branches, and creates 'cross_branch_related' edges.

    Args:
        kg: KnowledgeGraph instance.
        new_fact_id: Node ID of the newly added fact.
        record_id: Source record ID for provenance.

    Returns:
        Number of cross-branch edges created.
    """
    # Get the new fact's details
    node = kg.get_node(new_fact_id)
    if node is None:
        return 0

    label = node.get("label", "") if isinstance(node, dict) else ""
    source_branch = _extract_branch(new_fact_id)

    # Extract keywords from the label
    keywords = _extract_keywords(label)
    if not keywords:
        return 0

    # Cap at 5 keywords to limit query volume
    keywords = keywords[:5]

    edges_created = 0
    db = kg.db  # type: ignore[attr-defined]

    # Track already-linked target IDs to avoid duplicate edges from keyword + embedding
    linked_targets: set[str] = set()

    for keyword in keywords:
        # Escape SQL LIKE wildcards in keyword to prevent injection
        safe_keyword = keyword.replace("%", "\\%").replace("_", "\\_")
        safe_branch = (source_branch or "").replace("%", "\\%").replace("_", "\\_")
        # Search for matching nodes in OTHER branches via LIKE query
        try:
            with kg.db_lock:
                cursor = db.execute(
                    """SELECT node_id, label FROM kg_nodes
                       WHERE label LIKE ? ESCAPE '\\'
                       AND node_id NOT LIKE ? ESCAPE '\\'
                       LIMIT 10""",
                    (
                        f"%{safe_keyword}%",
                        f"{safe_branch}.%" if source_branch else new_fact_id,
                    ),
                )
                matches = cursor.fetchall()
        except (sqlite3.Error, OSError, ValueError) as exc:
            logger.warning(
                "Cross-branch keyword search failed for %r: %s", keyword, exc
            )
            continue

        for match in matches:
            target_id = match[0] if not isinstance(match, dict) else match["node_id"]
            target_branch = _extract_branch(target_id)

            # Skip same-branch matches
            if target_branch == source_branch:
                continue

            # Create cross-branch edge
            was_created = kg.add_edge(
                source_id=new_fact_id,
                target_id=target_id,
                relation="cross_branch_related",
                confidence=0.4,
                source_record=record_id,
            )
            if was_created:
                edges_created += 1
                linked_targets.add(target_id)

    # Supplementary: embedding-based similarity matching
    if embed_service is not None and label:
        try:
            new_embedding = embed_service.embed(label, prefix="search_document")
            # Fetch candidate nodes (random sample to avoid bias toward old nodes)
            with kg.db_lock:
                cursor = db.execute(
                    "SELECT node_id, label FROM kg_nodes ORDER BY RANDOM() LIMIT 200",
                )
                candidates = cursor.fetchall()

            # Filter candidates first, then batch-embed for performance
            filtered: list[tuple[str, str]] = []
            for candidate in candidates:
                cand_id = (
                    candidate[0]
                    if not isinstance(candidate, dict)
                    else candidate["node_id"]
                )
                cand_label = (
                    candidate[1]
                    if not isinstance(candidate, dict)
                    else candidate["label"]
                )
                cand_branch = _extract_branch(cand_id)

                if cand_branch == source_branch or cand_id == new_fact_id:
                    continue
                if cand_id in linked_targets:
                    continue
                if not cand_label:
                    continue
                filtered.append((cand_id, cand_label))

            if filtered:
                # Batch embed all candidate labels at once (much faster than N individual calls)
                cand_labels = [lbl for _, lbl in filtered]
                cand_embeddings = embed_service.embed_batch(
                    cand_labels, prefix="search_document"
                )

                for (cand_id, _cand_label), cand_embedding in zip(
                    filtered, cand_embeddings
                ):
                    sim = _cosine_similarity(new_embedding, cand_embedding)

                    if sim >= _EMBEDDING_SIMILARITY_THRESHOLD:
                        was_created = kg.add_edge(
                            source_id=new_fact_id,
                            target_id=cand_id,
                            relation="cross_branch_semantic",
                            confidence=round(sim, 3),
                            source_record=record_id,
                        )
                        if was_created:
                            edges_created += 1
                            linked_targets.add(cand_id)
        except (RuntimeError, OSError, ValueError, ImportError) as exc:
            logger.debug("Embedding-based cross-branch matching failed: %s", exc)

    return edges_created


def _extract_branch(node_id: str) -> str | None:
    """Extract the branch prefix from a node_id.

    Examples:
        'family.member.dad' -> 'family'
        'ops.schedule.monday' -> 'ops'
        'ingest:abc123' -> 'ingest'
        'preference.color' -> 'preference'
    """
    if not node_id:
        return None

    # Handle colon-separated IDs (e.g., 'ingest:abc')
    if ":" in node_id:
        return node_id.split(":")[0]

    # Handle dot-separated IDs (e.g., 'family.member.dad')
    if "." in node_id:
        return node_id.split(".")[0]

    return node_id


def _extract_keywords(label: str) -> list[str]:
    """Extract meaningful keywords from a label for cross-branch matching.

    Filters out short words and common stop words, deduplicates.
    """
    return _extract_keywords_core(
        label,
        stop_words=_STOP_WORDS,
        min_length=_MIN_KEYWORD_LEN,
        pattern=r"[a-zA-Z]+",
        deduplicate=True,
    )
