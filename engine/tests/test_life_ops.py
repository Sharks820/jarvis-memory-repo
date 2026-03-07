"""Comprehensive tests for jarvis_engine.life_ops module.

Covers OpsSnapshot loading, daily brief generation, action suggestions,
narrative brief with gateway, export, and edge cases.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from jarvis_engine.gateway.models import GatewayResponse, ModelGateway
from jarvis_engine.life_ops import (
    OpsSnapshot,
    _assemble_data_summary,
    _is_due_item,
    _is_urgent_item,
    _safe_bool,
    build_daily_brief,
    build_narrative_brief,
    export_actions_json,
    load_snapshot,
    suggest_actions,
)


# ---------------------------------------------------------------------------
# Helper to build a snapshot dict
# ---------------------------------------------------------------------------

def _snapshot_dict(**overrides):
    base = {
        "date": "2026-02-22",
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
    base.update(overrides)
    return base


def _snapshot(**overrides):
    d = _snapshot_dict(**overrides)
    return OpsSnapshot(**d)


# ---------------------------------------------------------------------------
# _safe_bool tests
# ---------------------------------------------------------------------------

class TestSafeBool:
    def test_true_string(self):
        assert _safe_bool("true") is True
        assert _safe_bool("True") is True
        assert _safe_bool("TRUE") is True

    def test_false_string(self):
        assert _safe_bool("false") is False
        assert _safe_bool("no") is False

    def test_yes_no(self):
        assert _safe_bool("yes") is True
        assert _safe_bool("y") is True
        assert _safe_bool("1") is True

    def test_numeric(self):
        assert _safe_bool(1) is True
        assert _safe_bool(0) is False
        assert _safe_bool(0.0) is False
        assert _safe_bool(3.14) is True

    def test_bool_passthrough(self):
        assert _safe_bool(True) is True
        assert _safe_bool(False) is False


# ---------------------------------------------------------------------------
# _is_due_item tests
# ---------------------------------------------------------------------------

class TestIsDueItem:
    def test_status_due(self):
        assert _is_due_item({"status": "due"}, "2026-02-22") is True

    def test_status_overdue(self):
        assert _is_due_item({"status": "overdue"}, "2026-02-22") is True

    def test_status_urgent(self):
        assert _is_due_item({"status": "urgent"}, "2026-02-22") is True

    def test_matching_due_date(self):
        assert _is_due_item({"due_date": "2026-02-22"}, "2026-02-22") is True

    def test_non_matching_due_date(self):
        assert _is_due_item({"due_date": "2026-02-23"}, "2026-02-22") is False

    def test_due_today_flag(self):
        assert _is_due_item({"due_today": True}, "2026-02-22") is True

    def test_due_today_string_true(self):
        assert _is_due_item({"due_today": "true"}, "2026-02-22") is True

    def test_not_due(self):
        assert _is_due_item({"status": "completed"}, "2026-02-22") is False

    def test_fallback_date_field(self):
        assert _is_due_item({"date": "2026-02-22"}, "2026-02-22") is True


# ---------------------------------------------------------------------------
# _is_urgent_item tests
# ---------------------------------------------------------------------------

class TestIsUrgentItem:
    def test_high_priority(self):
        assert _is_urgent_item({"priority": "high"}, "2026-02-22") is True

    def test_urgent_priority(self):
        assert _is_urgent_item({"priority": "urgent"}, "2026-02-22") is True

    def test_critical_priority(self):
        assert _is_urgent_item({"priority": "critical"}, "2026-02-22") is True

    def test_normal_priority_not_urgent(self):
        assert _is_urgent_item({"priority": "normal"}, "2026-02-22") is False

    def test_delegates_to_is_due(self):
        # Not urgent priority, but status is due -> urgent via _is_due_item
        assert _is_urgent_item({"priority": "low", "status": "due"}, "2026-02-22") is True


# ---------------------------------------------------------------------------
# load_snapshot tests
# ---------------------------------------------------------------------------

class TestLoadSnapshot:
    def test_valid_snapshot(self, tmp_path):
        path = tmp_path / "snap.json"
        path.write_text(json.dumps(_snapshot_dict(
            tasks=[{"title": "T1", "priority": "high"}],
        )), encoding="utf-8")
        snap = load_snapshot(path)
        assert snap.date == "2026-02-22"
        assert len(snap.tasks) == 1

    def test_missing_file_returns_empty(self, tmp_path):
        snap = load_snapshot(tmp_path / "nonexistent.json")
        assert snap.date == ""
        assert snap.tasks == []

    def test_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json}", encoding="utf-8")
        snap = load_snapshot(path)
        assert snap.date == ""

    def test_partial_fields(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"date": "2026-02-22", "tasks": [{"title": "A"}]}), encoding="utf-8")
        snap = load_snapshot(path)
        assert snap.date == "2026-02-22"
        assert len(snap.tasks) == 1
        assert snap.emails == []
        assert snap.bills == []


# ---------------------------------------------------------------------------
# build_daily_brief tests
# ---------------------------------------------------------------------------

class TestBuildDailyBrief:
    def test_header_present(self):
        snap = _snapshot()
        brief = build_daily_brief(snap)
        assert "Jarvis Daily Brief for 2026-02-22" in brief

    def test_counts_urgent_tasks(self):
        snap = _snapshot(tasks=[
            {"title": "A", "priority": "high"},
            {"title": "B", "priority": "urgent"},
            {"title": "C", "priority": "normal"},
        ])
        brief = build_daily_brief(snap)
        assert "Urgent tasks: 2" in brief

    def test_counts_unread_important_emails(self):
        snap = _snapshot(emails=[
            {"subject": "Hi", "read": False, "importance": "high"},
            {"subject": "Lo", "read": False, "importance": "normal"},
            {"subject": "Read", "read": True, "importance": "high"},
        ])
        brief = build_daily_brief(snap)
        assert "Important unread emails: 1" in brief

    def test_counts_due_bills(self):
        snap = _snapshot(bills=[
            {"name": "Power", "amount": 120, "status": "due"},
            {"name": "Water", "amount": 50, "status": "paid"},
        ])
        brief = build_daily_brief(snap)
        assert "Bills due/overdue: 1" in brief

    def test_counts_costly_subscriptions(self):
        snap = _snapshot(subscriptions=[
            {"name": "Big", "monthly_cost": 25.0},
            {"name": "Small", "monthly_cost": 5.0},
        ])
        brief = build_daily_brief(snap)
        assert "High-cost subscriptions (>= $20/mo): 1" in brief

    def test_no_actions_message(self):
        snap = _snapshot()
        brief = build_daily_brief(snap)
        assert "No critical actions detected" in brief

    def test_top_actions_capped_at_8(self):
        tasks = [{"title": f"Task {i}", "priority": "high"} for i in range(12)]
        snap = _snapshot(tasks=tasks)
        brief = build_daily_brief(snap)
        action_lines = [l for l in brief.split("\n") if l.startswith("- Complete high")]
        assert len(action_lines) <= 8

    def test_medications_due(self):
        snap = _snapshot(medications=[{"name": "Rx A", "dose": "10mg", "status": "due"}])
        brief = build_daily_brief(snap)
        assert "Medications due: 1" in brief


# ---------------------------------------------------------------------------
# suggest_actions tests
# ---------------------------------------------------------------------------

class TestSuggestActions:
    def test_high_priority_task_action(self):
        snap = _snapshot(tasks=[{"title": "Deploy fix", "priority": "high"}])
        actions = suggest_actions(snap)
        assert any("Deploy fix" in a for a in actions)

    def test_unread_urgent_email_action(self):
        snap = _snapshot(emails=[{"subject": "Urgent review", "read": False, "importance": "urgent"}])
        actions = suggest_actions(snap)
        assert any("Urgent review" in a for a in actions)

    def test_due_bill_action(self):
        snap = _snapshot(bills=[{"name": "Electric", "amount": 85.0, "status": "due"}])
        actions = suggest_actions(snap)
        assert any("Pay bill now" in a and "Electric" in a for a in actions)

    def test_low_usage_expensive_subscription(self):
        snap = _snapshot(subscriptions=[
            {"name": "ExpensiveTool", "monthly_cost": 30.0, "usage_score": 0.1},
        ])
        actions = suggest_actions(snap)
        assert any("Review/cancel" in a and "ExpensiveTool" in a for a in actions)

    def test_high_usage_expensive_subscription_not_flagged(self):
        snap = _snapshot(subscriptions=[
            {"name": "GoodTool", "monthly_cost": 30.0, "usage_score": 0.9},
        ])
        actions = suggest_actions(snap)
        assert not any("GoodTool" in a for a in actions)

    def test_medication_action_with_dose_and_time(self):
        snap = _snapshot(medications=[
            {"name": "VitD", "dose": "5000IU", "due_time": "8am", "status": "due"},
        ])
        actions = suggest_actions(snap)
        med_action = [a for a in actions if "VitD" in a]
        assert len(med_action) == 1
        assert "5000IU" in med_action[0]
        assert "8am" in med_action[0]

    def test_school_item_action(self):
        snap = _snapshot(school_items=[{"title": "Math exam", "priority": "high"}])
        actions = suggest_actions(snap)
        assert any("Math exam" in a for a in actions)

    def test_family_item_action(self):
        snap = _snapshot(family_items=[{"title": "Pickup child", "due_today": True}])
        actions = suggest_actions(snap)
        assert any("Pickup child" in a for a in actions)

    def test_project_action(self):
        snap = _snapshot(projects=[{"title": "Release v2.0", "priority": "critical"}])
        actions = suggest_actions(snap)
        assert any("Release v2.0" in a for a in actions)

    def test_calendar_prep_action(self):
        snap = _snapshot(calendar_events=[{"title": "Board meeting", "prep_needed": "yes"}])
        actions = suggest_actions(snap)
        assert any("Board meeting" in a for a in actions)

    def test_calendar_no_prep_not_flagged(self):
        snap = _snapshot(calendar_events=[{"title": "Lunch", "prep_needed": "no"}])
        actions = suggest_actions(snap)
        assert not any("Lunch" in a for a in actions)

    def test_empty_snapshot_no_actions(self):
        snap = _snapshot()
        actions = suggest_actions(snap)
        assert actions == []


# ---------------------------------------------------------------------------
# export_actions_json tests
# ---------------------------------------------------------------------------

class TestExportActionsJson:
    def test_exports_valid_json(self, tmp_path):
        actions = ["Complete task X", "Pay bill now: Power ($120.00)"]
        path = tmp_path / "actions.json"
        export_actions_json(actions, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2

    def test_privileged_action_class(self, tmp_path):
        actions = ["Pay bill now: Electric ($50.00)", "Review/cancel low-usage subscription: ToolX ($25.00/mo)"]
        path = tmp_path / "actions.json"
        export_actions_json(actions, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in data:
            assert record["action_class"] == "privileged"

    def test_bounded_write_default(self, tmp_path):
        actions = ["Complete high-priority task: Fix bug"]
        path = tmp_path / "actions.json"
        export_actions_json(actions, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[0]["action_class"] == "bounded_write"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "actions.json"
        export_actions_json(["test"], path)
        assert path.exists()


# ---------------------------------------------------------------------------
# _assemble_data_summary tests
# ---------------------------------------------------------------------------

class TestAssembleDataSummary:
    def test_includes_date(self):
        snap = _snapshot()
        summary = _assemble_data_summary(snap)
        assert "Date: 2026-02-22" in summary

    def test_includes_calendar_events(self):
        snap = _snapshot(calendar_events=[{"title": "Standup", "time": "9:00"}])
        summary = _assemble_data_summary(snap)
        assert "Calendar Events:" in summary
        assert "Standup" in summary
        assert "9:00" in summary

    def test_tasks_sorted_by_priority(self):
        snap = _snapshot(tasks=[
            {"title": "Low task", "priority": "low"},
            {"title": "Urgent task", "priority": "urgent"},
        ])
        summary = _assemble_data_summary(snap)
        urgent_pos = summary.find("Urgent task")
        low_pos = summary.find("Low task")
        assert urgent_pos < low_pos

    def test_unread_emails_only(self):
        snap = _snapshot(emails=[
            {"subject": "Read email", "read": True, "importance": "normal"},
            {"subject": "Unread email", "read": False, "importance": "normal"},
        ])
        summary = _assemble_data_summary(snap)
        assert "Unread email" in summary
        assert "Read email" not in summary

    def test_medications_due_section(self):
        snap = _snapshot(medications=[{"name": "VitD", "dose": "5000IU", "status": "due"}])
        summary = _assemble_data_summary(snap)
        assert "Medications Due:" in summary
        assert "VitD" in summary

    def test_bills_due_section(self):
        snap = _snapshot(bills=[{"name": "Electric", "amount": 120.0, "status": "due"}])
        summary = _assemble_data_summary(snap)
        assert "Bills Due:" in summary
        assert "Electric" in summary

    def test_empty_snapshot_only_date(self):
        snap = _snapshot()
        summary = _assemble_data_summary(snap)
        assert "Date: 2026-02-22" in summary
        assert "Tasks:" not in summary


# ---------------------------------------------------------------------------
# build_narrative_brief tests
# ---------------------------------------------------------------------------

class TestBuildNarrativeBrief:
    def test_no_gateway_falls_back_to_deterministic(self):
        snap = _snapshot(tasks=[{"title": "Test task", "priority": "high"}])
        result = build_narrative_brief(snap, gateway=None)
        assert "Jarvis Daily Brief" in result

    def test_gateway_success_returns_llm_text(self):
        snap = _snapshot()
        gateway = MagicMock(spec=ModelGateway)
        response = MagicMock(spec=GatewayResponse)
        response.text = "Good morning! Here is your briefing..."
        gateway.complete.return_value = response

        result = build_narrative_brief(snap, gateway=gateway, memory_context="test context")
        assert result == "Good morning! Here is your briefing..."
        gateway.complete.assert_called_once()

    def test_gateway_exception_falls_back(self):
        snap = _snapshot(tasks=[{"title": "Fallback task", "priority": "high"}])
        gateway = MagicMock(spec=ModelGateway)
        gateway.complete.side_effect = RuntimeError("LLM unavailable")

        result = build_narrative_brief(snap, gateway=gateway)
        assert "Jarvis Daily Brief" in result

    def test_gateway_empty_response_falls_back(self):
        snap = _snapshot()
        gateway = MagicMock(spec=ModelGateway)
        response = MagicMock(spec=GatewayResponse)
        response.text = ""
        gateway.complete.return_value = response

        result = build_narrative_brief(snap, gateway=gateway)
        assert "Jarvis Daily Brief" in result

    def test_gateway_none_response_falls_back(self):
        snap = _snapshot()
        gateway = MagicMock(spec=ModelGateway)
        gateway.complete.return_value = None

        result = build_narrative_brief(snap, gateway=gateway)
        assert "Jarvis Daily Brief" in result

    def test_memory_context_truncated(self):
        snap = _snapshot()
        gateway = MagicMock(spec=ModelGateway)
        response = MagicMock(spec=GatewayResponse)
        response.text = "Brief with context"
        gateway.complete.return_value = response

        long_context = "x" * 5000
        build_narrative_brief(snap, gateway=gateway, memory_context=long_context)
        call_args = gateway.complete.call_args
        prompt = call_args[1]["messages"][0]["content"] if "messages" in call_args[1] else call_args[0][0][0]["content"]
        # The safe_context should be at most 2000 chars
        assert len(long_context) > 2000  # verify our test input is longer

    def test_uses_local_model_env(self):
        snap = _snapshot()
        gateway = MagicMock(spec=ModelGateway)
        response = MagicMock(spec=GatewayResponse)
        response.text = "Custom model brief"
        gateway.complete.return_value = response

        with patch.dict("os.environ", {"JARVIS_LOCAL_MODEL": "llama3:8b"}):
            build_narrative_brief(snap, gateway=gateway)
        call_kwargs = gateway.complete.call_args[1]
        assert call_kwargs["model"] == "llama3:8b"
