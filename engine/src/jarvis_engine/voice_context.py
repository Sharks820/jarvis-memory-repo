"""Voice pipeline context-building helpers — smart context and system prompt assembly.

Split from voice_pipeline.py for separation of concerns.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from jarvis_engine.persona import load_persona_config

from jarvis_engine._constants import (
    STOP_WORDS as _HARVEST_STOP_WORDS,
)

if TYPE_CHECKING:
    from jarvis_engine._bus import CommandBus

logger = logging.getLogger(__name__)


def _current_datetime_prompt_line() -> str:
    """Provide deterministic current date/time context for model grounding."""
    from jarvis_engine.temporal import get_datetime_prompt

    return get_datetime_prompt()


def _build_smart_context(
    bus: "CommandBus",
    query: str,
    *,
    max_memory_items: int = 20,
    max_fact_items: int = 15,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Build context using best available retrieval method.

    Returns (memory_lines, fact_lines, cross_branch_lines, preference_lines)
    for system prompt injection.  Uses hybrid search (FTS5 + embeddings + RRF)
    when MemoryEngine is available, falls back to legacy token-overlap otherwise.
    """
    # Look up repo_root through voice_pipeline so tests that monkeypatch
    # voice_pipeline_mod.repo_root see the override in this module too.
    import jarvis_engine.voice_pipeline as _vp
    repo_root = _vp.repo_root

    memory_lines: list[str] = []
    fact_lines: list[str] = []
    cross_branch_lines: list[str] = []

    engine = bus.ctx.engine
    embed_service = bus.ctx.embed_service

    # --- Path 1: Hybrid search (superior) ---
    if engine is not None and embed_service is not None:
        try:
            from jarvis_engine.memory.search import hybrid_search

            query_embedding = embed_service.embed_query(query)
            results = hybrid_search(
                engine, query, query_embedding, k=max_memory_items
            )
            for record in results:
                summary = str(record.get("summary", "")).strip()
                if summary:
                    memory_lines.append(summary)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.debug("Hybrid search failed, falling back to legacy: %s", exc)

    # --- Path 2: Legacy token-overlap fallback ---
    if not memory_lines:
        try:
            # Look up build_context_packet through voice_pipeline so tests
            # that monkeypatch voice_pipeline_mod.build_context_packet see it.
            _build_context = _vp.build_context_packet
            packet = _build_context(
                repo_root(), query=query, max_items=max_memory_items, max_chars=1800
            )
            selected = packet.get("selected", [])
            if isinstance(selected, list):
                for row in selected:
                    if isinstance(row, dict):
                        summary = str(row.get("summary", "")).strip()
                        if summary:
                            memory_lines.append(summary)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Legacy context packet fallback failed: %s", exc)

    # --- KG facts: personal knowledge about the user ---
    kg = None  # Retain reference for cross-branch query below
    if engine is not None:
        try:
            kg = bus.ctx.kg
            if kg is None:
                from jarvis_engine.knowledge.graph import KnowledgeGraph
                kg = KnowledgeGraph(engine)
            # Extract keywords from query for fact lookup
            words = [
                w for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
                if w not in _HARVEST_STOP_WORDS
            ][:10]
            if words:
                facts = kg.query_relevant_facts(words, limit=max_fact_items)
                seen_node_ids: dict[str, float] = {}
                for fact in facts:
                    label = str(fact.get("label", "")).strip()
                    conf = fact.get("confidence", 0.0)
                    nid = fact.get("node_id", "")
                    if label and conf >= 0.5:
                        fact_lines.append(label)
                        if nid:
                            seen_node_ids[nid] = conf

                # Semantic KG search (embedding-based) — complements keyword FTS5
                if embed_service is not None:
                    try:
                        sem_facts = kg.query_relevant_facts_semantic(
                            query, embed_service=embed_service,
                            limit=max_fact_items, min_confidence=0.5,
                        )
                        for fact in sem_facts:
                            nid = fact.get("node_id", "")
                            label = str(fact.get("label", "")).strip()
                            conf = fact.get("confidence", 0.0)
                            if not label or conf < 0.5:
                                continue
                            # Deduplicate: skip if already seen with equal/higher confidence
                            if nid and nid in seen_node_ids and seen_node_ids[nid] >= conf:
                                continue
                            fact_lines.append(label)
                            if nid:
                                seen_node_ids[nid] = conf
                    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as sem_exc:
                        logger.debug("KG semantic fact query failed: %s", sem_exc)
        except (ImportError, OSError, RuntimeError, KeyError) as exc:
            logger.debug("KG fact query failed: %s", exc)

    # --- Cross-branch connections: link knowledge across life domains ---
    if kg is not None and engine is not None and embed_service is not None:
        try:
            from jarvis_engine.learning.cross_branch import cross_branch_query

            cb_result = cross_branch_query(
                query=query,
                engine=engine,
                kg=kg,
                embed_service=embed_service,
                k=6,
            )
            for conn in cb_result.get("cross_branch_connections", []):
                src = conn.get("source", "")
                tgt = conn.get("target", "")
                src_branch = conn.get("source_branch", "unknown")
                tgt_branch = conn.get("target_branch", "unknown")
                relation = conn.get("relation", "related")
                cross_branch_lines.append(
                    f"[{src_branch}] \"{src}\" relates to [{tgt_branch}] \"{tgt}\" via {relation}"
                )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.debug("Cross-branch query failed: %s", exc)

    # --- User preferences: personalize responses (LEARN-01) ---
    preference_lines: list[str] = []
    pref_tracker = bus.ctx.pref_tracker
    if pref_tracker is not None:
        try:
            prefs = pref_tracker.get_preferences()
            if prefs:
                pref_str = ", ".join(f"{k}: {v}" for k, v in prefs.items())
                preference_lines.append(pref_str)
        except (OSError, RuntimeError, KeyError) as exc:
            logger.debug("Preference retrieval failed: %s", exc)

    return memory_lines, fact_lines, cross_branch_lines, preference_lines


def _build_system_parts(
    memory_lines: list[str],
    fact_lines: list[str],
    cross_branch_lines: list[str],
    preference_lines: list[str],
) -> list[str]:
    """Assemble the system prompt parts for an LLM conversation.

    Called from ``_web_augmented_llm_conversation`` to assemble
    the LLM system prompt.
    """
    from jarvis_engine.persona import get_persona_prompt
    import jarvis_engine.voice_pipeline as _vp
    repo_root = _vp.repo_root
    persona = load_persona_config(repo_root())
    parts = [_current_datetime_prompt_line(), get_persona_prompt(persona)]
    if fact_lines:
        parts.append(
            "Known facts about the user (use these to personalize your response):\n"
            + "\n".join(f"- {line}" for line in fact_lines[:6])
        )
    if memory_lines:
        parts.append(
            "Relevant memories (recent interactions and context):\n"
            + "\n".join(f"- {line}" for line in memory_lines[:8])
        )
    if cross_branch_lines:
        parts.append(
            "Cross-domain connections:\n"
            + "\n".join(f"- {line}" for line in cross_branch_lines[:6])
        )
    if preference_lines:
        parts.append(
            "User preferences (adjust your response style accordingly): "
            + "; ".join(preference_lines)
        )
    return parts
