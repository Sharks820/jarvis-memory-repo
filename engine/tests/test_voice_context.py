"""Tests for voice_context.py — context building and system prompt assembly.

Tests cover _current_datetime_prompt_line, _build_smart_context (hybrid and
legacy paths, KG facts, cross-branch, preferences), and _build_system_parts.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.learning.preferences import PreferenceTracker
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.voice_context import (
    _build_smart_context,
    _build_system_parts,
    _current_datetime_prompt_line,
)

# Patch targets:
#   _build_smart_context lazily does `import jarvis_engine.voice_pipeline as _vp`
#   then reads _vp.repo_root and _vp.build_context_packet.
#   We patch the voice_pipeline module attributes directly.
_VP = "jarvis_engine.voice_pipeline"


# ===========================================================================
# _current_datetime_prompt_line
# ===========================================================================


class TestCurrentDatetimePromptLine:
    def test_returns_string(self) -> None:
        result = _current_datetime_prompt_line()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_date_info(self) -> None:
        """Should contain some date/time indication."""
        result = _current_datetime_prompt_line()
        assert len(result) > 5


# ===========================================================================
# _build_smart_context
# ===========================================================================


def _make_bus(
    *,
    engine=None,
    embed_service=None,
    kg=None,
    pref_tracker=None,
) -> MagicMock:
    bus = MagicMock()
    bus.ctx.engine = engine
    bus.ctx.embed_service = embed_service
    bus.ctx.kg = kg
    bus.ctx.pref_tracker = pref_tracker
    return bus


class TestBuildSmartContext:
    def test_returns_four_lists(self, tmp_path: Path) -> None:
        """Return value is a 4-tuple of lists."""
        bus = _make_bus()
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch(f"{_VP}.build_context_packet", return_value={"selected": []}):
            result = _build_smart_context(bus, "hello")
        assert isinstance(result, tuple)
        assert len(result) == 4
        for item in result:
            assert isinstance(item, list)

    def test_hybrid_search_path(self, tmp_path: Path) -> None:
        """When engine and embed_service are available, hybrid search is used."""
        engine = MagicMock(spec=MemoryEngine)
        embed_service = MagicMock(spec=EmbeddingService)
        embed_service.embed_query.return_value = [0.1] * 128

        bus = _make_bus(engine=engine, embed_service=embed_service)

        mock_results = [
            {"summary": "Memory about Python"},
            {"summary": "Memory about AI"},
        ]

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.memory.search.hybrid_search", return_value=mock_results):
            memory_lines, _, _, _ = _build_smart_context(bus, "tell me about Python")

        assert len(memory_lines) == 2
        assert "Memory about Python" in memory_lines

    def test_legacy_fallback_when_hybrid_empty(self, tmp_path: Path) -> None:
        """When hybrid search returns nothing, legacy build_context_packet is used."""
        bus = _make_bus()

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch(f"{_VP}.build_context_packet", return_value={
                 "selected": [
                     {"summary": "Legacy memory 1"},
                     {"summary": "Legacy memory 2"},
                 ]
             }):
            memory_lines, _, _, _ = _build_smart_context(bus, "hello")

        assert len(memory_lines) == 2
        assert "Legacy memory 1" in memory_lines

    def test_legacy_fallback_empty_selected(self, tmp_path: Path) -> None:
        """Legacy path with empty selected list produces no memory lines."""
        bus = _make_bus()

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch(f"{_VP}.build_context_packet", return_value={"selected": []}):
            memory_lines, _, _, _ = _build_smart_context(bus, "hello")

        assert memory_lines == []

    def test_kg_facts_extracted(self, tmp_path: Path) -> None:
        """KG facts with confidence >= 0.5 are included."""
        engine = MagicMock(spec=MemoryEngine)
        kg = MagicMock(spec=KnowledgeGraph)
        kg.query_relevant_facts.return_value = [
            {"label": "User likes Python", "confidence": 0.8, "node_id": "n1"},
            {"label": "Low confidence fact", "confidence": 0.3, "node_id": "n2"},
        ]
        kg.query_relevant_facts_semantic.return_value = []

        embed_service = MagicMock(spec=EmbeddingService)
        embed_service.embed_query.return_value = [0.1] * 128

        bus = _make_bus(engine=engine, kg=kg, embed_service=embed_service)

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.memory.search.hybrid_search", return_value=[]):
            _, fact_lines, _, _ = _build_smart_context(bus, "tell me about coding")

        assert "User likes Python" in fact_lines
        assert "Low confidence fact" not in fact_lines

    def test_preferences_included(self, tmp_path: Path) -> None:
        """User preferences from pref_tracker are returned."""
        pref_tracker = MagicMock(spec=PreferenceTracker)
        pref_tracker.get_preferences.return_value = {
            "tone": "casual",
            "detail": "concise",
        }
        bus = _make_bus(pref_tracker=pref_tracker)

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch(f"{_VP}.build_context_packet", return_value={"selected": []}):
            _, _, _, pref_lines = _build_smart_context(bus, "hello")

        assert len(pref_lines) == 1
        assert "tone" in pref_lines[0]
        assert "casual" in pref_lines[0]

    def test_preferences_empty_when_no_tracker(self, tmp_path: Path) -> None:
        bus = _make_bus(pref_tracker=None)

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch(f"{_VP}.build_context_packet", return_value={"selected": []}):
            _, _, _, pref_lines = _build_smart_context(bus, "hello")

        assert pref_lines == []

    def test_cross_branch_connections(self, tmp_path: Path) -> None:
        """Cross-branch connections are formatted correctly."""
        engine = MagicMock(spec=MemoryEngine)
        kg = MagicMock(spec=KnowledgeGraph)
        kg.query_relevant_facts.return_value = []
        kg.query_relevant_facts_semantic.return_value = []
        embed_service = MagicMock(spec=EmbeddingService)
        embed_service.embed_query.return_value = [0.1] * 128

        bus = _make_bus(engine=engine, kg=kg, embed_service=embed_service)

        mock_cb_result = {
            "cross_branch_connections": [
                {
                    "source": "Python",
                    "target": "automation",
                    "source_branch": "work",
                    "target_branch": "hobby",
                    "relation": "skill_transfer",
                },
            ]
        }

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.memory.search.hybrid_search", return_value=[]), \
             patch("jarvis_engine.learning.cross_branch.cross_branch_query", return_value=mock_cb_result):
            _, _, cb_lines, _ = _build_smart_context(bus, "skills")

        assert len(cb_lines) == 1
        assert "[work]" in cb_lines[0]
        assert "[hobby]" in cb_lines[0]
        assert "skill_transfer" in cb_lines[0]

    def test_hybrid_search_error_falls_back(self, tmp_path: Path) -> None:
        """When hybrid_search raises, falls back to legacy."""
        engine = MagicMock(spec=MemoryEngine)
        embed_service = MagicMock(spec=EmbeddingService)
        embed_service.embed_query.return_value = [0.1] * 128

        bus = _make_bus(engine=engine, embed_service=embed_service)

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.memory.search.hybrid_search", side_effect=RuntimeError("fail")), \
             patch(f"{_VP}.build_context_packet", return_value={
                 "selected": [{"summary": "Fallback memory"}]
             }):
            memory_lines, _, _, _ = _build_smart_context(bus, "test")

        assert "Fallback memory" in memory_lines

    def test_empty_summaries_filtered(self, tmp_path: Path) -> None:
        """Empty or whitespace-only summaries are excluded."""
        engine = MagicMock(spec=MemoryEngine)
        embed_service = MagicMock(spec=EmbeddingService)
        embed_service.embed_query.return_value = [0.1] * 128

        bus = _make_bus(engine=engine, embed_service=embed_service)

        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.memory.search.hybrid_search", return_value=[
                 {"summary": ""},
                 {"summary": "  "},
                 {"summary": "Valid memory"},
             ]):
            memory_lines, _, _, _ = _build_smart_context(bus, "test")

        assert memory_lines == ["Valid memory"]


# ===========================================================================
# _build_system_parts
# ===========================================================================


class TestBuildSystemParts:
    def test_returns_list_of_strings(self, tmp_path: Path) -> None:
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="Be helpful."):
            result = _build_system_parts([], [], [], [])

        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_includes_datetime_and_persona(self, tmp_path: Path) -> None:
        """System parts always include datetime and persona prompt."""
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="I am Jarvis."):
            result = _build_system_parts([], [], [], [])

        # At minimum: datetime line + persona prompt
        assert len(result) >= 2

    def test_includes_facts_section(self, tmp_path: Path) -> None:
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="Persona"):
            result = _build_system_parts([], ["User likes coffee"], [], [])

        facts_part = [p for p in result if "Known facts" in p]
        assert len(facts_part) == 1
        assert "User likes coffee" in facts_part[0]

    def test_includes_memories_section(self, tmp_path: Path) -> None:
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="Persona"):
            result = _build_system_parts(["Recent coding session"], [], [], [])

        mem_part = [p for p in result if "Relevant memories" in p]
        assert len(mem_part) == 1
        assert "Recent coding session" in mem_part[0]

    def test_includes_cross_domain_section(self, tmp_path: Path) -> None:
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="Persona"):
            result = _build_system_parts([], [], ["work -> hobby connection"], [])

        cb_part = [p for p in result if "Cross-domain" in p]
        assert len(cb_part) == 1

    def test_includes_preferences_section(self, tmp_path: Path) -> None:
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="Persona"):
            result = _build_system_parts([], [], [], ["tone: casual"])

        pref_part = [p for p in result if "User preferences" in p]
        assert len(pref_part) == 1
        assert "tone: casual" in pref_part[0]

    def test_empty_sections_excluded(self, tmp_path: Path) -> None:
        """Empty lists produce no extra sections beyond datetime+persona."""
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="P"):
            result = _build_system_parts([], [], [], [])

        # Only datetime + persona
        assert len(result) == 2

    def test_fact_lines_limited_to_six(self, tmp_path: Path) -> None:
        """At most 6 fact lines are included."""
        facts = [f"Fact {i}" for i in range(10)]
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="P"):
            result = _build_system_parts([], facts, [], [])

        facts_part = [p for p in result if "Known facts" in p][0]
        bullet_count = facts_part.count("- Fact")
        assert bullet_count == 6

    def test_memory_lines_limited_to_eight(self, tmp_path: Path) -> None:
        """At most 8 memory lines are included."""
        memories = [f"Memory {i}" for i in range(12)]
        with patch(f"{_VP}.repo_root", return_value=tmp_path), \
             patch("jarvis_engine.voice_context.load_persona_config", return_value={}), \
             patch("jarvis_engine.persona.get_persona_prompt", return_value="P"):
            result = _build_system_parts(memories, [], [], [])

        mem_part = [p for p in result if "Relevant memories" in p][0]
        bullet_count = mem_part.count("- Memory")
        assert bullet_count == 8
