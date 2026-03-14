"""Tests for CTX-01, CTX-02, CTX-06 — context continuity across provider switches.

CTX-01: Provider switch (local LLM <-> cloud LLM <-> CLI) preserves active
        conversation context.
CTX-02: Auto-assign routing keeps full task context and intent metadata across
        route changes.
CTX-06: Provider fallback chain never restarts the task from scratch unless
        explicitly requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.memory.conversation_state import (
    ConversationStateManager,
)
from jarvis_engine.voice.pipeline import ConversationState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_state_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for conversation state persistence."""
    d = tmp_path / "runtime"
    d.mkdir()
    return d


@pytest.fixture()
def csm(tmp_state_dir: Path) -> ConversationStateManager:
    """Return a fresh ConversationStateManager with no encryption."""
    return ConversationStateManager(
        state_dir=tmp_state_dir,
        db_path=tmp_state_dir / "timeline.db",
        encryption_key=None,
    )


@pytest.fixture()
def conv_state(tmp_path: Path) -> ConversationState:
    """Return a fresh voice-pipeline ConversationState."""
    cs = ConversationState(history_file=tmp_path / "history.json")
    # Mark as loaded so get_history_messages() doesn't reload from the
    # (non-existent) file and discard in-memory additions.
    cs._conversation_history_loaded = True
    return cs


# ========================================================================
# CTX-01: Provider switch preserves active conversation context
# ========================================================================


