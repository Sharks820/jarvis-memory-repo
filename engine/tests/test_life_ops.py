from __future__ import annotations

import json

from jarvis_engine.life_ops import build_daily_brief, load_snapshot, suggest_actions


def test_life_ops_brief_and_actions(tmp_path) -> None:
    snapshot_path = tmp_path / "ops.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "date": "2026-02-22",
                "tasks": [{"title": "Critical task", "priority": "high"}],
                "calendar_events": [{"title": "Board call", "prep_needed": "yes"}],
                "emails": [{"subject": "Urgent approval", "read": "false", "importance": "high"}],
                "bills": [{"name": "Power", "amount": "120", "status": "due"}],
                "subscriptions": [{"name": "ToolX", "monthly_cost": "n/a", "usage_score": "n/a"}],
                "medications": [{"name": "Rx A", "dose": "10mg", "status": "due"}],
                "school_items": [{"title": "Exam prep", "priority": "high"}],
                "family_items": [{"title": "Pickup child", "due_today": True}],
                "projects": [{"title": "Release build", "priority": "high"}],
            }
        ),
        encoding="utf-8",
    )
    snapshot = load_snapshot(snapshot_path)
    brief = build_daily_brief(snapshot)
    actions = suggest_actions(snapshot)

    assert "Jarvis Daily Brief for 2026-02-22" in brief
    assert "Urgent tasks: 1" in brief
    assert "Medications due: 1" in brief
    assert any("Critical task" in a for a in actions)
    assert any("Pay bill now" in a for a in actions)
    assert any("Take medication" in a for a in actions)
