"""Time-aware trigger rules for proactive notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


def check_medication_reminders(snapshot_data: dict, _now: datetime | None = None) -> list[str]:
    """Check medications list for items with due_time within 30 minutes of now."""
    alerts: list[str] = []
    medications = snapshot_data.get("medications", [])
    # Use local time since medication due_times are in local HH:MM format
    now = _now or datetime.now()

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
            # Handle midnight crossing: if due_time appears to be far in the past,
            # it's actually tomorrow (e.g., now=23:50, due=00:05 -> +15min not -1425min)
            if diff_minutes < -720:  # More than 12 hours in the past = tomorrow
                diff_minutes += 1440  # Add 24 hours
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
    # Use local time: calendar events are typically stored in local time
    now = datetime.now().astimezone()

    for event in events:
        if not event.get("prep_needed", False):
            continue
        start_str = event.get("start_time", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                # Treat naive timestamps as local time (not UTC)
                start_dt = start_dt.astimezone()
            diff_hours = (start_dt - now).total_seconds() / 3600.0
            if 0 <= diff_hours <= 2:
                title = event.get("title", "event")
                minutes = max(1, int(diff_hours * 60))
                alerts.append(f"Calendar prep: {title} starts in {minutes} minutes")
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


def check_contact_neglect(snapshot_data: dict) -> list[str]:
    """Check for important contacts not contacted in a long time."""
    alerts: list[str] = []
    contacts = snapshot_data.get("contacts", [])
    neglect_days = int(snapshot_data.get("neglect_threshold_days", 14))
    now = datetime.now()

    for contact in contacts:
        importance = float(contact.get("importance", 0))
        if importance < 0.4:
            continue
        last_contact_str = contact.get("last_contact_date", "")
        if not last_contact_str:
            continue
        try:
            last_dt = datetime.fromisoformat(last_contact_str)
            if last_dt.tzinfo is not None:
                last_dt = last_dt.replace(tzinfo=None)
            days_since = (now - last_dt).days
            # Scale threshold by importance — more important = shorter threshold
            adjusted_threshold = max(7, int(neglect_days * (1.0 - importance * 0.5)))
            if days_since >= adjusted_threshold:
                name = contact.get("name", "someone")
                last_topic = contact.get("last_topic", "")
                msg = f"You haven't talked to {name} in {days_since} days"
                if last_topic:
                    msg += f" — last discussed: {last_topic}"
                alerts.append(msg)
        except (ValueError, TypeError):
            continue

    return alerts[:3]  # Cap at 3 to avoid alert fatigue


def check_meeting_prep_intelligence(snapshot_data: dict) -> list[str]:
    """Check for upcoming meetings that need KG-powered intelligence briefing."""
    alerts: list[str] = []
    events = snapshot_data.get("calendar_events", [])
    now = datetime.now().astimezone()

    for event in events:
        start_str = event.get("start_time", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.astimezone()
            diff_minutes = (start_dt - now).total_seconds() / 60.0
            # Fire 10-15 minutes before meeting starts
            if 5 <= diff_minutes <= 15:
                title = event.get("title", "meeting")
                attendees = event.get("attendees", [])
                location = event.get("location", "")
                msg = f"Meeting in {int(diff_minutes)} min: {title}"
                if attendees:
                    names = ", ".join(str(a) for a in attendees[:3])
                    msg += f" with {names}"
                if location:
                    msg += f" at {location}"
                # Add context hints from KG if available
                context = event.get("kg_context", "")
                if context:
                    msg += f" | Context: {context}"
                alerts.append(msg)
        except (ValueError, TypeError):
            continue

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
    TriggerRule(
        rule_id="contact_neglect",
        description="Nudge about important contacts you haven't talked to",
        check_fn=check_contact_neglect,
        cooldown_minutes=720,  # 12 hours
    ),
    TriggerRule(
        rule_id="meeting_intelligence",
        description="Pre-meeting intelligence briefing with KG context",
        check_fn=check_meeting_prep_intelligence,
        cooldown_minutes=10,
    ),
]
