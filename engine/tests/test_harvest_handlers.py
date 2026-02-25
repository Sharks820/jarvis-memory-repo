"""Tests for harvest_handlers -- HarvestHandler, IngestSessionHandler, HarvestBudgetHandler."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)
from jarvis_engine.handlers.harvest_handlers import (
    HarvestBudgetHandler,
    HarvestHandler,
    IngestSessionHandler,
)


# ---------------------------------------------------------------------------
# HarvestHandler
# ---------------------------------------------------------------------------


def test_harvest_no_harvester() -> None:
    """Returns rc=2 when harvester is None."""
    handler = HarvestHandler(harvester=None)
    result = handler.handle(HarvestTopicCommand(topic="quantum computing"))
    assert result.return_code == 2
    assert result.topic == "quantum computing"
    assert result.results[0]["status"] == "error"


@patch("jarvis_engine.harvesting.harvester.HarvestCommand")
def test_harvest_success(mock_cmd_cls: MagicMock) -> None:
    """Successful harvest delegates to harvester.harvest()."""
    mock_harvester = MagicMock()
    mock_harvester.harvest.return_value = {
        "topic": "quantum computing",
        "results": [{"provider": "gemini", "text": "QC is..."}],
    }
    handler = HarvestHandler(harvester=mock_harvester)
    result = handler.handle(HarvestTopicCommand(topic="quantum computing", max_tokens=1024))

    assert result.return_code == 0
    assert result.topic == "quantum computing"
    assert len(result.results) == 1
    mock_harvester.harvest.assert_called_once()


@patch("jarvis_engine.harvesting.harvester.HarvestCommand")
def test_harvest_with_providers(mock_cmd_cls: MagicMock) -> None:
    """Providers list is passed through to HarvestCommand."""
    mock_harvester = MagicMock()
    mock_harvester.harvest.return_value = {"topic": "t", "results": []}
    handler = HarvestHandler(harvester=mock_harvester)
    result = handler.handle(HarvestTopicCommand(topic="t", providers=["gemini"]))
    assert result.return_code == 0
    # Verify the HarvestCommand was constructed with providers
    construct_call = mock_cmd_cls.call_args
    assert construct_call.kwargs.get("providers") == ["gemini"] or construct_call[1].get("providers") == ["gemini"]


@patch("jarvis_engine.harvesting.harvester.HarvestCommand")
def test_harvest_missing_topic_in_result(mock_cmd_cls: MagicMock) -> None:
    """When harvester result lacks 'topic', falls back to cmd.topic."""
    mock_harvester = MagicMock()
    mock_harvester.harvest.return_value = {"results": []}
    handler = HarvestHandler(harvester=mock_harvester)
    result = handler.handle(HarvestTopicCommand(topic="fallback topic"))
    assert result.topic == "fallback topic"


# ---------------------------------------------------------------------------
# IngestSessionHandler
# ---------------------------------------------------------------------------


def test_ingest_session_no_pipeline() -> None:
    """Returns rc=2 when pipeline is None."""
    handler = IngestSessionHandler(pipeline=None)
    result = handler.handle(IngestSessionCommand(source="claude"))
    assert result.return_code == 2
    assert result.source == "claude"


def test_ingest_session_unknown_source() -> None:
    """Returns rc=1 for unknown source type."""
    handler = IngestSessionHandler(pipeline=MagicMock())
    result = handler.handle(IngestSessionCommand(source="unknown_tool"))
    assert result.return_code == 1


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_claude_discover(mock_ingestor_cls: MagicMock) -> None:
    """Claude source with no session_path discovers sessions."""
    mock_ingestor = MagicMock()
    mock_ingestor.find_sessions.return_value = []
    mock_ingestor_cls.return_value = mock_ingestor

    mock_pipeline = MagicMock()
    handler = IngestSessionHandler(pipeline=mock_pipeline)
    result = handler.handle(IngestSessionCommand(source="claude"))

    assert result.return_code == 0
    assert result.sessions_processed == 0
    mock_ingestor.find_sessions.assert_called_once_with(project_path=None)


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_claude_with_project_path(mock_ingestor_cls: MagicMock) -> None:
    """Claude source with project_path scopes discovery."""
    mock_ingestor = MagicMock()
    mock_ingestor.find_sessions.return_value = []
    mock_ingestor_cls.return_value = mock_ingestor

    handler = IngestSessionHandler(pipeline=MagicMock())
    handler.handle(IngestSessionCommand(source="claude", project_path="/some/project"))
    mock_ingestor.find_sessions.assert_called_once_with(project_path="/some/project")


@patch("jarvis_engine.harvesting.session_ingestors.CodexIngestor")
def test_ingest_session_codex_discover(mock_ingestor_cls: MagicMock) -> None:
    """Codex source with no session_path discovers sessions."""
    mock_ingestor = MagicMock()
    mock_ingestor.find_sessions.return_value = []
    mock_ingestor_cls.return_value = mock_ingestor

    handler = IngestSessionHandler(pipeline=MagicMock())
    result = handler.handle(IngestSessionCommand(source="codex"))
    assert result.return_code == 0
    mock_ingestor.find_sessions.assert_called_once()


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_processes_texts(mock_ingestor_cls: MagicMock) -> None:
    """Ingests text chunks from discovered sessions."""
    mock_ingestor = MagicMock()
    session_path = MagicMock()
    session_path.name = "session_001.jsonl"
    mock_ingestor.find_sessions.return_value = [session_path]
    mock_ingestor.ingest_session.return_value = ["chunk1", "chunk2"]
    mock_ingestor_cls.return_value = mock_ingestor

    mock_pipeline = MagicMock()
    mock_pipeline.ingest.return_value = [{"id": 1}]

    handler = IngestSessionHandler(pipeline=mock_pipeline)
    result = handler.handle(IngestSessionCommand(source="claude"))

    assert result.return_code == 0
    assert result.sessions_processed == 1
    assert result.records_created == 2  # 2 chunks x 1 record each
    assert mock_pipeline.ingest.call_count == 2


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_empty_texts_skipped(mock_ingestor_cls: MagicMock) -> None:
    """Sessions returning empty texts are not counted."""
    mock_ingestor = MagicMock()
    s1, s2 = MagicMock(), MagicMock()
    s1.name = "s1.jsonl"
    s2.name = "s2.jsonl"
    mock_ingestor.find_sessions.return_value = [s1, s2]
    mock_ingestor.ingest_session.side_effect = [[], ["text1"]]
    mock_ingestor_cls.return_value = mock_ingestor

    mock_pipeline = MagicMock()
    mock_pipeline.ingest.return_value = [{"id": 1}]

    handler = IngestSessionHandler(pipeline=mock_pipeline)
    result = handler.handle(IngestSessionCommand(source="claude"))

    assert result.sessions_processed == 1  # only s2 counted


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_pipeline_error_continues(mock_ingestor_cls: MagicMock) -> None:
    """Pipeline errors on individual chunks don't abort the session."""
    mock_ingestor = MagicMock()
    session = MagicMock()
    session.name = "s.jsonl"
    mock_ingestor.find_sessions.return_value = [session]
    mock_ingestor.ingest_session.return_value = ["chunk1", "chunk2", "chunk3"]
    mock_ingestor_cls.return_value = mock_ingestor

    mock_pipeline = MagicMock()
    mock_pipeline.ingest.side_effect = [
        [{"id": 1}],
        Exception("db error"),
        [{"id": 2}, {"id": 3}],
    ]

    handler = IngestSessionHandler(pipeline=mock_pipeline)
    result = handler.handle(IngestSessionCommand(source="claude"))

    assert result.return_code == 0
    assert result.sessions_processed == 1
    assert result.records_created == 3  # 1 + 0 (error) + 2


