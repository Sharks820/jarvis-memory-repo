"""Tests for task_handlers -- QueryHandler and RouteHandler."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis_engine.commands.task_commands import (
    QueryCommand,
    RouteCommand,
    WebResearchCommand,
)
from jarvis_engine.handlers.task_handlers import (
    QueryHandler,
    RouteHandler,
    WebResearchHandler,
)


# ---------------------------------------------------------------------------
# QueryHandler
# ---------------------------------------------------------------------------

def test_query_handler_explicit_model() -> None:
    """QueryHandler uses explicit model when cmd.model is set."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="Hello world",
        model="gpt-4",
        provider="openai",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        fallback_used=False,
        fallback_reason="",
    )

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    cmd = QueryCommand(query="test", model="gpt-4")
    result = handler.handle(cmd)

    assert result.text == "Hello world"
    assert result.model == "gpt-4"
    assert "Explicit model" in result.route_reason
    assert result.return_code == 0


def test_query_handler_with_classifier() -> None:
    """QueryHandler uses classifier when no explicit model is set."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="Classified response",
        model="llama3",
        provider="ollama",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        fallback_used=False,
        fallback_reason="",
    )
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = ("simple_query", "llama3", 0.95)

    handler = QueryHandler(gateway=mock_gateway, classifier=mock_classifier)
    cmd = QueryCommand(query="what time is it")
    result = handler.handle(cmd)

    assert result.text == "Classified response"
    assert "Intent: simple_query" in result.route_reason
    mock_classifier.classify.assert_called_once_with(
        "what time is it",
        available_models=mock_gateway.available_model_names(),
    )


def test_query_handler_no_classifier_fallback() -> None:
    """QueryHandler uses default model when no classifier available."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="Default response",
        model="claude-sonnet-4-5-20250929",
        provider="anthropic",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        fallback_used=False,
        fallback_reason="",
    )

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    cmd = QueryCommand(query="what is the meaning of life")
    result = handler.handle(cmd)

    assert result.text == "Default response"
    assert "Default" in result.route_reason


def test_query_handler_system_prompt_included() -> None:
    """QueryHandler includes system prompt in messages."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="ok",
        model="test",
        provider="test",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        fallback_used=False,
        fallback_reason="",
    )

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    cmd = QueryCommand(query="test", system_prompt="You are Jarvis")
    handler.handle(cmd)

    call_args = mock_gateway.complete.call_args
    messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are Jarvis"


def test_query_handler_fallback_info_passed_through() -> None:
    """QueryHandler passes through fallback info from gateway."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="fallback response",
        model="llama3",
        provider="ollama",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        fallback_used=True,
        fallback_reason="Primary provider failed",
    )

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    cmd = QueryCommand(query="test")
    result = handler.handle(cmd)

    assert result.fallback_used is True
    assert result.fallback_reason == "Primary provider failed"


def test_query_handler_gateway_exception() -> None:
    """QueryHandler returns error result when gateway.complete raises."""
    mock_gateway = MagicMock()
    mock_gateway.complete.side_effect = RuntimeError("connection refused")

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    cmd = QueryCommand(query="test query")
    result = handler.handle(cmd)

    assert result.return_code == 2
    assert "RuntimeError" in result.text
    assert "error" in result.text.lower()


def test_query_handler_conversation_history() -> None:
    """QueryHandler injects conversation history into messages."""
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = SimpleNamespace(
        text="history response",
        model="test",
        provider="test",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        fallback_used=False,
        fallback_reason="",
    )

    handler = QueryHandler(gateway=mock_gateway, classifier=None)
    history = (("user", "hello"), ("assistant", "hi there"), ("user", "how are you"))
    cmd = QueryCommand(query="what is 2+2", history=history)
    handler.handle(cmd)

    call_args = mock_gateway.complete.call_args
    messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
    # 3 history messages + 1 final user query = 4 messages
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1] == {"role": "assistant", "content": "hi there"}
    assert messages[2] == {"role": "user", "content": "how are you"}
    assert messages[3] == {"role": "user", "content": "what is 2+2"}


# ---------------------------------------------------------------------------
# RouteHandler
# ---------------------------------------------------------------------------

def test_route_handler_with_classifier() -> None:
    """RouteHandler uses classifier when query is provided."""
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = ("code_generation", "claude-opus", 0.92)

    handler = RouteHandler(root=Path("."), classifier=mock_classifier)
    cmd = RouteCommand(query="write a python function")
    result = handler.handle(cmd)

    assert result.provider == "claude-opus"
    assert "code_generation" in result.reason


def test_route_handler_legacy_path() -> None:
    """RouteHandler falls back to ModelRouter when no classifier or query."""
    handler = RouteHandler(root=Path("."), classifier=None)
    cmd = RouteCommand(risk="low", complexity="easy")
    result = handler.handle(cmd)

    assert result.provider == "local_primary"


# ---------------------------------------------------------------------------
# WebResearchHandler
# ---------------------------------------------------------------------------

def test_web_research_handler_empty_query() -> None:
    handler = WebResearchHandler(root=Path("."))
    cmd = WebResearchCommand(query="   ")
    result = handler.handle(cmd)
    assert result.return_code == 2