class TestCTX01ProviderSwitchPreservesContext:
    """CTX-01: switching between local/cloud/CLI preserves entities, goals,
    decisions, rolling summary, and continuity instructions."""

    def test_provider_switch_preserves_entities(
        self, csm: ConversationStateManager
    ) -> None:
        """After a model switch, get_prompt_injection() still returns
        entities, goals, and decisions accumulated before the switch."""
        # Simulate a few turns on model A that produce entities/goals/decisions
        csm.update_turn(
            "user",
            "Let's deploy the Jarvis Dashboard to https://jarvis.example.com by March 20, 2026",
            "qwen3.5:latest",
        )
        csm.update_turn(
            "assistant",
            "I'll set up the deployment pipeline for Jarvis Dashboard. "
            "We still need to configure the SSL certificates.",
            "qwen3.5:latest",
        )

        # Record a model switch (local -> cloud)
        csm.mark_model_switch("qwen3.5:latest", "claude-sonnet", reason="fallback")

        # Simulate a turn on the new model
        csm.update_turn("user", "What was our deployment plan?", "claude-sonnet")

        injection = csm.get_prompt_injection()

        # Entities from before the switch must still be present
        entities = injection["anchor_entities"]
        assert any("jarvis.example.com" in e for e in entities), (
            f"URL entity lost after model switch: {entities}"
        )

        # Decisions from the assistant turn must survive
        decisions = injection["prior_decisions"]
        assert any("deployment pipeline" in d.lower() for d in decisions), (
            f"Decision lost after model switch: {decisions}"
        )

        # Unresolved goals must survive
        goals = injection["unresolved_goals"]
        assert any("ssl" in g.lower() or "certificates" in g.lower() for g in goals), (
            f"Unresolved goal lost after model switch: {goals}"
        )

    def test_provider_switch_injects_continuity_instruction(
        self, conv_state: ConversationState
    ) -> None:
        """conversation_continuity_instruction() returns non-None when the
        model changes between turns."""
        conv_state.mark_routed_model("qwen3.5:latest", "ollama")
        conv_state.add_to_history("user", "Hello")
        conv_state.add_to_history("assistant", "Hi there")

        # Switch to a different model
        instruction = conv_state.conversation_continuity_instruction(
            "claude-sonnet", history_len=2
        )

        assert instruction is not None
        assert "qwen3.5:latest" in instruction
        assert "claude-sonnet" in instruction
        assert "continue" in instruction.lower() or "do not reset" in instruction.lower()

    def test_continuity_instruction_none_when_same_model(
        self, conv_state: ConversationState
    ) -> None:
        """No continuity instruction when model stays the same."""
        conv_state.mark_routed_model("qwen3.5:latest", "ollama")

        instruction = conv_state.conversation_continuity_instruction(
            "qwen3.5:latest", history_len=5
        )
        assert instruction is None

    def test_continuity_instruction_none_when_no_history(
        self, conv_state: ConversationState
    ) -> None:
        """No continuity instruction when conversation history is empty."""
        conv_state.mark_routed_model("qwen3.5:latest", "ollama")

        instruction = conv_state.conversation_continuity_instruction(
            "claude-sonnet", history_len=0
        )
        assert instruction is None

    def test_rolling_summary_survives_model_switch(
        self, csm: ConversationStateManager
    ) -> None:
        """Checkpoint + rolling summary persists across model changes."""
        # Create a checkpoint with dropped messages (simulating history eviction)
        dropped = [
            {"role": "user", "content": "Tell me about quantum computing"},
            {"role": "assistant", "content": "Quantum computing uses qubits..."},
        ]
        checkpoint_id = csm.create_checkpoint(dropped_messages=dropped)
        assert checkpoint_id >= 1

        # Verify rolling summary exists
        injection_before = csm.get_prompt_injection()
        assert injection_before["rolling_summary"], "Rolling summary should be non-empty"
        assert "quantum" in injection_before["rolling_summary"].lower()

        # Now switch models
        csm.mark_model_switch("qwen3.5:latest", "claude-sonnet", reason="user_request")

        # Rolling summary must still be present after model switch
        injection_after = csm.get_prompt_injection()
        assert injection_after["rolling_summary"] == injection_before["rolling_summary"], (
            "Rolling summary changed after model switch"
        )

    def test_local_to_cloud_switch_preserves_context(
        self, conv_state: ConversationState
    ) -> None:
        """Simulate local -> cloud switch, verify full conversation history
        is still accessible after the switch."""
        # Build up history on local model
        conv_state.mark_routed_model("qwen3.5:latest", "ollama")
        conv_state.add_to_history("user", "What is the weather today?")
        conv_state.add_to_history("assistant", "It is sunny and 72F.")
        conv_state.add_to_history("user", "Should I bring an umbrella?")
        conv_state.add_to_history("assistant", "No, you should be fine without one.")

        history_before = conv_state.get_history_messages()
        assert len(history_before) == 4

        # Switch to cloud model
        conv_state.mark_routed_model("claude-sonnet", "anthropic")

        # History must be unchanged
        history_after = conv_state.get_history_messages()
        assert history_after == history_before, (
            "Conversation history changed after provider switch"
        )

        # Continuity instruction should now fire for the new model
        instruction = conv_state.conversation_continuity_instruction(
            "claude-sonnet", history_len=len(history_after)
        )
        # After mark_routed_model the _last_routed_model is now claude-sonnet,
        # so asking for claude-sonnet should return None (same model).
        # But asking for a THIRD model should return an instruction.
        instruction_third = conv_state.conversation_continuity_instruction(
            "gemini-cli", history_len=len(history_after)
        )
        assert instruction_third is not None


# ========================================================================
# CTX-02: Fallback chain carries messages through unchanged
# ========================================================================


