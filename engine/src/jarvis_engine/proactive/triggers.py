"""Time-aware trigger rules for proactive notifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class TriggerRule:
    """A single proactive trigger rule."""

    rule_id: str
    description: str
    check_fn: Callable[[dict], list[str]]
    cooldown_minutes: int = 60


@dataclass
class TriggerAlert:
    """An alert fired by a trigger rule."""

    rule_id: str
    message: str
    priority: str = "normal"
    timestamp: str = ""


def check_medication_reminders(snapshot_data: dict) -> list[str]:
    """Check medications list for items with due_time within 30 minutes of now."""
    alerts: list[str] = []
    medications = snapshot_data.get("medications", [])
    # Use local time since medication due_times are in local HH:MM format
    now = datetime.now()

    for med in medications:
        due_time_str = med.get("due_time", "")
        if not due_time_str:
            continue
        try:
            # Parse HH:MM format, use today's date in local time
            parts = due_time_str.split(":")
            due_hour, due_min = int(parts[0]), int(parts[1])
            due_dt = now.replace(hour=due_hour, minute=due_min, second=0, microsecond=0)
            diff_minutes = (due_dt - now).total_seconds() / 60.0
            if 0 <= diff_minutes <= 30:
                name = med.get("name", "medication")
                alerts.append(f"Medication reminder: {name} due at {due_time_str}")
        except (ValueError, IndexError):
            continue

    return alerts


def check_bill_due_alerts(snapshot_data: dict) -> list[str]:
    """Check bills for status=due or overdue."""
    alerts: list[str] = []
    bills = snapshot_data.get("bills", [])

    for bill in bills:
        status = bill.get("status", "").lower()
        if status in ("due", "overdue"):
            name = bill.get("name", "bill")
            amount = bill.get("amount", "")
            msg = f"Bill alert: {name} is {status}"
            if amount:
                msg += f" (${amount})"
            alerts.append(msg)

    return alerts


def check_calendar_prep(snapshot_data: dict) -> list[str]:
    """Check calendar_events for prep_needed=true within next 2 hours."""
    alerts: list[str] = []
    events = snapshot_data.get("calendar_events", [])
    now = datetime.now(timezone.utc)

    for event in events:
        if not event.get("prep_needed", False):
            continue
        start_str = event.get("start_time", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            diff_hours = (start_dt - now).total_seconds() / 3600.0
            if 0 <= diff_hours <= 2:
                title = event.get("title", "event")
                alerts.append(f"Calendar prep: {title} starts in {int(diff_hours * 60)} minutes")
        except (ValueError, TypeError):
            continue

    return alerts


def check_urgent_tasks(snapshot_data: dict) -> list[str]:
    """Check tasks for priority=high or urgent."""
    alerts: list[str] = []
    tasks = snapshot_data.get("tasks", [])

    for task in tasks:
        priority = task.get("priority", "").lower()
        if priority in ("high", "urgent"):
            title = task.get("title", "task")
            alerts.append(f"Urgent task: {title} (priority: {priority})")

    return alerts


DEFAULT_TRIGGER_RULES: list[TriggerRule] = [
    TriggerRule(
        rule_id="medication_reminder",
        description="Remind about medications due within 30 minutes",
        check_fn=check_medication_reminders,
        cooldown_minutes=30,
    ),
    TriggerRule(
        rule_id="bill_due_alert",
        description="Alert about bills that are due or overdue",
        check_fn=check_bill_due_alerts,
        cooldown_minutes=360,
    ),
    TriggerRule(
        rule_id="calendar_prep",
        description="Remind about events needing preparation within 2 hours",
        check_fn=check_calendar_prep,
        cooldown_minutes=120,
    ),
    TriggerRule(
        rule_id="urgent_task_alert",
        description="Alert about high/urgent priority tasks",
        check_fn=check_urgent_tasks,
        cooldown_minutes=180,
    ),
]