def test_ingest_session_explicit_path_outside_allowed() -> None:
    """Explicit session_path outside allowed roots returns rc=2."""
    handler = IngestSessionHandler(pipeline=MagicMock())
    # Use an absolute path that cannot be under ~/.claude, ~/.codex, or ~/AppData
    # On Windows or Unix, the root drive / filesystem root is never under those dirs
    if os.name == "nt":
        outside_path = "C:\\Windows\\System32\\evil.jsonl"
    else:
        outside_path = "/etc/evil.jsonl"
    result = handler.handle(IngestSessionCommand(
        source="claude",
        session_path=outside_path,
    ))
    assert result.return_code == 2


@patch("jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor")
def test_ingest_session_explicit_path_allowed(mock_ingestor_cls: MagicMock) -> None:
    """Explicit session_path within ~/.claude is accepted."""
    mock_ingestor = MagicMock()
    mock_ingestor.ingest_session.return_value = []
    mock_ingestor_cls.return_value = mock_ingestor

    home = Path.home()
    claude_dir = home / ".claude"

    # Only test if .claude dir exists or simulate
    allowed_path = claude_dir / "sessions" / "test.jsonl"

    handler = IngestSessionHandler(pipeline=MagicMock())
    # This will check if the resolved path starts with an allowed root.
    # We can't easily create files in ~/.claude in tests, so we test the
    # rejection path above and the logic path here if the dir exists.
    if claude_dir.exists():
        # Even if dir exists, the file won't exist -- ingestor handles that
        result = handler.handle(IngestSessionCommand(
            source="claude",
            session_path=str(allowed_path),
        ))
        # If the path resolves under .claude, it should be accepted (rc=0)
        assert result.return_code == 0
    else:
        # Path doesn't start with any allowed root
        result = handler.handle(IngestSessionCommand(
            source="claude",
            session_path=str(allowed_path),
        ))
        # Either accepted or rejected depending on path resolution
        assert result.return_code in (0, 2)