class TestCTX02FallbackChainPreservesMessages:
    """CTX-02: auto-assign routing keeps full task context and intent
    metadata across route changes in the gateway fallback chain."""

    def test_fallback_chain_carries_messages_through(self) -> None:
        """Verify the messages list passed to _fallback_chain is the same
        object forwarded to each fallback provider call."""
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gw = ModelGateway.__new__(ModelGateway)
        # Minimal init for _fallback_chain
        gw._cloud_keys = {"groq": "fake-key"}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False

        original_messages = [
            {"role": "system", "content": "You are Jarvis."},
            {"role": "user", "content": "What is 2+2?"},
        ]

        captured_messages: list[list[dict[str, str]]] = []

        def fake_openai_compat(
            messages: list[dict[str, str]], *args: Any, **kwargs: Any
        ) -> GatewayResponse:
            # Capture the actual messages reference passed in
            captured_messages.append(messages)
            return GatewayResponse(
                text="4", model="llama-3.3-70b", provider="groq"
            )

        gw._call_openai_compat = fake_openai_compat  # type: ignore[assignment]
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        resp = gw._fallback_chain(
            original_messages, max_tokens=256, reason="test_fallback"
        )

        assert len(captured_messages) == 1
        # CTX-02: the exact same list object was passed through
        assert captured_messages[0] is original_messages
        assert resp.text == "4"
        assert resp.fallback_used is True

    def test_route_change_preserves_intent_metadata(self) -> None:
        """Verify task context (messages, system prompt) is not rebuilt on
        fallback -- the fallback response carries continuity_context."""
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gw = ModelGateway.__new__(ModelGateway)
        gw._cloud_keys = {"groq": "fake-key"}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False

        original_messages = [
            {"role": "system", "content": "You are Jarvis, a personal AI assistant."},
            {"role": "user", "content": "Summarize my last meeting notes."},
        ]

        def fake_openai_compat(
            messages: list[dict[str, str]], *args: Any, **kwargs: Any
        ) -> GatewayResponse:
            return GatewayResponse(
                text="Meeting summary: ...", model="llama-3.3-70b", provider="groq"
            )

        gw._call_openai_compat = fake_openai_compat  # type: ignore[assignment]
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        resp = gw._fallback_chain(
            original_messages,
            max_tokens=512,
            reason="anthropic_timeout",
            skip_provider="anthropic",
        )

        # CTX-02: _continuity_context must be populated
        ctx = resp._continuity_context
        assert ctx["original_model"] == "anthropic"
        assert ctx["fallback_model"] == "llama-3.3-70b"
        assert ctx["intent_preserved"] is True
        assert ctx["first_message_preserved"] is True

    def test_continuity_context_logs_to_activity_feed(self) -> None:
        """Verify that fallback route changes are logged to activity feed."""
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gw = ModelGateway.__new__(ModelGateway)
        gw._cloud_keys = {"groq": "fake-key"}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False

        def fake_openai_compat(
            messages: list[dict[str, str]], *args: Any, **kwargs: Any
        ) -> GatewayResponse:
            return GatewayResponse(
                text="ok", model="llama-3.3-70b", provider="groq"
            )

        gw._call_openai_compat = fake_openai_compat  # type: ignore[assignment]
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        messages = [{"role": "user", "content": "test"}]

        with patch("jarvis_engine.gateway.models._log_activity") as mock_log:
            resp = gw._fallback_chain(
                messages, max_tokens=256, reason="test", skip_provider="ollama"
            )

            # Find the fallback_route_change call
            route_change_calls = [
                c for c in mock_log.call_args_list
                if c[0][1].startswith("Fallback route change:")
            ]
            assert len(route_change_calls) >= 1, (
                f"Expected fallback_route_change activity log, got: {mock_log.call_args_list}"
            )
            logged_meta = route_change_calls[0][0][2]
            assert logged_meta["event"] == "fallback_route_change"
            assert logged_meta["intent_preserved"] is True


# ========================================================================
# CTX-06: Fallback never restarts from scratch
# ========================================================================


