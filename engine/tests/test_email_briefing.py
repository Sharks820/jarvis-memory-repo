"""Tests for email triage, narrative daily briefing, and OpsBriefHandler integration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jarvis_engine.life_ops import (
    OpsSnapshot,
    _assemble_data_summary,
    build_daily_brief,
    build_narrative_brief,
)
from jarvis_engine.ops_sync import _triage_email


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides: Any) -> OpsSnapshot:
    """Create a minimal OpsSnapshot with optional overrides."""
    defaults = {
        "date": "2026-02-23",
        "tasks": [],
        "calendar_events": [],
        "emails": [],
        "bills": [],
        "subscriptions": [],
        "medications": [],
        "school_items": [],
        "family_items": [],
        "projects": [],
    }
    defaults.update(overrides)
    return OpsSnapshot(**defaults)


def _make_snapshot_path(tmp_path: Path, **overrides: Any) -> Path:
    """Write a snapshot JSON file and return the path."""
    defaults = {
        "date": "2026-02-23",
        "tasks": [],
        "calendar_events": [],
        "emails": [],
        "bills": [],
        "subscriptions": [],
        "medications": [],
        "school_items": [],
        "family_items": [],
        "projects": [],
    }
    defaults.update(overrides)
    path = tmp_path / "ops_snapshot.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


@dataclass
class MockGatewayResponse:
    text: str
    model: str = "qwen3:14b"
    provider: str = "ollama"


# ---------------------------------------------------------------------------
# Email Triage Tests
# ---------------------------------------------------------------------------


class TestTriageEmail:
    def test_triage_email_high_subject(self) -> None:
        result = _triage_email("alice@example.com", "URGENT: server down")
        assert result == "high"

    def test_triage_email_high_sender(self) -> None:
        result = _triage_email("billing@company.com", "Monthly invoice")
        assert result == "high"

    def test_triage_email_normal(self) -> None:
        result = _triage_email("friend@gmail.com", "Weekend plans")
        assert result == "normal"

    def test_triage_email_payment_due(self) -> None:
        result = _triage_email("noreply@bank.com", "Payment due reminder")
        assert result == "high"

    def test_triage_email_security_sender(self) -> None:
        result = _triage_email("security@corp.com", "Login notification")
        assert result == "high"

    def test_triage_email_alert_sender(self) -> None:
        result = _triage_email("alert@monitoring.io", "CPU usage normal")
        assert result == "high"

    def test_triage_email_deadline_subject(self) -> None:
        result = _triage_email("boss@work.com", "Deadline approaching")
        assert result == "high"

    def test_triage_email_expiring_subject(self) -> None:
        result = _triage_email("service@example.com", "Your subscription is expiring soon")
        assert result == "high"

    def test_triage_email_overdue_subject(self) -> None:
        result = _triage_email("library@city.gov", "Books overdue notice")
        assert result == "high"

    def test_triage_email_case_insensitive(self) -> None:
        result = _triage_email("BILLING@COMPANY.COM", "monthly statement")
        assert result == "high"


# ---------------------------------------------------------------------------
# Narrative Briefing Tests
# ---------------------------------------------------------------------------


class TestBuildNarrativeBrief:
    def test_build_narrative_brief_no_gateway(self) -> None:
        snapshot = _make_snapshot(
            tasks=[{"title": "Test task", "priority": "high"}],
        )
        result = build_narrative_brief(snapshot, gateway=None)
        expected = build_daily_brief(snapshot)
        assert result == expected

    def test_build_narrative_brief_with_mock_gateway(self) -> None:
        snapshot = _make_snapshot()
        mock_gw = MagicMock()
        mock_gw.complete.return_value = MockGatewayResponse(
            text="Good morning, sir. Your schedule is clear today."
        )
        result = build_narrative_brief(snapshot, gateway=mock_gw)
        assert result == "Good morning, sir. Your schedule is clear today."
        mock_gw.complete.assert_called_once()
        call_kwargs = mock_gw.complete.call_args
        assert call_kwargs.kwargs.get("route_reason") == "daily_briefing_narrative"

    def test_build_narrative_brief_gateway_failure(self) -> None:
        snapshot = _make_snapshot(
            tasks=[{"title": "Important task", "priority": "urgent"}],
        )
        mock_gw = MagicMock()
        mock_gw.complete.side_effect = ConnectionError("Ollama down")
        result = build_narrative_brief(snapshot, gateway=mock_gw)
        expected = build_daily_brief(snapshot)
        assert result == expected

    def test_build_narrative_brief_empty_response(self) -> None:
        snapshot = _make_snapshot()
        mock_gw = MagicMock()
        mock_gw.complete.return_value = MockGatewayResponse(text="")
        result = build_narrative_brief(snapshot, gateway=mock_gw)
        expected = build_daily_brief(snapshot)
        assert result == expected

    def test_build_narrative_brief_with_memory_context(self) -> None:
        snapshot = _make_snapshot()
        mock_gw = MagicMock()
        mock_gw.complete.return_value = MockGatewayResponse(
            text="Based on your recent patterns..."
        )
        result = build_narrative_brief(
            snapshot, gateway=mock_gw, memory_context="User sleeps late on weekends"
        )
        assert result == "Based on your recent patterns..."
        call_args = mock_gw.complete.call_args
        prompt_content = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])[0]["content"]
        assert "User sleeps late on weekends" in prompt_content


# ---------------------------------------------------------------------------
# Data Summary Tests
# ---------------------------------------------------------------------------


class TestAssembleDataSummary:
    def test_assemble_data_summary_truncation(self) -> None:
        """Snapshot with 50 calendar events should truncate to 10."""
        events = [{"title": f"Event {i}", "time": f"{i % 24:02d}:00"} for i in range(50)]
        snapshot = _make_snapshot(calendar_events=events)
        summary = _assemble_data_summary(snapshot)
        # Count event lines (lines starting with "  - ")
        event_lines = [l for l in summary.splitlines() if l.strip().startswith("- Event ")]
        assert len(event_lines) <= 10

    def test_assemble_data_summary_includes_date(self) -> None:
        snapshot = _make_snapshot(date="2026-02-23")
        summary = _assemble_data_summary(snapshot)
        assert "2026-02-23" in summary

    def test_assemble_data_summary_includes_tasks(self) -> None:
        snapshot = _make_snapshot(
            tasks=[
                {"title": "Urgent fix", "priority": "urgent"},
                {"title": "Normal work", "priority": "normal"},
            ]
        )
        summary = _assemble_data_summary(snapshot)
        assert "Urgent fix" in summary
        assert "Tasks:" in summary

    def test_assemble_data_summary_includes_emails(self) -> None:
        snapshot = _make_snapshot(
            emails=[{"subject": "Critical alert", "importance": "high", "read": False}]
        )
        summary = _assemble_data_summary(snapshot)
        assert "Critical alert" in summary
        assert "Unread Emails:" in summary

    def test_assemble_data_summary_includes_medications(self) -> None:
        snapshot = _make_snapshot(
            medications=[{"name": "Rx A", "dose": "10mg", "status": "due"}]
        )
        summary = _assemble_data_summary(snapshot)
        assert "Rx A" in summary
        assert "Medications Due:" in summary

    def test_assemble_data_summary_includes_bills(self) -> None:
        snapshot = _make_snapshot(
            bills=[{"name": "Electric", "amount": 150.0, "status": "due"}]
        )
        summary = _assemble_data_summary(snapshot)
        assert "Electric" in summary
        assert "Bills Due:" in summary

    def test_assemble_data_summary_empty_snapshot(self) -> None:
        snapshot = _make_snapshot()
        summary = _assemble_data_summary(snapshot)
        assert "Date:" in summary
        # Should not have section headers for empty lists
        assert "Tasks:" not in summary
        assert "Unread Emails:" not in summary

    def test_assemble_data_summary_task_priority_sorting(self) -> None:
        snapshot = _make_snapshot(
            tasks=[
                {"title": "Low item", "priority": "low"},
                {"title": "Urgent item", "priority": "urgent"},
                {"title": "High item", "priority": "high"},
            ]
        )
        summary = _assemble_data_summary(snapshot)
        lines = summary.splitlines()
        task_lines = [l for l in lines if "[urgent]" in l or "[high]" in l or "[low]" in l]
        assert len(task_lines) == 3
        # Urgent should come before low
        urgent_idx = next(i for i, l in enumerate(task_lines) if "Urgent" in l)
        low_idx = next(i for i, l in enumerate(task_lines) if "Low" in l)
        assert urgent_idx < low_idx


# ---------------------------------------------------------------------------
# OpsBriefHandler Tests
# ---------------------------------------------------------------------------


class TestOpsBriefHandler:
    def test_ops_brief_handler_without_gateway(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.ops_handlers import OpsBriefHandler
        from jarvis_engine.commands.ops_commands import OpsBriefCommand

        snapshot_path = _make_snapshot_path(
            tmp_path,
            tasks=[{"title": "Task A", "priority": "high"}],
            emails=[{"subject": "Alert", "read": False, "importance": "high"}],
        )
        handler = OpsBriefHandler(tmp_path, gateway=None)
        cmd = OpsBriefCommand(snapshot_path=snapshot_path, output_path=None)
        result = handler.handle(cmd)
        assert "Jarvis Daily Brief" in result.brief
        assert "Urgent tasks: 1" in result.brief

    def test_ops_brief_handler_with_gateway(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.ops_handlers import OpsBriefHandler
        from jarvis_engine.commands.ops_commands import OpsBriefCommand

        snapshot_path = _make_snapshot_path(tmp_path)
        mock_gw = MagicMock()
        mock_gw.complete.return_value = MockGatewayResponse(
            text="Good morning. All clear today."
        )
        handler = OpsBriefHandler(tmp_path, gateway=mock_gw)
        cmd = OpsBriefCommand(snapshot_path=snapshot_path, output_path=None)
        result = handler.handle(cmd)
        assert result.brief == "Good morning. All clear today."

    def test_ops_brief_handler_gateway_error_falls_back(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.ops_handlers import OpsBriefHandler
        from jarvis_engine.commands.ops_commands import OpsBriefCommand

        snapshot_path = _make_snapshot_path(tmp_path)
        mock_gw = MagicMock()
        mock_gw.complete.side_effect = RuntimeError("Gateway crashed")
        handler = OpsBriefHandler(tmp_path, gateway=mock_gw)
        cmd = OpsBriefCommand(snapshot_path=snapshot_path, output_path=None)
        result = handler.handle(cmd)
        assert "Jarvis Daily Brief" in result.brief


# ---------------------------------------------------------------------------
# Backward Compatibility Tests
# ---------------------------------------------------------------------------


class TestExistingBuildDailyBriefUnchanged:
    def test_existing_build_daily_brief_unchanged(self) -> None:
        """Replicate existing test_life_ops.py data to prove build_daily_brief is untouched."""
        snapshot = _make_snapshot(
            date="2026-02-22",
            tasks=[{"title": "Critical task", "priority": "high"}],
            calendar_events=[{"title": "Board call", "prep_needed": "yes"}],
            emails=[{"subject": "Urgent approval", "read": "false", "importance": "high"}],
            bills=[{"name": "Power", "amount": "120", "status": "due"}],
            subscriptions=[{"name": "ToolX", "monthly_cost": "n/a", "usage_score": "n/a"}],
            medications=[{"name": "Rx A", "dose": "10mg", "status": "due"}],
            school_items=[{"title": "Exam prep", "priority": "high"}],
            family_items=[{"title": "Pickup child", "due_today": True}],
            projects=[{"title": "Release build", "priority": "high"}],
        )
        brief = build_daily_brief(snapshot)
        assert "Jarvis Daily Brief for 2026-02-22" in brief
        assert "Urgent tasks: 1" in brief
        assert "Medications due: 1" in brief
        assert "Important unread emails: 1" in brief
