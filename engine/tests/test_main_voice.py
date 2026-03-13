"""Tests for voice-related CLI commands and voice pipeline functions.

Covers: voice-say, voice-listen, voice-list, voice-enroll, voice-verify,
voice-run (routing), wake-word, conversation history, conversation continuity,
model switch logging, URL shortening, datetime prompt, _build_smart_context,
_MAX_TOKENS_BY_ROUTE, QueryCommand.history, QueryHandler history injection.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine import main as main_mod
from jarvis_engine import voice_pipeline as voice_pipeline_mod
from jarvis_engine import voice_context as voice_context_mod
from jarvis_engine import voice_extractors as voice_extractors_mod
from jarvis_engine.command_bus import AppContext, CommandBus
from jarvis_engine.gateway.models import ModelGateway
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine


# ===========================================================================
# Standalone voice utility tests (no mock_bus needed)
# ===========================================================================


def test_sanitize_memory_content_redacts_credentials() -> None:
    from jarvis_engine import auto_ingest as auto_ingest_mod

    content = "master password: ExamplePass123! token=abc123"
    cleaned = auto_ingest_mod.sanitize_memory_content(content)
    assert "ExamplePass123!" not in cleaned
    assert "abc123" not in cleaned
    assert "[redacted]" in cleaned


def test_current_datetime_prompt_line_includes_utc_and_epoch() -> None:
    line = voice_context_mod._current_datetime_prompt_line()
    assert "Current date/time:" in line
    assert "UTC" in line
    assert "epoch" in line
    assert "Treat this as the present" in line


def testshorten_urls_for_speech_replaces_raw_url_with_domain_link() -> None:
    text = "Check this out: https://docs.example.com/guides/very/long/path?query=1"
    shortened = voice_extractors_mod.shorten_urls_for_speech(text)
    assert "https://" not in shortened
    assert "[docs.example.com link]" in shortened


def test_conversation_continuity_instruction_on_model_switch(monkeypatch) -> None:
    monkeypatch.setattr(voice_pipeline_mod, "_last_routed_model","kimi-k2")
    line = voice_pipeline_mod._conversation_continuity_instruction("gemma3:4b", history_len=3)
    assert line is not None
    assert "previous turn used model 'kimi-k2'" in line
    assert "uses 'gemma3:4b'" in line


def test_conversation_continuity_instruction_no_history_or_same_model(monkeypatch) -> None:
    monkeypatch.setattr(voice_pipeline_mod, "_last_routed_model","gemma3:4b")
    assert voice_pipeline_mod._conversation_continuity_instruction("gemma3:4b", history_len=3) is None
    assert voice_pipeline_mod._conversation_continuity_instruction("kimi-k2", history_len=0) is None


def test_mark_routed_model_logs_on_switch(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    def _fake_log_activity(category: str, summary: str, details: dict | None = None):
        calls.append((category, summary, details or {}))
        return "evt"

    class _Cat:
        LLM_ROUTING = "llm_routing"
        CONVERSATION_STATE = "conversation_state"

    import types
    fake_mod = types.SimpleNamespace(ActivityCategory=_Cat, log_activity=_fake_log_activity)
    monkeypatch.setitem(__import__("sys").modules, "jarvis_engine.activity_feed", fake_mod)

    monkeypatch.setattr(voice_pipeline_mod, "_last_routed_model",None)
    voice_pipeline_mod._mark_routed_model("kimi-k2", "groq")
    voice_pipeline_mod._mark_routed_model("gemma3:4b", "ollama")

    # First call: model switch event from voice_pipeline
    # Second call: continuity_reconstruction event from conversation_state
    assert len(calls) == 2
    assert "kimi-k2 -> gemma3:4b" in calls[0][1]
    assert calls[0][2]["event"] == "conversation_model_switch"
    assert calls[1][2]["event"] == "continuity_reconstruction"


def test_cmd_voice_say_sanitizes_urls_before_dispatch(capsys, monkeypatch) -> None:
    from jarvis_engine.commands.voice_commands import VoiceSayResult

    captured: dict[str, object] = {}

    class _Bus:
        def dispatch(self, cmd):
            captured["text"] = getattr(cmd, "text", "")
            return VoiceSayResult(voice_name="David", output_wav="", message="Spoken.")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())
    rc = main_mod.cmd_voice_say(
        text="source https://www.openai.com/research/latest",
        profile="jarvis_like",
        voice_pattern="",
        output_wav="",
        rate=-1,
    )
    assert rc == 0
    assert captured["text"] == "source [openai.com link]"
    out = capsys.readouterr().out
    assert "voice=David" in out


def test_cmd_voice_listen_emits_state_transitions(monkeypatch, capsys) -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    class _Bus:
        def dispatch(self, _cmd):
            return VoiceListenResult(text="hello jarvis", confidence=0.91, duration_seconds=1.2, message="ok")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())

    rc = main_mod.cmd_voice_listen(duration=3.0, language="en", execute=False)
    assert rc == 0

    out = capsys.readouterr().out
    assert "listening_state=arming" in out
    assert "listening_state=listening" in out
    assert "listening_state=processing" in out
    assert "listening_state=idle" in out
    assert "transcription=hello jarvis" in out


def test_cmd_voice_listen_emits_error_state(monkeypatch, capsys) -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    class _Bus:
        def dispatch(self, _cmd):
            return VoiceListenResult(text="", confidence=0.0, duration_seconds=0.0, message="error: microphone unavailable")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())

    rc = main_mod.cmd_voice_listen(duration=3.0, language="en", execute=False)
    assert rc == 2
    out = capsys.readouterr().out
    assert "listening_state=error" in out
    assert "error: microphone unavailable" in out


def test_cmd_voice_listen_dispatches_conversation_mode_when_not_executing(monkeypatch) -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    captured: dict[str, object] = {}

    class _Bus:
        def dispatch(self, cmd):
            captured["mode"] = getattr(cmd, "utterance_mode", "")
            return VoiceListenResult(text="hello jarvis", confidence=0.91, duration_seconds=1.2, message="ok")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())

    rc = main_mod.cmd_voice_listen(duration=3.0, language="en", execute=False)
    assert rc == 0
    assert captured["mode"] == "conversation"


def test_cmd_voice_listen_dispatches_command_mode_when_executing(monkeypatch) -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    captured: dict[str, object] = {}

    class _Bus:
        def dispatch(self, cmd):
            captured["mode"] = getattr(cmd, "utterance_mode", "")
            return VoiceListenResult(text="brain status", confidence=0.91, duration_seconds=1.2, message="ok")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())
    monkeypatch.setattr(main_mod, "cmd_voice_run", lambda **_: 0)

    rc = main_mod.cmd_voice_listen(duration=3.0, language="en", execute=True)
    assert rc == 0
    assert captured["mode"] == "command"


def test_cmd_voice_listen_forwards_utterance_when_executing(monkeypatch) -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    utterance = {
        "raw_text": "Jarvis brain status",
        "command_text": "brain status",
        "language": "en",
        "confidence": 0.93,
        "backend": "deepgram-nova3",
        "segments": [
            {"start": 0.0, "end": 1.1, "text": "Jarvis brain status", "kind": "utterance"},
        ],
    }
    captured: dict[str, object] = {}

    class _Bus:
        def dispatch(self, cmd):
            return VoiceListenResult(
                text="brain status",
                confidence=0.91,
                duration_seconds=1.2,
                utterance=utterance,
                message="ok",
            )

    def _fake_voice_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())
    monkeypatch.setattr(main_mod, "cmd_voice_run", _fake_voice_run)

    rc = main_mod.cmd_voice_listen(duration=3.0, language="en", execute=True)
    assert rc == 0
    assert captured["text"] == "brain status"
    assert captured["utterance"] == utterance


def test_cmd_voice_run_routes_web_research(monkeypatch, capsys) -> None:
    """Web search queries route through LLM with web augmentation."""
    from jarvis_engine.commands.voice_commands import VoiceRunResult

    class _Bus:
        def dispatch(self, cmd):
            return VoiceRunResult(return_code=0, intent="web_research", message="ok")

    monkeypatch.setattr(main_mod, "_get_bus", lambda: _Bus())

    rc = main_mod.cmd_voice_run(
        text="Jarvis, search the web for samsung galaxy s25 spam call filtering",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 0


# ===========================================================================
# Voice commands via mock bus
# ===========================================================================


class TestVoiceList:
    """Tests for cmd_voice_list."""

    def test_voice_list_with_voices(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceListResult
        result = VoiceListResult(windows_voices=["David", "Zira"], edge_voices=["en-GB-RyanNeural"])
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_list()
        assert rc == 0
        out = capsys.readouterr().out
        assert "David" in out
        assert "en-GB-RyanNeural" in out

    def test_voice_list_empty(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceListResult
        result = VoiceListResult(windows_voices=[], edge_voices=[])
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_list()
        assert rc == 1


class TestVoiceSay:
    """Tests for cmd_voice_say."""

    def test_voice_say(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceSayResult
        result = VoiceSayResult(voice_name="David", output_wav="", message="Spoken.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_say(text="Hello", profile="jarvis_like",
                                     voice_pattern="", output_wav="", rate=-1)
        assert rc == 0
        out = capsys.readouterr().out
        assert "voice=David" in out

    def test_voice_say_with_wav(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceSayResult
        result = VoiceSayResult(voice_name="Zira", output_wav="/tmp/out.wav", message="Saved.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_say(text="Test", profile="default",
                                     voice_pattern="", output_wav="/tmp/out.wav", rate=150)
        assert rc == 0
        out = capsys.readouterr().out
        assert "wav=/tmp/out.wav" in out


class TestVoiceEnroll:
    """Tests for cmd_voice_enroll."""

    def test_enroll_success(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceEnrollResult
        result = VoiceEnrollResult(user_id="conner", profile_path="/tmp/profile",
                                   samples=3, message="Enrolled successfully.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_enroll(user_id="conner", wav_path="/tmp/voice.wav", replace=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "user_id=conner" in out
        assert "samples=3" in out

    def test_enroll_error(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceEnrollResult
        result = VoiceEnrollResult(message="error: WAV file not found.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_enroll(user_id="conner", wav_path="/bad/path.wav", replace=False)
        assert rc == 2


class TestVoiceVerify:
    """Tests for cmd_voice_verify."""

    def test_verify_matched(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(user_id="conner", score=0.95, threshold=0.82,
                                   matched=True, message="Match confirmed.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_verify(user_id="conner", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 0
        out = capsys.readouterr().out
        assert "matched=True" in out

    def test_verify_not_matched(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(user_id="conner", score=0.5, threshold=0.82,
                                   matched=False, message="No match.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_verify(user_id="conner", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 2

    def test_verify_error(self, capsys, mock_bus):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(message="error: No enrolled profile.")
        bus = mock_bus(result)
        rc = main_mod.cmd_voice_verify(user_id="nobody", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 2


class TestWakeWord:
    """Tests for cmd_wake_word."""

    def test_wake_word_not_started(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import WakeWordStartResult
        result = WakeWordStartResult(started=False, message="pyaudio not installed.")
        bus = mock_bus(result)
        rc = main_mod.cmd_wake_word(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "started=False" in out


# ===========================================================================
# Conversation history buffer tests
# ===========================================================================


class TestConversationHistory:
    """Tests for _conversation_history, _add_to_history, _get_history_messages."""

    def setup_method(self):
        """Reset module-level conversation history before each test."""
        voice_pipeline_mod._conversation_history.clear()
        voice_pipeline_mod._conversation_history_loaded = True

    def test_add_to_history_appends_message(self):
        """_add_to_history appends a dict with role and content."""
        voice_pipeline_mod._add_to_history("user", "Hello Jarvis")
        hist = voice_pipeline_mod._get_history_messages()
        assert len(hist) == 1
        assert hist[0] == {"role": "user", "content": "Hello Jarvis"}

    def test_add_to_history_multiple_messages(self):
        """Multiple calls build up the history list."""
        voice_pipeline_mod._add_to_history("user", "What is the weather?")
        voice_pipeline_mod._add_to_history("assistant", "It is sunny.")
        hist = voice_pipeline_mod._get_history_messages()
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[1]["role"] == "assistant"

    def test_history_caps_at_max_turns_times_2(self):
        """History is capped at _CONVERSATION_MAX_TURNS * 2 entries."""
        max_entries = voice_pipeline_mod._CONVERSATION_MAX_TURNS * 2
        # Add more than the cap
        for i in range(max_entries + 6):
            role = "user" if i % 2 == 0 else "assistant"
            voice_pipeline_mod._add_to_history(role, f"message {i}")

        hist = voice_pipeline_mod._get_history_messages()
        assert len(hist) == max_entries
        # Oldest messages should have been evicted; latest should be present
        assert hist[-1]["content"] == f"message {max_entries + 5}"

    def test_history_truncates_long_content(self):
        """Content is truncated to configured max chars per message."""
        long_msg = "x" * 2000
        voice_pipeline_mod._add_to_history("user", long_msg)
        hist = voice_pipeline_mod._get_history_messages()
        assert len(hist[0]["content"]) == min(
            len(long_msg),
            voice_pipeline_mod._CONVERSATION_MAX_CHARS_PER_MESSAGE,
        )

    def test_get_history_returns_copy(self):
        """_get_history_messages returns a copy, not the original list."""
        voice_pipeline_mod._add_to_history("user", "test")
        hist = voice_pipeline_mod._get_history_messages()
        hist.clear()
        # Original should be unaffected
        assert len(voice_pipeline_mod._get_history_messages()) == 1

    def test_conversation_max_turns_within_supported_bounds(self):
        """_CONVERSATION_MAX_TURNS follows bounded env configuration."""
        assert 4 <= voice_pipeline_mod._CONVERSATION_MAX_TURNS <= 40


# ===========================================================================
# _MAX_TOKENS_BY_ROUTE tests
# ===========================================================================


class TestMaxTokensByRoute:
    """Tests for _MAX_TOKENS_BY_ROUTE configuration."""

    @pytest.mark.parametrize("route,expected", [
        pytest.param("math_logic", 2048, id="math_logic"),
        pytest.param("complex", 2048, id="complex"),
        pytest.param("routine", 1024, id="routine"),
        pytest.param("simple_private", 1024, id="simple_private"),
    ])
    def test_max_tokens_by_route(self, route, expected):
        assert voice_pipeline_mod._MAX_TOKENS_BY_ROUTE[route] == expected

    def test_max_tokens_unknown_route_returns_none(self):
        """Unknown routes are not in the dict (caller uses .get with default)."""
        assert voice_pipeline_mod._MAX_TOKENS_BY_ROUTE.get("unknown_route") is None


# ===========================================================================
# _build_smart_context tests
# ===========================================================================


class TestBuildSmartContext:
    """Tests for _build_smart_context function."""

    def test_hybrid_search_path_when_engine_available(self, monkeypatch):
        """When bus has engine and embed_service on ctx, uses hybrid_search."""
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed_query.return_value = [0.1, 0.2, 0.3]
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=mock_embed)

        fake_records = [
            {"summary": "User likes hiking on weekends"},
            {"summary": "User takes metformin daily"},
        ]

        with patch("jarvis_engine.voice_pipeline.hybrid_search", create=True) as mock_hs:
            # hybrid_search is imported inside _build_smart_context, so patch the import target
            with patch.dict("sys.modules", {}):
                pass
            # Patch at the location where it's imported inside the function
            with patch("jarvis_engine.memory.search.hybrid_search", return_value=fake_records):
                memory_lines, fact_lines, _cb, _prefs = voice_pipeline_mod._build_smart_context(bus,"health")

        # Memory lines come from hybrid_search results
        assert "User likes hiking on weekends" in memory_lines
        assert "User takes metformin daily" in memory_lines

    def test_legacy_fallback_when_no_engine(self, monkeypatch):
        """When bus has no engine on ctx, falls back to build_context_packet."""
        bus = MagicMock(spec=[])  # empty spec - no attributes
        bus.ctx = AppContext()  # all None defaults

        fake_packet = {
            "selected": [
                {"summary": "Legacy memory entry 1"},
                {"summary": "Legacy memory entry 2"},
            ]
        }

        monkeypatch.setattr(
            voice_pipeline_mod, "build_context_packet", lambda *a, **kw: fake_packet
        )

        memory_lines, fact_lines, _cb, _prefs = voice_pipeline_mod._build_smart_context(bus,"anything")
        assert "Legacy memory entry 1" in memory_lines
        assert "Legacy memory entry 2" in memory_lines

    def test_legacy_fallback_when_hybrid_fails(self, monkeypatch):
        """When hybrid_search raises, falls back to build_context_packet."""
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed_query.side_effect = RuntimeError("embed failed")
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=mock_embed)

        fake_packet = {
            "selected": [{"summary": "Fallback memory"}]
        }
        monkeypatch.setattr(
            voice_pipeline_mod, "build_context_packet", lambda *a, **kw: fake_packet
        )

        memory_lines, fact_lines, _cb, _prefs = voice_pipeline_mod._build_smart_context(bus,"test query")
        assert "Fallback memory" in memory_lines

    def test_kg_facts_injected_when_engine_available(self, monkeypatch, tmp_path):
        """KG facts are queried and returned as fact_lines."""
        bus = MagicMock(spec=[])
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=None)

        # Legacy path returns empty for memory
        monkeypatch.setattr(
            voice_pipeline_mod, "build_context_packet",
            lambda *a, **kw: {"selected": []},
        )

        # Mock the KnowledgeGraph that's constructed inside _build_smart_context
        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = [
            {"label": "User is allergic to peanuts", "confidence": 0.9},
            {"label": "User prefers window seat", "confidence": 0.7},
        ]

        with patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance):
            memory_lines, fact_lines, _cb, _prefs = voice_pipeline_mod._build_smart_context(bus,"tell me about allergies")

        assert "User is allergic to peanuts" in fact_lines

    def test_kg_facts_filtered_by_confidence(self, monkeypatch):
        """KG facts with confidence < 0.5 are excluded from fact_lines."""
        bus = MagicMock(spec=[])
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=None)

        monkeypatch.setattr(
            voice_pipeline_mod, "build_context_packet",
            lambda *a, **kw: {"selected": []},
        )

        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = [
            {"label": "High confidence fact", "confidence": 0.9},
            {"label": "Low confidence fact", "confidence": 0.3},
        ]

        with patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance):
            memory_lines, fact_lines, _cb, _prefs = voice_pipeline_mod._build_smart_context(bus,"some query")

        assert "High confidence fact" in fact_lines
        assert "Low confidence fact" not in fact_lines

    def test_returns_empty_when_everything_fails(self, monkeypatch):
        """Returns ([], []) when both memory and KG queries fail."""
        bus = MagicMock(spec=[])
        bus.ctx = AppContext()  # all None defaults

        monkeypatch.setattr(
            voice_pipeline_mod, "build_context_packet",
            MagicMock(side_effect=RuntimeError("DB broken")),
        )

        memory_lines, fact_lines, cross_branch_lines, pref_lines = voice_pipeline_mod._build_smart_context(bus, "broken query")
        assert memory_lines == []
        assert fact_lines == []
        assert cross_branch_lines == []
        assert pref_lines == []


# ===========================================================================
# QueryCommand.history field tests
# ===========================================================================


class TestQueryCommandHistory:
    """Tests for the history field on QueryCommand."""

    def test_query_command_has_history_field(self):
        """QueryCommand has a history field defaulting to empty tuple."""
        from jarvis_engine.commands.task_commands import QueryCommand
        cmd = QueryCommand(query="test")
        assert hasattr(cmd, "history")
        assert cmd.history == ()

    def test_query_command_history_accepts_tuples(self):
        """QueryCommand.history can hold conversation turn tuples."""
        from jarvis_engine.commands.task_commands import QueryCommand
        history = (("user", "Hello"), ("assistant", "Hi there"))
        cmd = QueryCommand(query="follow up", history=history)
        assert cmd.history == history
        assert len(cmd.history) == 2

    def test_query_command_is_frozen(self):
        """QueryCommand is a frozen dataclass (immutable)."""
        from jarvis_engine.commands.task_commands import QueryCommand
        cmd = QueryCommand(query="test")
        with pytest.raises(AttributeError):
            cmd.query = "changed"


# ===========================================================================
# QueryHandler with conversation history injection tests
# ===========================================================================


class TestQueryHandlerHistory:
    """Tests for QueryHandler injecting history into LLM messages."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_injects_history_before_query(self):
        """QueryHandler places history messages between system prompt and user query."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock(spec=ModelGateway)
        mock_gateway.complete.return_value = GatewayResponse(
            text="response", model="test-model", provider="test"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="What about my diet?",
            system_prompt="You are Jarvis.",
            history=(
                ("user", "Tell me about my health"),
                ("assistant", "You take metformin daily."),
            ),
        )

        handler.handle(cmd)

        # Inspect the messages passed to gateway.complete
        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages") or call_kwargs[0][0]
        # If passed as positional, it'll be messages=...
        if not isinstance(messages, list):
            messages = call_kwargs.kwargs["messages"]

        # Expected order: system, history user, history assistant, current user
        assert messages[0] == {"role": "system", "content": "You are Jarvis."}
        assert messages[1] == {"role": "user", "content": "Tell me about my health"}
        assert messages[2] == {"role": "assistant", "content": "You take metformin daily."}
        assert messages[3] == {"role": "user", "content": "What about my diet?"}

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_works_without_history(self):
        """QueryHandler works correctly when history is empty (default)."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock(spec=ModelGateway)
        mock_gateway.complete.return_value = GatewayResponse(
            text="answer", model="test-model", provider="test"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="What time is it?",
            system_prompt="You are helpful.",
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        # Only system + user, no history
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "What time is it?"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_filters_invalid_history_roles(self):
        """QueryHandler only injects 'user' and 'assistant' roles from history."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock(spec=ModelGateway)
        mock_gateway.complete.return_value = GatewayResponse(
            text="ok", model="m", provider="p"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="test",
            history=(
                ("user", "valid user msg"),
                ("system", "injected system msg"),  # should be filtered
                ("assistant", "valid assistant msg"),
                ("admin", "injected admin msg"),  # should be filtered
            ),
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        roles = [m["role"] for m in messages]
        assert "admin" not in roles
        # The handler now injects a datetime system prompt even when cmd.system_prompt is empty,
        # but the injected "system" entry from history should still be filtered out.
        system_contents = [m["content"] for m in messages if m["role"] == "system"]
        assert "injected system msg" not in system_contents

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_skips_empty_content_in_history(self):
        """QueryHandler skips history entries with empty content."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock(spec=ModelGateway)
        mock_gateway.complete.return_value = GatewayResponse(
            text="ok", model="m", provider="p"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="final question",
            history=(
                ("user", "first question"),
                ("assistant", ""),  # empty - should be skipped
                ("user", "second question"),
            ),
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        # Should have: first question, second question, final question (no empty assistant)
        contents = [m["content"] for m in messages]
        assert "" not in contents