class TestCTX06FallbackNeverRestartsFromScratch:
    """CTX-06: provider fallback chain never restarts the task from scratch
    unless explicitly requested."""

    def test_fallback_never_restarts_from_scratch(self) -> None:
        """The first message in each fallback attempt is the same as the
        original -- the chain never rebuilds the messages list."""
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gw = ModelGateway.__new__(ModelGateway)
        gw._cloud_keys = {"groq": "fake-key", "mistral": "fake-key-2"}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False

        original_messages = [
            {"role": "system", "content": "You are Jarvis. Context: user is in a meeting."},
            {"role": "user", "content": "What did we discuss earlier?"},
            {"role": "assistant", "content": "You discussed the Q1 budget."},
            {"role": "user", "content": "Can you expand on that?"},
        ]

        first_messages_seen: list[dict[str, str]] = []
        call_count = 0

        def fake_openai_compat(
            messages: list[dict[str, str]], *args: Any, **kwargs: Any
        ) -> GatewayResponse:
            nonlocal call_count
            call_count += 1
            first_messages_seen.append(messages[0])
            if call_count == 1:
                # First provider (groq) fails
                raise RuntimeError("Groq timeout")
            # Second provider (mistral) succeeds
            return GatewayResponse(
                text="The Q1 budget was $1.2M...",
                model="devstral-2",
                provider="mistral",
            )

        gw._call_openai_compat = fake_openai_compat  # type: ignore[assignment]
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        resp = gw._fallback_chain(
            original_messages,
            max_tokens=512,
            reason="primary_failed",
            skip_provider="anthropic",
        )

        # CTX-06: Both attempts saw the same first message -- never restarted
        assert len(first_messages_seen) == 2
        assert first_messages_seen[0] is first_messages_seen[1]
        assert first_messages_seen[0] is original_messages[0]

        # The response carries continuity metadata
        assert resp._continuity_context["intent_preserved"] is True
        assert resp._continuity_context["first_message_preserved"] is True

    def test_fallback_all_fail_still_preserves_context(self) -> None:
        """Even when ALL providers fail, the error response still carries
        continuity_context confirming the chain did not restart."""
        from jarvis_engine.gateway.models import ModelGateway

        gw = ModelGateway.__new__(ModelGateway)
        gw._cloud_keys = {}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        messages = [
            {"role": "system", "content": "Context preserved."},
            {"role": "user", "content": "Hello"},
        ]

        # No providers available + skip_ollama = total failure
        resp = gw._fallback_chain(
            messages,
            max_tokens=256,
            reason="primary_crashed",
            skip_provider="ollama",
            skip_ollama=True,
        )

        assert resp.provider == "none"
        assert resp.fallback_used is True
        # CTX-06: continuity context still attached even on total failure
        assert resp._continuity_context["intent_preserved"] is True
        assert resp._continuity_context["first_message_preserved"] is True

    def test_explicit_restart_clears_context(
        self, csm: ConversationStateManager
    ) -> None:
        """Only an explicit reset command clears conversation state.
        Normal model switches must NOT clear state."""
        # Build up state
        csm.update_turn(
            "user",
            "I'll deploy the Jarvis Dashboard to production next week",
            "qwen3.5:latest",
        )
        csm.update_turn(
            "assistant",
            "We still need to run the integration tests before deployment.",
            "qwen3.5:latest",
        )

        injection_before = csm.get_prompt_injection()
        assert injection_before["unresolved_goals"], "Should have unresolved goals"

        # Model switch should NOT clear state
        csm.mark_model_switch("qwen3.5:latest", "claude-sonnet", reason="fallback")

        injection_after_switch = csm.get_prompt_injection()
        assert injection_after_switch["unresolved_goals"] == injection_before["unresolved_goals"]
        assert injection_after_switch["prior_decisions"] == injection_before["prior_decisions"]

        # Explicit reset DOES clear transient state (goals, summary)
        csm.reset()

        injection_after_reset = csm.get_prompt_injection()
        assert injection_after_reset["rolling_summary"] == ""
        assert injection_after_reset["unresolved_goals"] == []
        # Note: reset() preserves anchor_entities and prior_decisions by design
        # (long-term memory). But rolling_summary and unresolved_goals are cleared.

    def test_fallback_to_ollama_preserves_messages(self) -> None:
        """When the chain falls back all the way to Ollama, the same
        messages are passed through (not rebuilt)."""
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gw = ModelGateway.__new__(ModelGateway)
        gw._cloud_keys = {}
        gw._cli_providers = {}
        gw._anthropic = None
        gw._health = None
        gw._budget = None
        gw._http = MagicMock()
        gw._closed = False
        gw._refresh_cli_providers = lambda: None  # type: ignore[assignment]

        original_messages = [
            {"role": "system", "content": "System context here."},
            {"role": "user", "content": "Continue our conversation."},
        ]

        captured_messages: list[list[dict[str, str]]] = []

        def fake_fallback_to_ollama(
            messages: list[dict[str, str]], *args: Any, **kwargs: Any
        ) -> GatewayResponse:
            captured_messages.append(messages)
            return GatewayResponse(
                text="Continuing...",
                model="qwen3.5:latest",
                provider="ollama",
            )

        gw._fallback_to_ollama = fake_fallback_to_ollama  # type: ignore[assignment]

        resp = gw._fallback_chain(
            original_messages,
            max_tokens=256,
            reason="all_cloud_failed",
        )

        # The exact same messages object was passed to Ollama fallback
        assert len(captured_messages) == 1
        assert captured_messages[0] is original_messages
        assert resp._continuity_context["first_message_preserved"] is True