# ===========================================================================
# Expanded test coverage below
# ===========================================================================

from jarvis_engine.commands.task_commands import (
    RunTaskCommand,
)
from jarvis_engine.handlers.task_handlers import RunTaskHandler


# ---------------------------------------------------------------------------
# Helper: mock gateway response
# ---------------------------------------------------------------------------

def _gateway_response(**overrides):
    defaults = dict(
        text="response text",
        model="test-model",
        provider="test-provider",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        fallback_used=False,
        fallback_reason="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# RunTaskHandler tests
# ---------------------------------------------------------------------------


class TestRunTaskHandler:

    def test_run_task_handler_lazy_init(self, tmp_path):
        """Orchestrator is lazily created on first handle() call."""
        handler = RunTaskHandler(root=tmp_path)
        assert handler._orchestrator is None
        assert handler._store is None

    def test_run_task_code_dry_run(self, tmp_path):
        handler = RunTaskHandler(root=tmp_path)
        cmd = RunTaskCommand(
            task_type="code",
            prompt="Write hello world",
            execute=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        with patch("jarvis_engine.handlers.task_handlers._main_mod", create=True):
            result = handler.handle(cmd)
        assert result.allowed is True
        assert result.return_code == 0
        assert "Dry-run" in result.plan

    def test_run_task_privileged_denied(self, tmp_path):
        handler = RunTaskHandler(root=tmp_path)
        cmd = RunTaskCommand(
            task_type="video",
            prompt="Create video",
            execute=True,
            approve_privileged=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = handler.handle(cmd)
        assert result.allowed is False
        assert result.return_code == 2

    def test_run_task_auto_ingest_failure_handled_gracefully(self, tmp_path):
        """Auto-ingest failure should not crash the handler."""
        handler = RunTaskHandler(root=tmp_path)
        cmd = RunTaskCommand(
            task_type="code",
            prompt="Write hello world",
            execute=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        # Even if _auto_ingest_memory fails, result should still be returned
        with patch(
            "jarvis_engine.main._auto_ingest_memory",
            side_effect=RuntimeError("ingest failed"),
        ):
            result = handler.handle(cmd)
        assert result.allowed is True
        assert result.auto_ingest_record_id == ""

    def test_run_task_orchestrator_cached(self, tmp_path):
        """Orchestrator should be created once and reused."""
        handler = RunTaskHandler(root=tmp_path)
        orch1 = handler._get_orchestrator()
        orch2 = handler._get_orchestrator()
        assert orch1 is orch2

    def test_run_task_image_dry_run(self, tmp_path):
        handler = RunTaskHandler(root=tmp_path)
        cmd = RunTaskCommand(
            task_type="image",
            prompt="A landscape painting",
            execute=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = handler.handle(cmd)
        assert result.allowed is True
        assert "Dry-run" in result.plan


# ---------------------------------------------------------------------------
# QueryHandler: extended tests
# ---------------------------------------------------------------------------


class TestQueryHandlerExtended:

    def test_no_system_prompt_single_message(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response()
        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="hello")
        handler.handle(cmd)
        call_args = mock_gateway.complete.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"

    def test_max_tokens_passed_through(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response()
        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="test", max_tokens=2048)
        handler.handle(cmd)
        call_args = mock_gateway.complete.call_args
        assert call_args.kwargs.get("max_tokens", call_args[1].get("max_tokens")) == 2048

    def test_cost_usd_passed_through(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response(cost_usd=0.05)
        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="test")
        result = handler.handle(cmd)
        assert abs(result.cost_usd - 0.05) < 0.001

    def test_token_counts_passed_through(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response(
            input_tokens=100, output_tokens=200
        )
        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="test")
        result = handler.handle(cmd)
        assert result.input_tokens == 100
        assert result.output_tokens == 200

    def test_classifier_confidence_in_route_reason(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response()
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = ("analysis", "claude-opus", 0.87)
        handler = QueryHandler(gateway=mock_gateway, classifier=mock_classifier)
        cmd = QueryCommand(query="analyze this data")
        result = handler.handle(cmd)
        assert "0.87" in result.route_reason

    def test_route_reason_passed_to_gateway(self):
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response()
        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="test", model="custom-model")
        handler.handle(cmd)
        call_args = mock_gateway.complete.call_args
        route_reason = call_args.kwargs.get("route_reason", "")
        assert "Explicit model" in route_reason


# ---------------------------------------------------------------------------
# RouteHandler: extended tests
# ---------------------------------------------------------------------------


class TestRouteHandlerExtended:

    def test_route_with_empty_query_and_classifier_uses_legacy(self):
        mock_classifier = MagicMock()
        handler = RouteHandler(root=Path("."), classifier=mock_classifier)
        cmd = RouteCommand(query="", risk="low", complexity="easy")
        result = handler.handle(cmd)
        # Empty query means classifier is not used
        assert result.provider == "local_primary"
        mock_classifier.classify.assert_not_called()

    def test_route_classifier_returns_confidence(self):
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = ("reasoning", "claude-opus", 0.99)
        handler = RouteHandler(root=Path("."), classifier=mock_classifier)
        cmd = RouteCommand(query="explain quantum computing")
        result = handler.handle(cmd)
        assert "0.99" in result.reason

    def test_route_high_risk_complexity(self):
        handler = RouteHandler(root=Path("."), classifier=None)
        cmd = RouteCommand(risk="high", complexity="hard")
        result = handler.handle(cmd)
        # ModelRouter routes high risk/complexity to cloud
        assert result.provider != ""

    def test_route_medium_risk(self):
        handler = RouteHandler(root=Path("."), classifier=None)
        cmd = RouteCommand(risk="medium", complexity="normal")
        result = handler.handle(cmd)
        assert result.provider != ""


# ---------------------------------------------------------------------------
# WebResearchHandler: extended tests
# ---------------------------------------------------------------------------


class TestWebResearchHandlerExtended:

    def test_successful_research(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query", auto_ingest=False)
        mock_report = {
            "summary_lines": ["Finding 1", "Finding 2"],
            "findings": [{"domain": "example.com", "title": "Test"}],
        }
        with patch(
            "jarvis_engine.web_research.run_web_research",
            return_value=mock_report,
        ):
            result = handler.handle(cmd)
        assert result.return_code == 0
        assert result.report == mock_report

    def test_research_value_error_returns_code_2(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query")
        with patch(
            "jarvis_engine.web_research.run_web_research",
            side_effect=ValueError("bad query"),
        ):
            result = handler.handle(cmd)
        assert result.return_code == 2

    def test_research_generic_exception_returns_code_2(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query")
        with patch(
            "jarvis_engine.web_research.run_web_research",
            side_effect=RuntimeError("network error"),
        ):
            result = handler.handle(cmd)
        assert result.return_code == 2

    def test_max_results_clamped(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test", max_results=100, max_pages=100)
        with patch(
            "jarvis_engine.web_research.run_web_research",
            return_value={"summary_lines": [], "findings": []},
        ) as mock_fn:
            handler.handle(cmd)
        call_args = mock_fn.call_args
        # run_web_research is called with positional or keyword args
        max_results = call_args.kwargs.get("max_results")
        max_pages = call_args.kwargs.get("max_pages")
        if max_results is not None:
            assert max_results <= 20
        if max_pages is not None:
            assert max_pages <= 20

    def test_auto_ingest_with_summary_lines(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query", auto_ingest=True)
        mock_report = {
            "summary_lines": ["Finding 1"],
            "findings": [{"domain": "example.com"}],
        }
        with patch(
            "jarvis_engine.web_research.run_web_research",
            return_value=mock_report,
        ):
            with patch(
                "jarvis_engine.main._auto_ingest_memory",
                return_value="rec-123",
            ):
                result = handler.handle(cmd)
        assert result.return_code == 0
        assert result.auto_ingest_record_id == "rec-123"

    def test_auto_ingest_failure_handled_gracefully(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query", auto_ingest=True)
        mock_report = {
            "summary_lines": ["Finding 1"],
            "findings": [],
        }
        with patch(
            "jarvis_engine.web_research.run_web_research",
            return_value=mock_report,
        ):
            with patch(
                "jarvis_engine.main._auto_ingest_memory",
                side_effect=RuntimeError("ingest failed"),
            ):
                result = handler.handle(cmd)
        assert result.return_code == 0
        assert result.auto_ingest_record_id == ""

    def test_auto_ingest_disabled(self):
        handler = WebResearchHandler(root=Path("."))
        cmd = WebResearchCommand(query="test query", auto_ingest=False)
        mock_report = {
            "summary_lines": ["Finding 1"],
            "findings": [],
        }
        with patch(
            "jarvis_engine.web_research.run_web_research",
            return_value=mock_report,
        ):
            result = handler.handle(cmd)
        assert result.return_code == 0
        assert result.auto_ingest_record_id == ""


# ---------------------------------------------------------------------------
# QueryHandler: default_query_model from config
# ---------------------------------------------------------------------------


class TestQueryHandlerConfigModel:

    def test_no_classifier_uses_config_default_model(self):
        """QueryHandler uses config.default_query_model when no classifier is available."""
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = _gateway_response(
            model="custom-model-from-config"
        )

        handler = QueryHandler(gateway=mock_gateway, classifier=None)
        cmd = QueryCommand(query="what is the meaning of life")

        with patch(
            "jarvis_engine.config.load_config"
        ) as mock_load_config:
            mock_cfg = MagicMock()
            mock_cfg.default_query_model = "custom-model-from-config"
            mock_load_config.return_value = mock_cfg
            result = handler.handle(cmd)

        call_args = mock_gateway.complete.call_args
        assert call_args.kwargs.get("model", call_args[1].get("model")) == "custom-model-from-config"
        assert "Default" in result.route_reason

    def test_no_classifier_default_model_is_claude_sonnet(self):
        """Without config override, the default model should be claude-sonnet-4-5-20250929."""
        from jarvis_engine.config import EngineConfig

        cfg = EngineConfig()
        assert cfg.default_query_model == "claude-sonnet-4-5-20250929"
