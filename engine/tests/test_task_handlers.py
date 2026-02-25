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
    mock_classifier.classify.assert_called_once_with("what time is it")


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