# ========================================================================
# Integration: ConversationStateManager model switch + prompt injection
# ========================================================================


class TestContextContinuityIntegration:
    """Cross-cutting integration tests combining CSM and voice pipeline."""

    def test_model_history_tracks_all_switches(
        self, csm: ConversationStateManager
    ) -> None:
        """model_history in the snapshot accumulates all provider switches."""
        csm.mark_model_switch("qwen3.5:latest", "claude-sonnet", reason="fallback")
        csm.mark_model_switch("claude-sonnet", "gemini-cli", reason="user_request")
        csm.mark_model_switch("gemini-cli", "qwen3.5:latest", reason="privacy")

        snapshot = csm.snapshot
        assert len(snapshot.model_history) == 3
        assert snapshot.model_history[0][0] == "claude-sonnet"
        assert snapshot.model_history[1][0] == "gemini-cli"
        assert snapshot.model_history[2][0] == "qwen3.5:latest"

    def test_state_persists_across_manager_reload(
        self, tmp_state_dir: Path
    ) -> None:
        """State saved by one CSM instance is loadable by a new instance."""
        csm1 = ConversationStateManager(
            state_dir=tmp_state_dir,
            db_path=tmp_state_dir / "timeline.db",
            encryption_key=None,
        )
        csm1.update_turn(
            "user", "Remember: project deadline is March 20, 2026", "qwen3.5:latest"
        )
        csm1.update_turn(
            "assistant",
            "I'll keep track of the March 20, 2026 deadline.",
            "qwen3.5:latest",
        )
        csm1.mark_model_switch("qwen3.5:latest", "claude-sonnet", reason="test")
        csm1.save()

        session_id = csm1.snapshot.session_id
        injection1 = csm1.get_prompt_injection()
        csm1.close()

        # Create a new manager from the same directory
        csm2 = ConversationStateManager(
            state_dir=tmp_state_dir,
            db_path=tmp_state_dir / "timeline2.db",
            encryption_key=None,
        )

        injection2 = csm2.get_prompt_injection()
        assert csm2.snapshot.session_id == session_id
        assert injection2["anchor_entities"] == injection1["anchor_entities"]
        assert injection2["prior_decisions"] == injection1["prior_decisions"]
        assert injection2["rolling_summary"] == injection1["rolling_summary"]
        csm2.close()

    def test_gateway_response_continuity_context_field(self) -> None:
        """GatewayResponse._continuity_context defaults to empty dict."""
        from jarvis_engine.gateway.models import GatewayResponse

        resp = GatewayResponse(text="hi", model="test", provider="test")
        assert resp._continuity_context == {}

        # When populated, it carries the expected keys
        resp._continuity_context = {
            "original_model": "ollama",
            "fallback_model": "groq",
            "intent_preserved": True,
            "first_message_preserved": True,
        }
        assert resp._continuity_context["intent_preserved"] is True
