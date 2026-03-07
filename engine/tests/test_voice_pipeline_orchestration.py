"""Tests for voice_pipeline.py orchestration functions.

Covers the 3 most complex untested function clusters:
  1. _classify_and_route() — IntentClassifier routing, privacy fallback
  2. _perform_web_search() — web search execution, result formatting
  3. _dispatch_and_handle_response() — LLM dispatch, learning trigger

Also tests supporting helpers:
  - ConversationState (load/save, mark_routed_model, add_to_history)
  - _needs_web_search() / _requires_fresh_web_confirmation()
  - _learn_conversation()
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_VP = "jarvis_engine.voice_pipeline"


# ===========================================================================
# ConversationState
# ===========================================================================


class TestConversationState:
    """Tests for the ConversationState class directly."""

    def _make_state(self, tmp_path: Path):
        from jarvis_engine.voice_pipeline import ConversationState
        return ConversationState(history_file=tmp_path / "conv.json")

    def test_add_to_history_appends_and_caps(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        for i in range(30):
            state.add_to_history("user", f"msg-{i}")
        history = state.get_history_messages()
        # Max turns is 12 by default, so 12*2 = 24 messages max
        assert len(history) <= 24

    def test_add_to_history_truncates_long_content(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        long_msg = "x" * 5000
        state.add_to_history("user", long_msg)
        history = state.get_history_messages()
        assert len(history[0]["content"]) <= 2000

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.add_to_history("user", "hello")
        state.add_to_history("assistant", "hi there")
        state.save_conversation_history(force=True)

        from jarvis_engine.voice_pipeline import ConversationState
        state2 = ConversationState(history_file=tmp_path / "conv.json")
        state2.load_conversation_history()
        history = state2.get_history_messages()
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "hi there"

    def test_clear_history(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.add_to_history("user", "hello")
        # Force the loaded flag to True by reading first, so get_history_messages
        # won't reload from disk after clear.
        assert len(state.get_history_messages()) == 1
        state.clear_history()
        # After clear, in-memory list is empty.  get_history_messages won't
        # reload because _conversation_history_loaded is already True.
        assert state.get_history_messages() == []

    def test_get_history_lazy_loads(self, tmp_path: Path) -> None:
        """First call to get_history_messages triggers load_conversation_history."""
        hfile = tmp_path / "conv.json"
        hfile.write_text(json.dumps([{"role": "user", "content": "persisted"}]))

        from jarvis_engine.voice_pipeline import ConversationState
        state = ConversationState(history_file=hfile)
        msgs = state.get_history_messages()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persisted"

    def test_mark_routed_model_ignores_empty(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.mark_routed_model("", "test")
        assert state._last_routed_model is None

    def test_mark_routed_model_logs_switch(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        with patch("jarvis_engine.voice_pipeline.ConversationState.mark_routed_model.__module__", create=True):
            state.mark_routed_model("model-a", "provider-a")
            assert state._last_routed_model == "model-a"
            # Switch model — should log activity
            with patch(f"{_VP}.ConversationState.mark_routed_model", wraps=state.mark_routed_model):
                state.mark_routed_model("model-b", "provider-b")
                assert state._last_routed_model == "model-b"

    def test_continuity_instruction_none_when_no_history(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.mark_routed_model("model-a", "p")
        result = state.conversation_continuity_instruction("model-b", 0)
        assert result is None

    def test_continuity_instruction_none_when_same_model(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.mark_routed_model("model-a", "p")
        result = state.conversation_continuity_instruction("model-a", 5)
        assert result is None

    def test_continuity_instruction_present_on_switch(self, tmp_path: Path) -> None:
        state = self._make_state(tmp_path)
        state.mark_routed_model("model-a", "p")
        result = state.conversation_continuity_instruction("model-b", 5)
        assert result is not None
        assert "model-a" in result
        assert "model-b" in result

    def test_save_debounce_skips_non_dirty(self, tmp_path: Path) -> None:
        """save_conversation_history without force should skip if not dirty."""
        state = self._make_state(tmp_path)
        # Not dirty, non-forced save should be no-op (no file created)
        state.save_conversation_history(force=False)
        assert not (tmp_path / "conv.json").exists()


# ===========================================================================
# _needs_web_search
# ===========================================================================


class TestNeedsWebSearch:
    """Tests for _needs_web_search() pattern matching."""

    def _call(self, query: str) -> bool:
        from jarvis_engine.voice_pipeline import _needs_web_search
        return _needs_web_search(query)

    def test_current_events_trigger(self) -> None:
        assert self._call("What is the latest news about AI?") is True

    def test_stock_price_trigger(self) -> None:
        assert self._call("What is the stock price of Tesla?") is True

    def test_weather_trigger(self) -> None:
        assert self._call("What's the weather forecast for tomorrow?") is True

    def test_score_trigger(self) -> None:
        assert self._call("What was the score of the game?") is True

    def test_year_trigger(self) -> None:
        assert self._call("Best programming languages in 2026") is True

    def test_search_keyword(self) -> None:
        assert self._call("Search for python tutorials") is True

    def test_personal_exclusion_calendar(self) -> None:
        assert self._call("What's on my calendar today?") is False

    def test_personal_exclusion_reminder(self) -> None:
        assert self._call("Remind me to buy groceries") is False

    def test_personal_exclusion_medication(self) -> None:
        assert self._call("When is my prescription refill?") is False

    def test_plain_conversation_no_web(self) -> None:
        assert self._call("Tell me a joke about cats") is False

    def test_how_to_trigger(self) -> None:
        assert self._call("How do I install Node.js?") is True


# ===========================================================================
# _requires_fresh_web_confirmation
# ===========================================================================


class TestRequiresFreshWebConfirmation:
    """Tests for _requires_fresh_web_confirmation() strict marker matching."""

    def _call(self, query: str) -> bool:
        from jarvis_engine.voice_pipeline import _requires_fresh_web_confirmation
        return _requires_fresh_web_confirmation(query)

    def test_latest_triggers(self) -> None:
        assert self._call("What are the latest headlines?") is True

    def test_today_triggers(self) -> None:
        assert self._call("What happened today in sports?") is True

    def test_right_now_triggers(self) -> None:
        assert self._call("What is happening right now in the market?") is True

    def test_breaking_triggers(self) -> None:
        assert self._call("Any breaking news?") is True

    def test_plain_query_no_trigger(self) -> None:
        assert self._call("Tell me about quantum computing") is False

    def test_as_of_triggers(self) -> None:
        assert self._call("What is the status as of March?") is True


# ===========================================================================
# _classify_and_route
# ===========================================================================


class TestClassifyAndRoute:
    """Tests for _classify_and_route() — intent classification + model selection."""

    def _make_bus(
        self,
        *,
        classifier_result=None,
        classifier_error=None,
        gateway_models=None,
    ):
        bus = MagicMock()
        # Set up intent classifier
        if classifier_error:
            bus.ctx.intent_classifier.classify.side_effect = classifier_error
        elif classifier_result:
            bus.ctx.intent_classifier.classify.return_value = classifier_result
        else:
            bus.ctx.intent_classifier = None

        # Gateway
        if gateway_models is not None:
            bus.ctx.gateway.available_model_names.return_value = gateway_models
        else:
            bus.ctx.gateway = None

        return bus

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_classifier_routes_routine(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus(classifier_result=("routine", "kimi-k2", 0.85))
        route, model = _classify_and_route(bus, "tell me a joke")
        assert route == "routine"
        assert model == "kimi-k2"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_classifier_routes_complex(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus(classifier_result=("complex", "claude-opus", 0.92))
        route, model = _classify_and_route(bus, "explain quantum entanglement in detail")
        assert route == "complex"
        assert model == "claude-opus"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_classifier_routes_web_research(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus(classifier_result=("web_research", "gemini-2.5-flash", 0.88))
        route, model = _classify_and_route(bus, "search for latest news")
        assert route == "web_research"
        assert model == "gemini-2.5-flash"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_privacy_fallback_when_classifier_absent(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus()  # No classifier
        route, model = _classify_and_route(bus, "what is my bank account balance")
        assert route == "simple_private"
        assert model == "gemma3:4b"  # DEFAULT_LOCAL_MODEL

    @patch.dict("os.environ", {"GROQ_API_KEY": "key123", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_env_model_fallback_non_private(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus()  # No classifier
        route, model = _classify_and_route(bus, "tell me about dinosaurs")
        assert route == "routine"  # default_route
        assert model == "kimi-k2"  # GROQ_API_KEY is set -> kimi-k2

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_classifier_error_falls_through(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus(classifier_error=RuntimeError("embedding fail"))
        # Non-private query, no cloud keys -> local model
        route, model = _classify_and_route(bus, "tell me a joke")
        assert model == "gemma3:4b"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "mk", "ZAI_API_KEY": ""})
    def test_mistral_fallback_priority(self) -> None:
        from jarvis_engine.voice_pipeline import _classify_and_route
        bus = self._make_bus()  # No classifier
        route, model = _classify_and_route(bus, "explain gravity")
        assert model == "devstral-2"


# ===========================================================================
# _perform_web_search
# ===========================================================================


class TestPerformWebSearch:
    """Tests for _perform_web_search() — web search execution and result formatting."""

    @patch(f"{_VP}._needs_web_search", return_value=False)
    def test_skips_when_not_needed(self, mock_nws) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        parts = ["existing"]
        searched, attempted, result = _perform_web_search(
            "hello", parts, force=False, route="routine",
        )
        assert searched is False
        assert attempted is False
        assert result == {}
        assert parts == ["existing"]  # Unchanged

    @patch("jarvis_engine.web_research.run_web_research")
    def test_force_triggers_search(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        mock_research.return_value = {
            "summary_lines": ["Result 1", "Result 2"],
            "scanned_urls": ["https://example.com"],
            "findings": [{"domain": "example.com", "url": "https://example.com/1"}],
        }
        parts = []
        searched, attempted, result = _perform_web_search(
            "latest AI news", parts, force=True, route="routine",
        )
        assert searched is True
        assert attempted is True
        assert "summary_lines" in result
        assert any("Web search results" in p for p in parts)

    @patch("jarvis_engine.web_research.run_web_research")
    def test_web_research_route_triggers(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        mock_research.return_value = {
            "summary_lines": ["Line 1"],
            "scanned_urls": [],
            "findings": [],
        }
        parts = []
        searched, attempted, result = _perform_web_search(
            "test query", parts, force=False, route="web_research",
        )
        assert searched is True
        assert attempted is True

    @patch("jarvis_engine.web_research.run_web_research")
    def test_empty_summary_lines(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        mock_research.return_value = {"summary_lines": [], "scanned_urls": []}
        parts = []
        searched, attempted, result = _perform_web_search(
            "test", parts, force=True, route="routine",
        )
        assert searched is False
        assert attempted is True

    @patch("jarvis_engine.web_research.run_web_research", side_effect=RuntimeError("network down"))
    def test_error_handling(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        parts = []
        searched, attempted, result = _perform_web_search(
            "test", parts, force=True, route="routine",
        )
        assert searched is False
        assert attempted is True
        assert result == {}

    @patch("jarvis_engine.web_research.run_web_research", side_effect=ImportError("web_research not installed"))
    def test_import_error_handling(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        parts = []
        searched, attempted, result = _perform_web_search(
            "test", parts, force=True, route="routine",
        )
        assert searched is False
        assert attempted is True

    @patch("jarvis_engine.web_research.run_web_research")
    def test_sources_appended_to_context(self, mock_research) -> None:
        from jarvis_engine.voice_pipeline import _perform_web_search
        mock_research.return_value = {
            "summary_lines": ["AI is evolving"],
            "scanned_urls": ["https://ai.com", "https://ml.org"],
            "findings": [],
        }
        parts = []
        _perform_web_search("AI news", parts, force=True, route="routine")
        context_part = parts[0]
        assert "Sources:" in context_part
        assert "https://ai.com" in context_part


# ===========================================================================
# _dispatch_and_handle_response
# ===========================================================================


class TestDispatchAndHandleResponse:
    """Tests for _dispatch_and_handle_response() — LLM dispatch + learning."""

    def _make_bus_and_result(
        self,
        *,
        response_text: str = "Hello there!",
        return_code: int = 0,
        model: str = "kimi-k2",
        provider: str = "groq",
    ):
        from jarvis_engine.commands.task_commands import QueryResult
        result = QueryResult(
            text=response_text,
            model=model,
            provider=provider,
            return_code=return_code,
        )
        bus = MagicMock()
        bus.dispatch.return_value = result
        return bus

    @patch(f"{_VP}._learn_conversation")
    @patch(f"{_VP}._mark_routed_model")
    @patch(f"{_VP}._add_to_history")
    @patch("jarvis_engine.main.cmd_voice_say")
    def test_success_path(self, mock_say, mock_hist, mock_mark, mock_learn, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result()
        rc = _dispatch_and_handle_response(
            bus, "hello", "system prompt", 512, "kimi-k2",
            (), speak=False, web_searched=False, web_result={},
            route="routine", response_callback=None,
        )
        assert rc == 0
        output = capsys.readouterr().out
        assert "response=" in output
        assert "model=kimi-k2" in output
        assert "provider=groq" in output
        mock_hist.assert_called_once_with("assistant", "Hello there!")
        mock_mark.assert_called_once_with("kimi-k2", "groq")
        mock_learn.assert_called_once()

    @patch(f"{_VP}._learn_conversation")
    @patch(f"{_VP}._mark_routed_model")
    @patch(f"{_VP}._add_to_history")
    @patch("jarvis_engine.main.cmd_voice_say")
    def test_speak_calls_tts(self, mock_say, mock_hist, mock_mark, mock_learn) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result()
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=True, web_searched=False, web_result={},
            route="routine", response_callback=None,
        )
        assert rc == 0
        mock_say.assert_called_once_with(text="Hello there!")

    @patch(f"{_VP}._learn_conversation")
    @patch(f"{_VP}._mark_routed_model")
    @patch(f"{_VP}._add_to_history")
    @patch("jarvis_engine.main.cmd_voice_say")
    def test_web_searched_flag_printed(self, mock_say, mock_hist, mock_mark, mock_learn, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result()
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=False, web_searched=True, web_result={},
            route="web_research", response_callback=None,
        )
        assert rc == 0
        output = capsys.readouterr().out
        assert "web_search_used=true" in output

    @patch(f"{_VP}._learn_conversation")
    @patch(f"{_VP}._mark_routed_model")
    @patch(f"{_VP}._add_to_history")
    @patch("jarvis_engine.main.cmd_voice_say")
    def test_response_callback_invoked(self, mock_say, mock_hist, mock_mark, mock_learn) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result()
        callback = MagicMock()
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=False, web_searched=False, web_result={},
            route="routine", response_callback=callback,
        )
        assert rc == 0
        callback.assert_called_once_with("Hello there!")

    @patch("jarvis_engine.main.cmd_voice_say")
    def test_llm_failure_returns_1(self, mock_say, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result(return_code=1, response_text="model not loaded")
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=False, web_searched=False, web_result={},
            route="routine", response_callback=None,
        )
        assert rc == 1
        output = capsys.readouterr().out
        assert "intent=llm_unavailable" in output

    @patch("jarvis_engine.main.cmd_voice_say")
    def test_llm_failure_with_web_fallback(self, mock_say, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result(return_code=1)
        web_result = {"summary_lines": ["AI breakthrough announced"]}
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=False, web_searched=True, web_result=web_result,
            route="web_research", response_callback=None,
        )
        assert rc == 0
        output = capsys.readouterr().out
        assert "web-research-fallback" in output
        assert "web_search_used=true" in output

    @patch(f"{_VP}._learn_conversation")
    @patch(f"{_VP}._mark_routed_model")
    @patch(f"{_VP}._add_to_history")
    @patch("jarvis_engine.main.cmd_voice_say")
    def test_empty_response_returns_1(self, mock_say, mock_hist, mock_mark, mock_learn, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = self._make_bus_and_result(response_text="   ")
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=False, web_searched=False, web_result={},
            route="routine", response_callback=None,
        )
        assert rc == 1
        output = capsys.readouterr().out
        assert "intent=llm_empty_response" in output

    @patch("jarvis_engine.main.cmd_voice_say")
    def test_exception_returns_1_and_speaks_error(self, mock_say, capsys) -> None:
        from jarvis_engine.voice_pipeline import _dispatch_and_handle_response
        bus = MagicMock()
        bus.dispatch.side_effect = RuntimeError("connection refused")
        rc = _dispatch_and_handle_response(
            bus, "hello", "sys", 512, "kimi-k2",
            (), speak=True, web_searched=False, web_result={},
            route="routine", response_callback=None,
        )
        assert rc == 1
        output = capsys.readouterr().out
        assert "intent=llm_error" in output
        mock_say.assert_called_once()
        assert "trouble connecting" in mock_say.call_args[1]["text"]


# ===========================================================================
# _learn_conversation
# ===========================================================================


class TestLearnConversation:
    """Tests for _learn_conversation() — dispatches LearnInteractionCommand."""

    def test_dispatches_learn_command(self) -> None:
        from jarvis_engine.voice_pipeline import _learn_conversation
        from jarvis_engine.commands.learning_commands import LearnInteractionCommand
        bus = MagicMock()
        _learn_conversation(bus, "hello", "hi there", "routine", "kimi-k2")
        bus.dispatch.assert_called_once()
        cmd = bus.dispatch.call_args[0][0]
        assert isinstance(cmd, LearnInteractionCommand)
        assert cmd.user_message == "hello"
        assert cmd.assistant_response == "hi there"
        assert cmd.route == "routine"

    def test_truncates_long_messages(self) -> None:
        from jarvis_engine.voice_pipeline import _learn_conversation
        bus = MagicMock()
        long_text = "x" * 5000
        _learn_conversation(bus, long_text, long_text, "routine", "kimi-k2")
        cmd = bus.dispatch.call_args[0][0]
        assert len(cmd.user_message) <= 1000
        assert len(cmd.assistant_response) <= 1000

    @patch(f"{_VP}._auto_ingest_memory")
    def test_fallback_to_auto_ingest(self, mock_ingest) -> None:
        from jarvis_engine.voice_pipeline import _learn_conversation
        bus = MagicMock()
        bus.dispatch.side_effect = RuntimeError("handler not registered")
        _learn_conversation(bus, "hello", "hi", "routine", "kimi-k2")
        mock_ingest.assert_called_once()
        # Verify the auto-ingest fallback content structure
        call_kwargs = mock_ingest.call_args[1]
        assert call_kwargs["source"] == "conversation"
        assert call_kwargs["kind"] == "episodic"
        assert "hello" in call_kwargs["content"]

    @patch(f"{_VP}._auto_ingest_memory", side_effect=OSError("disk full"))
    def test_both_learning_paths_fail_gracefully(self, mock_ingest) -> None:
        """If both LearnInteraction and auto_ingest fail, no exception raised."""
        from jarvis_engine.voice_pipeline import _learn_conversation
        bus = MagicMock()
        bus.dispatch.side_effect = RuntimeError("handler not registered")
        # Should not raise
        _learn_conversation(bus, "hello", "hi", "routine", "kimi-k2")
