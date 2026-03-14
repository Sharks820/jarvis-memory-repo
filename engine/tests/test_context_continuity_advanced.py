"""Tests for CTX-03, CTX-04, CTX-05 — advanced context continuity features.

CTX-03: Mission artifacts in shared state
CTX-04: Transport-limit-aware compaction
CTX-05: Cross-provider output normalization into unified timeline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine.memory.conversation_state import (
    ConversationStateManager,
    normalize_provider_name,
)


@pytest.fixture()
def csm(tmp_path: Path) -> ConversationStateManager:
    """Create a fresh ConversationStateManager with temp storage."""
    return ConversationStateManager(
        state_dir=tmp_path / "state",
        db_path=tmp_path / "timeline.db",
        encryption_key=None,
    )


# ---------------------------------------------------------------------------
# CTX-03: Mission and artifact tracking in shared state
# ---------------------------------------------------------------------------


class TestCTX03MissionTracking:
    """CTX-03: Shared conversation state includes mission status and artifacts."""

    def test_mission_tracking_in_state(self, csm: ConversationStateManager) -> None:
        """track_mission adds to active_mission_ids."""
        csm.track_mission("mission-001")
        csm.track_mission("mission-002")

        missions = csm.get_active_missions()
        assert "mission-001" in missions
        assert "mission-002" in missions
        assert len(missions) == 2

    def test_mission_tracking_deduplicates(self, csm: ConversationStateManager) -> None:
        """Tracking the same mission twice does not create duplicates."""
        csm.track_mission("mission-001")
        csm.track_mission("mission-001")

        missions = csm.get_active_missions()
        assert missions.count("mission-001") == 1

    def test_mission_tracking_ignores_empty(self, csm: ConversationStateManager) -> None:
        """Empty or whitespace-only mission IDs are ignored."""
        csm.track_mission("")
        csm.track_mission("   ")
        csm.track_mission("valid-id")

        assert csm.get_active_missions() == ["valid-id"]

    def test_artifact_tracking_in_state(self, csm: ConversationStateManager) -> None:
        """track_artifact adds to referenced_artifacts."""
        csm.track_artifact("https://example.com/doc.pdf")
        csm.track_artifact("/home/user/notes.txt")

        snap = csm.snapshot
        assert "https://example.com/doc.pdf" in snap.referenced_artifacts
        assert "/home/user/notes.txt" in snap.referenced_artifacts

    def test_artifact_tracking_deduplicates(self, csm: ConversationStateManager) -> None:
        """Tracking the same artifact twice does not create duplicates."""
        csm.track_artifact("https://example.com/doc.pdf")
        csm.track_artifact("https://example.com/doc.pdf")

        snap = csm.snapshot
        assert snap.referenced_artifacts.count("https://example.com/doc.pdf") == 1

    def test_mission_injected_into_prompt(self, csm: ConversationStateManager) -> None:
        """get_prompt_injection includes active mission IDs."""
        csm.track_mission("learn-python-basics")
        csm.track_mission("research-ai-safety")

        injection = csm.get_prompt_injection()
        assert "active_mission_ids" in injection
        assert "learn-python-basics" in injection["active_mission_ids"]
        assert "research-ai-safety" in injection["active_mission_ids"]

    def test_artifact_injected_into_prompt(self, csm: ConversationStateManager) -> None:
        """get_prompt_injection includes referenced artifacts."""
        csm.track_artifact("https://arxiv.org/paper123")
        csm.track_artifact("C:\\Users\\data\\report.csv")

        injection = csm.get_prompt_injection()
        assert "referenced_artifacts" in injection
        assert "https://arxiv.org/paper123" in injection["referenced_artifacts"]
        assert "C:\\Users\\data\\report.csv" in injection["referenced_artifacts"]


# ---------------------------------------------------------------------------
# CTX-04: Transport-limit-aware compaction
# ---------------------------------------------------------------------------


class TestCTX04TransportCompaction:
    """CTX-04: Context compaction retains key facts under transport limits."""

    def test_compact_fits_under_limit(self, csm: ConversationStateManager) -> None:
        """compact_for_transport returns data under max_chars."""
        # Fill state with substantial data
        for i in range(50):
            csm.track_mission(f"mission-{i:04d}")
            csm.track_artifact(f"https://example.com/artifact-{i:04d}.pdf")

        with csm._lock:
            csm._snapshot.rolling_summary = "A" * 2000
            csm._snapshot.anchor_entities = {f"Entity_{i}" for i in range(100)}
            csm._snapshot.unresolved_goals = [f"Goal {i}: do something important" for i in range(30)]
            csm._snapshot.prior_decisions = [f"Decision {i}: we chose X" for i in range(30)]

        max_chars = 2000
        compacted = csm.compact_for_transport(max_chars=max_chars)
        serialized = json.dumps(compacted, ensure_ascii=False)
        assert len(serialized) <= max_chars

    def test_compact_preserves_entities_first(self, csm: ConversationStateManager) -> None:
        """Entities survive compaction even when other data is trimmed."""
        with csm._lock:
            csm._snapshot.anchor_entities = {"Alice", "Bob", "Project_X"}
            csm._snapshot.rolling_summary = "S" * 5000
            csm._snapshot.unresolved_goals = [f"Goal {i}" for i in range(50)]
            csm._snapshot.prior_decisions = [f"Decision {i}" for i in range(50)]

        # Use a limit that forces heavy trimming but entities should survive
        compacted = csm.compact_for_transport(max_chars=500)
        assert len(compacted.get("anchor_entities", [])) > 0

        # Summary should be trimmed more aggressively than entities
        summary_len = len(compacted.get("rolling_summary", ""))
        assert summary_len < 5000  # must have been trimmed

    def test_compact_truncates_summary_last(self, csm: ConversationStateManager) -> None:
        """rolling_summary is the first thing to be trimmed (lowest priority text)."""
        with csm._lock:
            csm._snapshot.anchor_entities = {"Alice"}
            csm._snapshot.rolling_summary = "X" * 3000
            csm._snapshot.unresolved_goals = ["Fix the bug"]
            csm._snapshot.prior_decisions = ["We chose Python"]

        # Generous limit — everything should fit except maybe summary
        compacted_large = csm.compact_for_transport(max_chars=4000)
        # Should be under limit
        assert len(json.dumps(compacted_large, ensure_ascii=False)) <= 4000

        # Tight limit — summary must be heavily trimmed but structured data preserved
        compacted_tight = csm.compact_for_transport(max_chars=500)
        assert len(json.dumps(compacted_tight, ensure_ascii=False)) <= 500
        # Summary should be the most trimmed field
        tight_summary_len = len(compacted_tight.get("rolling_summary", ""))
        assert tight_summary_len < 3000

    def test_compact_returns_full_data_when_under_limit(
        self, csm: ConversationStateManager
    ) -> None:
        """When data already fits, compact_for_transport returns everything."""
        with csm._lock:
            csm._snapshot.anchor_entities = {"Alice"}
            csm._snapshot.rolling_summary = "Short summary"
            csm._snapshot.unresolved_goals = ["One goal"]
            csm._snapshot.prior_decisions = ["One decision"]

        compacted = csm.compact_for_transport(max_chars=8000)
        assert compacted["rolling_summary"] == "Short summary"
        assert compacted["anchor_entities"] == ["Alice"]
        assert compacted["unresolved_goals"] == ["One goal"]
        assert compacted["prior_decisions"] == ["One decision"]


# ---------------------------------------------------------------------------
# CTX-05: Cross-provider output normalization
# ---------------------------------------------------------------------------


class TestCTX05ProviderNormalization:
    """CTX-05: Cross-provider outputs normalized into one durable timeline."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("claude-3-opus-20240229", "claude"),
            ("claude-3-sonnet-20240229", "claude"),
            ("claude", "claude"),
            ("gpt-4-turbo", "openai"),
            ("gpt-3.5-turbo", "openai"),
            ("o1-preview", "openai"),
            ("qwen3.5:latest", "qwen"),
            ("qwen2-72b", "qwen"),
            ("gemini-1.5-pro", "gemini"),
            ("gemma-7b", "gemma"),
            ("llama-3-70b", "llama"),
            ("mistral-large", "mistral"),
            ("mixtral-8x7b", "mixtral"),
            ("deepseek-coder-v2", "deepseek"),
            ("phi-3-mini", "phi"),
            ("command-r-plus", "cohere"),
            ("whisper-large-v3", "whisper"),
            ("", "unknown"),
            ("some-random-model", "some-random-model"),
        ],
    )
    def test_provider_name_normalization(self, raw: str, expected: str) -> None:
        """normalize_provider_name maps raw model names to canonical providers."""
        assert normalize_provider_name(raw) == expected

    def test_unified_timeline_includes_all_providers(
        self, csm: ConversationStateManager
    ) -> None:
        """Timeline entries from multiple providers all appear in unified timeline."""
        csm.update_turn("user", "Hello from user", "claude-3-opus-20240229")
        csm.update_turn("assistant", "Hi there!", "claude-3-opus-20240229")
        csm.update_turn("user", "Switch provider", "gpt-4-turbo")
        csm.update_turn("assistant", "I'm GPT now", "gpt-4-turbo")
        csm.update_turn("user", "Local query", "qwen3.5:latest")
        csm.update_turn("assistant", "Local response", "qwen3.5:latest")

        timeline = csm.get_unified_timeline(limit=20)
        assert len(timeline) == 6

        providers = {e["normalized_provider"] for e in timeline}
        assert "claude" in providers
        assert "openai" in providers
        assert "qwen" in providers

    def test_timeline_entries_have_normalized_names(
        self, csm: ConversationStateManager
    ) -> None:
        """No raw model names appear in the normalized_provider field."""
        raw_models = [
            "claude-3-opus-20240229",
            "gpt-4-turbo",
            "qwen3.5:latest",
            "llama-3-70b",
        ]
        for model in raw_models:
            csm.update_turn("user", f"msg for {model}", model)

        timeline = csm.get_unified_timeline(limit=20)
        for entry in timeline:
            # normalized_provider should never contain version numbers or colons
            provider = entry["normalized_provider"]
            assert ":" not in provider, f"Raw model leaked: {provider}"
            assert not any(
                c.isdigit() and i > 0
                for i, c in enumerate(provider)
            ) or provider in ("o1",), f"Version number in provider: {provider}"

    def test_timeline_entry_structure(self, csm: ConversationStateManager) -> None:
        """Each unified timeline entry has the required fields."""
        csm.update_turn("user", "Test message", "claude-3-opus")

        timeline = csm.get_unified_timeline(limit=5)
        assert len(timeline) == 1

        entry = timeline[0]
        assert "timestamp" in entry
        assert "normalized_provider" in entry
        assert "role" in entry
        assert "content_hash" in entry
        assert "summary_snippet" in entry
        assert entry["normalized_provider"] == "claude"
        assert entry["role"] == "user"
        assert "Test message" in entry["summary_snippet"]
