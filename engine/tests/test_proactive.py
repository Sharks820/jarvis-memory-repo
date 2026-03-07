"""Tests for proactive intelligence: triggers, notifications, engine, and wake word."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


from jarvis_engine.proactive.triggers import (
    DEFAULT_TRIGGER_RULES,
    TriggerAlert,
    TriggerRule,
    check_bill_due_alerts,
    check_calendar_prep,
    check_medication_reminders,
    check_urgent_tasks,
)
from jarvis_engine.proactive.notifications import Notifier
from jarvis_engine.proactive import ProactiveEngine


# ---------- Task 1: Trigger rules ----------


class TestTriggerRuleDefaults:
    def test_trigger_rule_defaults(self):
        rule = TriggerRule(
            rule_id="test",
            description="A test rule",
            check_fn=lambda d: [],
        )
        assert rule.rule_id == "test"
        assert rule.description == "A test rule"
        assert rule.cooldown_minutes == 60


class TestCheckMedicationReminders:
    def test_medication_due_within_30_min(self):
        # Use local time since medication due_times are local HH:MM
        now = datetime.now()
        due_time = (now + timedelta(minutes=15)).strftime("%H:%M")
        snapshot = {
            "medications": [
                {"name": "Vitamin D", "due_time": due_time},
            ]
        }
        alerts = check_medication_reminders(snapshot)
        assert len(alerts) == 1
        assert "Vitamin D" in alerts[0]

    def test_medication_not_due(self):
        now = datetime.now()
        due_time = (now + timedelta(hours=3)).strftime("%H:%M")
        snapshot = {
            "medications": [
                {"name": "Vitamin D", "due_time": due_time},
            ]
        }
        alerts = check_medication_reminders(snapshot)
        assert len(alerts) == 0

    def test_empty_medications(self):
        alerts = check_medication_reminders({})
        assert alerts == []


class TestCheckBillDueAlerts:
    def test_bill_due(self):
        snapshot = {
            "bills": [
                {"name": "Electric", "status": "due", "amount": "150"},
            ]
        }
        alerts = check_bill_due_alerts(snapshot)
        assert len(alerts) == 1
        assert "Electric" in alerts[0]
        assert "due" in alerts[0]

    def test_bill_paid(self):
        snapshot = {
            "bills": [
                {"name": "Electric", "status": "paid"},
            ]
        }
        alerts = check_bill_due_alerts(snapshot)
        assert len(alerts) == 0

    def test_bill_overdue(self):
        snapshot = {
            "bills": [
                {"name": "Water", "status": "overdue", "amount": "75"},
            ]
        }
        alerts = check_bill_due_alerts(snapshot)
        assert len(alerts) == 1
        assert "overdue" in alerts[0]


class TestCheckCalendarPrep:
    def test_event_prep_needed(self):
        now = datetime.now(timezone.utc)
        start = (now + timedelta(hours=1)).isoformat()
        snapshot = {
            "calendar_events": [
                {"title": "Team Meeting", "prep_needed": True, "time": start},
            ]
        }
        alerts = check_calendar_prep(snapshot)
        assert len(alerts) == 1
        assert "Team Meeting" in alerts[0]

    def test_event_prep_needed_start_time_fallback(self):
        """Backward compat: start_time field still works when time is absent."""
        now = datetime.now(timezone.utc)
        start = (now + timedelta(hours=1)).isoformat()
        snapshot = {
            "calendar_events": [
                {"title": "Standup", "prep_needed": True, "start_time": start},
            ]
        }
        alerts = check_calendar_prep(snapshot)
        assert len(alerts) == 1
        assert "Standup" in alerts[0]

    def test_event_no_prep(self):
        now = datetime.now(timezone.utc)
        start = (now + timedelta(hours=1)).isoformat()
        snapshot = {
            "calendar_events": [
                {"title": "Lunch", "prep_needed": False, "time": start},
            ]
        }
        alerts = check_calendar_prep(snapshot)
        assert len(alerts) == 0


class TestCheckUrgentTasks:
    def test_high_priority_task(self):
        snapshot = {
            "tasks": [
                {"title": "Fix critical bug", "priority": "high"},
            ]
        }
        alerts = check_urgent_tasks(snapshot)
        assert len(alerts) == 1
        assert "Fix critical bug" in alerts[0]

    def test_normal_priority_task(self):
        snapshot = {
            "tasks": [
                {"title": "Update docs", "priority": "normal"},
            ]
        }
        alerts = check_urgent_tasks(snapshot)
        assert len(alerts) == 0

    def test_urgent_priority_task(self):
        snapshot = {
            "tasks": [
                {"title": "Security patch", "priority": "urgent"},
            ]
        }
        alerts = check_urgent_tasks(snapshot)
        assert len(alerts) == 1
        assert "urgent" in alerts[0]


# ---------- Task 2: Notifications ----------


class TestNotifier:
    def test_notifier_send_graceful_no_winotify(self):
        """winotify unavailable should return False, no exception."""
        notifier = Notifier()
        with patch.dict(sys.modules, {"winotify": None}):
            result = notifier.send("Test", "Hello")
            # Returns False because winotify import fails
            assert result is False

    def test_notifier_send_batch(self):
        notifier = Notifier()
        alerts = [
            TriggerAlert(rule_id="test_1", message="Alert 1", timestamp=""),
            TriggerAlert(rule_id="test_2", message="Alert 2", timestamp=""),
        ]
        # Mock send to always succeed
        notifier.send = MagicMock(return_value=True)
        count = notifier.send_batch(alerts)
        assert count == 2
        assert notifier.send.call_count == 2


# ---------- Task 3: ProactiveEngine ----------


class TestProactiveEngine:
    def _make_engine(self):
        notifier = Notifier()
        notifier.send = MagicMock(return_value=True)

        def check_always(data):
            return ["Test alert"]

        rules = [
            TriggerRule(
                rule_id="test_rule",
                description="Always fires",
                check_fn=check_always,
                cooldown_minutes=5,
            )
        ]
        return ProactiveEngine(rules=rules, notifier=notifier), notifier

    def test_evaluate_fires_alerts(self):
        engine, notifier = self._make_engine()
        alerts = engine.evaluate({"some": "data"})
        assert len(alerts) == 1
        assert alerts[0].rule_id == "test_rule"
        assert alerts[0].message == "Test alert"

    def test_cooldown_respected(self):
        engine, _ = self._make_engine()
        # First evaluation fires
        alerts1 = engine.evaluate({})
        assert len(alerts1) == 1

        # Second evaluation within cooldown should not fire
        alerts2 = engine.evaluate({})
        assert len(alerts2) == 0

    def test_cooldown_expired(self):
        engine, _ = self._make_engine()
        # First evaluation fires
        alerts1 = engine.evaluate({})
        assert len(alerts1) == 1

        # Manually set last_fired to past time (beyond cooldown)
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        engine._last_fired["test_rule"] = past

        # Should fire again
        alerts2 = engine.evaluate({})
        assert len(alerts2) == 1

    def test_reset_cooldowns(self):
        engine, _ = self._make_engine()
        engine.evaluate({})
        assert len(engine._last_fired) > 0

        engine.reset_cooldowns()
        assert len(engine._last_fired) == 0

        # Should fire again after reset
        alerts = engine.evaluate({})
        assert len(alerts) == 1


# ---------- Task 4: Wake word ----------


class TestWakeWordDetector:
    def test_graceful_no_openwakeword(self):
        """openwakeword not installed should not crash."""
        from jarvis_engine.wakeword import WakeWordDetector

        detector = WakeWordDetector()
        callback = MagicMock()

        # Mock openwakeword import to fail
        with patch.dict(
            sys.modules, {"openwakeword": None, "openwakeword.model": None}
        ):
            # start should return without error
            import threading

            stop = threading.Event()
            stop.set()  # set immediately so loop exits
            detector.start(callback, stop)
            # callback should not have been called
            callback.assert_not_called()


# ---------- DEFAULT_TRIGGER_RULES ----------


class TestDefaultTriggerRules:
    def test_default_trigger_rules_count(self):
        assert len(DEFAULT_TRIGGER_RULES) == 6

    def test_rule_ids(self):
        ids = {r.rule_id for r in DEFAULT_TRIGGER_RULES}
        assert "medication_reminder" in ids
        assert "bill_due_alert" in ids
        assert "calendar_prep" in ids
        assert "urgent_task_alert" in ids
        assert "contact_neglect" in ids
        assert "meeting_intelligence" in ids

    def test_cooldowns(self):
        cooldowns = {r.rule_id: r.cooldown_minutes for r in DEFAULT_TRIGGER_RULES}
        assert cooldowns["medication_reminder"] == 30
        assert cooldowns["bill_due_alert"] == 360
        assert cooldowns["calendar_prep"] == 120
        assert cooldowns["urgent_task_alert"] == 180
        assert cooldowns["contact_neglect"] == 720
        assert cooldowns["meeting_intelligence"] == 10


# ---------- Handlers (command bus integration) ----------


class TestProactiveCheckHandler:
    def test_handler_no_engine(self):
        from jarvis_engine.handlers.proactive_handlers import ProactiveCheckHandler
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand

        from pathlib import Path

        handler = ProactiveCheckHandler(Path("."))
        result = handler.handle(ProactiveCheckCommand())
        assert "not available" in result.message

    def test_handler_snapshot_not_found(self):
        from jarvis_engine.handlers.proactive_handlers import ProactiveCheckHandler
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand

        from pathlib import Path

        mock_engine = MagicMock()
        handler = ProactiveCheckHandler(Path("."), proactive_engine=mock_engine)
        result = handler.handle(
            ProactiveCheckCommand(snapshot_path="./nonexistent.json")
        )
        assert "not found" in result.message

    def test_handler_snapshot_path_traversal(self):
        from jarvis_engine.handlers.proactive_handlers import ProactiveCheckHandler
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand

        from pathlib import Path

        mock_engine = MagicMock()
        handler = ProactiveCheckHandler(Path("."), proactive_engine=mock_engine)
        result = handler.handle(ProactiveCheckCommand(snapshot_path="/etc/passwd"))
        assert "outside" in result.message.lower()


class TestWakeWordStartHandler:
    def test_handler_starts(self):
        from jarvis_engine.handlers.proactive_handlers import WakeWordStartHandler
        from jarvis_engine.commands.proactive_commands import WakeWordStartCommand

        from pathlib import Path

        with patch("jarvis_engine.wakeword.WakeWordDetector") as mock_cls:
            mock_detector = MagicMock()
            mock_cls.return_value = mock_detector
            handler = WakeWordStartHandler(Path("."))
            result = handler.handle(WakeWordStartCommand(threshold=0.6))
            assert result.started is True
            assert "started" in result.message