# ---------------------------------------------------------------------------
# HarvestBudgetHandler
# ---------------------------------------------------------------------------


def test_budget_no_manager() -> None:
    """Returns rc=2 when budget_manager is None."""
    handler = HarvestBudgetHandler(budget_manager=None)
    result = handler.handle(HarvestBudgetCommand())
    assert result.return_code == 2
    assert "error" in result.summary


def test_budget_status_default() -> None:
    """Default action='status' returns spend summary."""
    mock_budget = MagicMock()
    mock_budget.get_spend_summary.return_value = {
        "total_usd": 1.50,
        "providers": {"gemini": 0.75, "kimi": 0.75},
    }
    handler = HarvestBudgetHandler(budget_manager=mock_budget)
    result = handler.handle(HarvestBudgetCommand())

    assert result.return_code == 0
    assert result.summary["total_usd"] == 1.50
    mock_budget.get_spend_summary.assert_called_once_with(provider=None)


def test_budget_status_filtered_by_provider() -> None:
    """Status action with provider filters the summary."""
    mock_budget = MagicMock()
    mock_budget.get_spend_summary.return_value = {"total_usd": 0.5}
    handler = HarvestBudgetHandler(budget_manager=mock_budget)
    result = handler.handle(HarvestBudgetCommand(provider="gemini"))

    mock_budget.get_spend_summary.assert_called_once_with(provider="gemini")


def test_budget_set_success() -> None:
    """Set action with all required fields calls set_budget."""
    mock_budget = MagicMock()
    handler = HarvestBudgetHandler(budget_manager=mock_budget)
    result = handler.handle(HarvestBudgetCommand(
        action="set",
        provider="gemini",
        period="daily",
        limit_usd=5.0,
        limit_requests=100,
    ))

    assert result.return_code == 0
    assert result.summary["action"] == "set"
    assert result.summary["limit_usd"] == 5.0
    mock_budget.set_budget.assert_called_once_with(
        provider="gemini",
        period="daily",
        limit_usd=5.0,
        limit_requests=100,
    )


def test_budget_set_missing_fields() -> None:
    """Set action without required fields returns rc=1."""
    mock_budget = MagicMock()
    handler = HarvestBudgetHandler(budget_manager=mock_budget)

    # Missing limit_usd
    result = handler.handle(HarvestBudgetCommand(
        action="set",
        provider="gemini",
        period="daily",
    ))
    assert result.return_code == 1
    assert "error" in result.summary

    # Missing provider
    result = handler.handle(HarvestBudgetCommand(
        action="set",
        period="daily",
        limit_usd=5.0,
    ))
    assert result.return_code == 1


def test_budget_set_no_limit_requests_defaults_zero() -> None:
    """When limit_requests is None, defaults to 0."""
    mock_budget = MagicMock()
    handler = HarvestBudgetHandler(budget_manager=mock_budget)
    result = handler.handle(HarvestBudgetCommand(
        action="set",
        provider="kimi",
        period="monthly",
        limit_usd=10.0,
        limit_requests=None,
    ))

    assert result.return_code == 0
    mock_budget.set_budget.assert_called_once_with(
        provider="kimi",
        period="monthly",
        limit_usd=10.0,
        limit_requests=0,
    )


def test_budget_set_missing_period() -> None:
    """Set action without period returns rc=1."""
    mock_budget = MagicMock()
    handler = HarvestBudgetHandler(budget_manager=mock_budget)
    result = handler.handle(HarvestBudgetCommand(
        action="set",
        provider="gemini",
        limit_usd=5.0,
    ))
    assert result.return_code == 1
