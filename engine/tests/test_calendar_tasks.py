"""Tests for ICS calendar parsing, calendar loading, and task source loading."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from jarvis_engine.ops_sync import (
    _parse_ics,
    _parse_ics_fallback,
    load_calendar_events,
    load_task_items,
)


def _has_icalendar() -> bool:
    try:
        import icalendar  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Helpers: ICS test data builders
# ---------------------------------------------------------------------------


def _make_ics(events_block: str) -> str:
    """Wrap VEVENT blocks in a minimal VCALENDAR envelope."""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//Test//EN\r\n"
        f"{events_block}"
        "END:VCALENDAR\r\n"
    )


def _simple_event_ics(
    summary: str = "Team Standup",
    dtstart: str = "20260301T090000Z",
    location: str = "Room 42",
    description: str = "Daily standup meeting",
) -> str:
    """Build ICS text with a single simple event (1 hour duration)."""
    # Compute DTEND as 1 hour after DTSTART by incrementing the hour portion
    hour = int(dtstart[9:11])
    dtend = f"{dtstart[:9]}{hour + 1:02d}{dtstart[11:]}"
    block = (
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtend}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"LOCATION:{location}\r\n"
        f"DESCRIPTION:{description}\r\n"
        "END:VEVENT\r\n"
    )
    return _make_ics(block)


def _allday_event_ics(
    summary: str = "Company Holiday", dtstart: str = "20260301"
) -> str:
    """Build ICS text with an all-day event (DATE, not DATETIME)."""
    block = (
        "BEGIN:VEVENT\r\n"
        f"DTSTART;VALUE=DATE:{dtstart}\r\n"
        f"DTEND;VALUE=DATE:{dtstart}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
    )
    return _make_ics(block)


def _recurring_event_ics(
    summary: str = "Daily Scrum",
    dtstart: str = "20260301T100000Z",
    rrule: str = "FREQ=DAILY;COUNT=5",
) -> str:
    """Build ICS text with a recurring event."""
    block = (
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtstart[:8]}T110000Z\r\n"
        f"SUMMARY:{summary}\r\n"
        f"RRULE:{rrule}\r\n"
        "END:VEVENT\r\n"
    )
    return _make_ics(block)


# ---------------------------------------------------------------------------
# ICS Parsing Tests
# ---------------------------------------------------------------------------


class TestParseIcsSimpleEvent:
    def test_returns_one_event(self) -> None:
        text = _simple_event_ics()
        events = _parse_ics(text, target_date=date(2026, 3, 1))
        assert len(events) == 1

    def test_event_fields(self) -> None:
        text = _simple_event_ics(summary="Team Standup", location="Room 42")
        events = _parse_ics(text, target_date=date(2026, 3, 1))
        e = events[0]
        assert e["title"] == "Team Standup"
        assert e["time"] == "09:00"
        assert e["location"] == "Room 42"
        assert e["prep_needed"] == "yes"


class TestParseIcsAlldayEvent:
    def test_allday_time_field(self) -> None:
        text = _allday_event_ics(summary="Company Holiday", dtstart="20260301")
        events = _parse_ics(text, target_date=date(2026, 3, 1))
        assert len(events) == 1
        assert events[0]["time"] == "all-day"
        assert events[0]["title"] == "Company Holiday"


class TestParseIcsRecurringEvent:
    @pytest.mark.skipif(
        not _has_icalendar(),
        reason="icalendar not installed -- fallback parser cannot expand RRULE",
    )
    def test_recurring_event_within_range(self) -> None:
        text = _recurring_event_ics(
            summary="Daily Scrum",
            dtstart="20260301T100000Z",
            rrule="FREQ=DAILY;COUNT=5",
        )
        # March 3 is day 3 of 5 -- should appear
        events = _parse_ics(text, target_date=date(2026, 3, 3))
        assert len(events) == 1
        assert events[0]["title"] == "Daily Scrum"
        assert events[0]["time"] == "10:00"


class TestParseIcsNoEventsOutsideRange:
    def test_no_events_for_distant_date(self) -> None:
        text = _simple_event_ics(dtstart="20260301T090000Z")
        events = _parse_ics(text, target_date=date(2026, 6, 15))
        assert events == []


class TestParseIcsFallbackWhenLibraryMissing:
    def test_fallback_on_import_error(self, monkeypatch) -> None:
        """When icalendar is not importable, _parse_ics falls back to line-by-line parser."""
        # Create a fake module that raises ImportError on import
        original_import = (
            __builtins__.__import__
            if hasattr(__builtins__, "__import__")
            else __import__
        )

        def fake_import(name, *args, **kwargs):
            if name in ("icalendar", "recurring_ical_events"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)

        # Clear cached modules so the lazy import retries
        monkeypatch.delitem(sys.modules, "icalendar", raising=False)
        monkeypatch.delitem(sys.modules, "recurring_ical_events", raising=False)

        text = _simple_event_ics(summary="Fallback Test", dtstart="20260301T090000Z")
        events = _parse_ics(text, target_date=date(2026, 3, 1))
        # Fallback parser returns events but without date filtering or recurring expansion
        assert len(events) >= 1
        assert events[0]["title"] == "Fallback Test"


class TestParseIcsEmptyString:
    def test_empty_string_returns_empty(self) -> None:
        assert _parse_ics("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert _parse_ics("   \n  \r\n  ") == []


# ---------------------------------------------------------------------------
# Calendar Loader Tests
# ---------------------------------------------------------------------------


class TestLoadCalendarEventsJsonFeed:
    def test_loads_from_json_env(self, monkeypatch, tmp_path: Path) -> None:
        cal_json = tmp_path / "cal.json"
        cal_json.write_text(
            json.dumps([{"title": "JSON Event", "time": "10:00"}]),
            encoding="utf-8",
        )
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", str(cal_json))
        monkeypatch.delenv("JARVIS_CALENDAR_ICS_FILE", raising=False)
        monkeypatch.delenv("JARVIS_CALENDAR_ICS_URL", raising=False)

        events = load_calendar_events()
        assert len(events) == 1
        assert events[0]["title"] == "JSON Event"


class TestLoadCalendarEventsIcsFile:
    def test_loads_from_ics_file(self, monkeypatch, tmp_path: Path) -> None:
        ics_file = tmp_path / "cal.ics"
        ics_file.write_text(
            _simple_event_ics(summary="ICS File Event", dtstart="20260301T140000Z"),
            encoding="utf-8",
        )
        monkeypatch.delenv("JARVIS_CALENDAR_JSON", raising=False)
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", str(ics_file))
        monkeypatch.delenv("JARVIS_CALENDAR_ICS_URL", raising=False)

        events = load_calendar_events(target_date=date(2026, 3, 1))
        assert len(events) == 1
        assert events[0]["title"] == "ICS File Event"
        assert events[0]["time"] == "14:00"


class TestLoadCalendarEventsNoConfig:
    def test_no_env_vars_returns_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_CALENDAR_JSON", raising=False)
        monkeypatch.delenv("JARVIS_CALENDAR_ICS_FILE", raising=False)
        monkeypatch.delenv("JARVIS_CALENDAR_ICS_URL", raising=False)

        events = load_calendar_events()
        assert events == []


# ---------------------------------------------------------------------------
# Task Source Tests
# ---------------------------------------------------------------------------


class TestLoadTaskItemsDefaultJson:
    def test_loads_from_default_path(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_TASK_SOURCE", raising=False)
        monkeypatch.delenv("JARVIS_TASKS_JSON", raising=False)

        planning = tmp_path / ".planning"
        planning.mkdir()
        tasks_file = planning / "tasks.json"
        tasks_file.write_text(
            json.dumps(
                [
                    {
                        "title": "Fix bug",
                        "priority": "high",
                        "due_date": "2026-03-01",
                        "status": "pending",
                    },
                    {
                        "title": "Write docs",
                        "priority": "normal",
                        "due_date": "2026-03-02",
                        "status": "pending",
                    },
                ]
            ),
            encoding="utf-8",
        )

        tasks = load_task_items(tmp_path)
        assert len(tasks) == 2
        assert tasks[0]["title"] == "Fix bug"
        assert tasks[1]["title"] == "Write docs"


class TestLoadTaskItemsEnvPath:
    def test_loads_from_env_json_path(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_TASK_SOURCE", raising=False)

        custom_file = tmp_path / "custom_tasks.json"
        custom_file.write_text(
            json.dumps(
                [{"title": "Custom Task", "priority": "urgent", "status": "pending"}]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("JARVIS_TASKS_JSON", str(custom_file))

        tasks = load_task_items(tmp_path)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Custom Task"


class TestLoadTaskItemsTodoistNoToken:
    def test_todoist_without_token_returns_empty(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "todoist")
        monkeypatch.delenv("JARVIS_TODOIST_TOKEN", raising=False)

        tasks = load_task_items(tmp_path)
        assert tasks == []


class TestLoadTaskItemsGoogleTasksStub:
    def test_google_tasks_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "google_tasks")

        tasks = load_task_items(tmp_path)
        assert tasks == []


class TestLoadTaskItemsNoFile:
    def test_missing_json_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_TASK_SOURCE", raising=False)
        monkeypatch.delenv("JARVIS_TASKS_JSON", raising=False)

        # No tasks.json file exists
        tasks = load_task_items(tmp_path)
        assert tasks == []


# ---------------------------------------------------------------------------
# Fallback parser standalone test
# ---------------------------------------------------------------------------


class TestParseIcsFallback:
    def test_fallback_parses_simple_vevent(self) -> None:
        text = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "SUMMARY:Fallback Event\n"
            "DTSTART:20260301T090000Z\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = _parse_ics_fallback(text)
        assert len(events) == 1
        assert events[0]["title"] == "Fallback Event"
        assert events[0]["time"] == "09:00"
        assert events[0]["prep_needed"] == "yes"
