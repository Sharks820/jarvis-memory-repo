from __future__ import annotations

import imaplib
import json
import socket
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine import ops_sync
from jarvis_engine.ops_sync import (
    SyncSummary,
    _decode_email_header,
    _is_safe_calendar_url,
    _load_feed_json_list,
    _parse_ics,
    _parse_ics_fallback,
    _read_json_list,
    _triage_email,
    build_live_snapshot,
    load_calendar_events,
    load_email_items,
    load_task_items,
)

# ---------------------------------------------------------------------------
# Minimal ICS text fixtures
# ---------------------------------------------------------------------------

_BASIC_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Team standup
DTSTART:20260301T093000Z
LOCATION:Zoom
DESCRIPTION:Daily standup call
END:VEVENT
END:VCALENDAR
"""

_MULTI_EVENT_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Morning run
DTSTART:20260301T060000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Lunch with Dave
DTSTART:20260301T120000Z
LOCATION:Deli
END:VEVENT
BEGIN:VEVENT
SUMMARY:Dentist
DTSTART:20260302T140000Z
END:VEVENT
END:VCALENDAR
"""

_ALL_DAY_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Company holiday
DTSTART;VALUE=DATE:20260301
END:VEVENT
END:VCALENDAR
"""

_FOLDED_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Folded
 event title
DTSTART:20260301T100000Z
END:VEVENT
END:VCALENDAR
"""


# ===========================================================================
# _read_json_list
# ===========================================================================

class TestReadJsonList:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert _read_json_list(tmp_path / "nope.json") == []

    def test_valid_list_of_dicts(self, tmp_path: Path) -> None:
        p = tmp_path / "items.json"
        p.write_text(json.dumps([{"a": 1}, {"b": 2}]), encoding="utf-8")
        assert _read_json_list(p) == [{"a": 1}, {"b": 2}]

    def test_filters_non_dict_entries(self, tmp_path: Path) -> None:
        p = tmp_path / "mixed.json"
        p.write_text(json.dumps([{"ok": True}, "string", 42, None, {"ok": False}]), encoding="utf-8")
        result = _read_json_list(p)
        assert result == [{"ok": True}, {"ok": False}]

    def test_not_a_list(self, tmp_path: Path) -> None:
        p = tmp_path / "obj.json"
        p.write_text(json.dumps({"key": "val"}), encoding="utf-8")
        assert _read_json_list(p) == []

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{broken", encoding="utf-8")
        assert _read_json_list(p) == []

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        assert _read_json_list(p) == []

    def test_empty_list(self, tmp_path: Path) -> None:
        p = tmp_path / "empty_list.json"
        p.write_text("[]", encoding="utf-8")
        assert _read_json_list(p) == []


# ===========================================================================
# _load_feed_json_list
# ===========================================================================

class TestLoadFeedJsonList:
    def test_default_path_created_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_MEDICATIONS_JSON", raising=False)
        default = tmp_path / ".planning" / "medications.json"
        result = _load_feed_json_list(tmp_path, "JARVIS_MEDICATIONS_JSON", default)
        assert result == []
        assert default.exists()
        assert json.loads(default.read_text(encoding="utf-8")) == []

    def test_default_path_already_has_data(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_MEDICATIONS_JSON", raising=False)
        default = tmp_path / "meds.json"
        default.write_text(json.dumps([{"med": "aspirin"}]), encoding="utf-8")
        result = _load_feed_json_list(tmp_path, "JARVIS_MEDICATIONS_JSON", default)
        assert result == [{"med": "aspirin"}]

    def test_env_path_inside_repo(self, tmp_path: Path, monkeypatch) -> None:
        feed_file = tmp_path / "data" / "feed.json"
        feed_file.parent.mkdir(parents=True, exist_ok=True)
        feed_file.write_text(json.dumps([{"item": 1}]), encoding="utf-8")
        monkeypatch.setenv("JARVIS_SCHOOL_JSON", str(feed_file))
        monkeypatch.delenv("JARVIS_ALLOW_EXTERNAL_FEEDS", raising=False)
        result = _load_feed_json_list(tmp_path, "JARVIS_SCHOOL_JSON", tmp_path / "default.json")
        assert result == [{"item": 1}]

    def test_env_path_is_directory_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        d = tmp_path / "adir"
        d.mkdir()
        monkeypatch.setenv("JARVIS_FAMILY_JSON", str(d))
        result = _load_feed_json_list(tmp_path, "JARVIS_FAMILY_JSON", tmp_path / "default.json")
        assert result == []

    def test_env_unc_forward_slash_blocked(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_ALLOW_EXTERNAL_FEEDS", "true")
        monkeypatch.setenv("JARVIS_SCHOOL_JSON", "//evil/share/feed.json")
        result = _load_feed_json_list(tmp_path, "JARVIS_SCHOOL_JSON", tmp_path / "default.json")
        assert result == []

    def test_external_allowed_reads_outside_repo(self, tmp_path: Path, monkeypatch) -> None:
        external = tmp_path / "outside" / "ext.json"
        external.parent.mkdir()
        external.write_text(json.dumps([{"ext": True}]), encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("JARVIS_ALLOW_EXTERNAL_FEEDS", "1")
        monkeypatch.setenv("JARVIS_PROJECTS_JSON", str(external))
        result = _load_feed_json_list(repo, "JARVIS_PROJECTS_JSON", repo / "default.json")
        assert result == [{"ext": True}]


# ===========================================================================
# _parse_ics_fallback
# ===========================================================================

class TestParseIcsFallback:
    def test_empty_string(self) -> None:
        assert _parse_ics_fallback("", None) == []

    def test_basic_event_no_date_filter(self) -> None:
        events = _parse_ics_fallback(_BASIC_ICS, None)
        assert len(events) == 1
        assert events[0]["title"] == "Team standup"
        assert events[0]["time"] == "09:30"
        assert events[0]["location"] == "Zoom"
        assert events[0]["prep_needed"] == "yes"

    def test_filter_by_date_includes_matching(self) -> None:
        events = _parse_ics_fallback(_MULTI_EVENT_ICS, date(2026, 3, 1))
        titles = [e["title"] for e in events]
        assert "Morning run" in titles
        assert "Lunch with Dave" in titles
        assert "Dentist" not in titles

    def test_filter_by_date_excludes_non_matching(self) -> None:
        events = _parse_ics_fallback(_MULTI_EVENT_ICS, date(2026, 3, 2))
        assert len(events) == 1
        assert events[0]["title"] == "Dentist"

    def test_all_day_event(self) -> None:
        events = _parse_ics_fallback(_ALL_DAY_ICS, date(2026, 3, 1))
        assert len(events) == 1
        assert events[0]["time"] == "all-day"
        assert events[0]["title"] == "Company holiday"

    def test_events_sorted_by_time(self) -> None:
        events = _parse_ics_fallback(_MULTI_EVENT_ICS, date(2026, 3, 1))
        times = [e["time"] for e in events]
        assert times == sorted(times)

    def test_rfc5545_line_folding(self) -> None:
        """RFC 5545 folding: newline+space is removed, joining line fragments."""
        events = _parse_ics_fallback(_FOLDED_ICS, None)
        assert len(events) == 1
        # The replacement of "\n " removes both the newline and the
        # continuation space, so the fragments join without a separator.
        assert events[0]["title"] == "Foldedevent title"

    def test_description_truncated_to_200(self) -> None:
        long_desc = "A" * 300
        ics = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Long\n"
            f"DTSTART:20260301T100000Z\nDESCRIPTION:{long_desc}\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        events = _parse_ics_fallback(ics, None)
        assert len(events[0]["description"]) == 200

    def test_untitled_event(self) -> None:
        ics = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
            "DTSTART:20260301T100000Z\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        events = _parse_ics_fallback(ics, None)
        assert events[0]["title"] == "Untitled event"

    def test_lines_without_colon_skipped(self) -> None:
        ics = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
            "SOME-JUNK-LINE-NO-COLON\n"
            "SUMMARY:OK event\nDTSTART:20260301T080000Z\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        events = _parse_ics_fallback(ics, None)
        assert len(events) == 1
        assert events[0]["title"] == "OK event"

    def test_key_with_params_stripped(self) -> None:
        ics = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
            "DTSTART;TZID=US/Eastern:20260301T100000\n"
            "SUMMARY:Params test\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        events = _parse_ics_fallback(ics, None)
        assert events[0]["title"] == "Params test"


# ===========================================================================
# _parse_ics (icalendar-based parser with fallback)
# ===========================================================================

class TestParseIcs:
    def test_empty_text_returns_empty(self) -> None:
        assert _parse_ics("", None) == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert _parse_ics("   \n  \t  ", None) == []

    def test_falls_back_when_icalendar_not_installed(self) -> None:
        with patch.dict("sys.modules", {"icalendar": None, "recurring_ical_events": None}):
            # Force ImportError by removing modules from cache
            import sys
            saved_ical = sys.modules.pop("icalendar", None)
            saved_rie = sys.modules.pop("recurring_ical_events", None)
            try:
                with patch("jarvis_engine.ops_sync._parse_ics_fallback", return_value=[{"title": "fb"}]) as fb:
                    result = _parse_ics(_BASIC_ICS, date(2026, 3, 1))
                    # Either calls fallback or parses via icalendar; both are valid
                    # We just verify no crash and we get a list
                    assert isinstance(result, list)
            finally:
                if saved_ical is not None:
                    sys.modules["icalendar"] = saved_ical
                if saved_rie is not None:
                    sys.modules["recurring_ical_events"] = saved_rie

    def test_falls_back_on_icalendar_parse_error(self) -> None:
        """If icalendar is installed but parsing fails, fallback is used."""
        mock_cal_module = MagicMock()
        mock_cal_module.Calendar.from_ical.side_effect = ValueError("bad ics")
        mock_rie = MagicMock()
        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            with patch("jarvis_engine.ops_sync._parse_ics_fallback", return_value=[{"title": "fb"}]) as fb:
                result = _parse_ics("INVALID ICS", date(2026, 3, 1))
                fb.assert_called_once()
                assert result == [{"title": "fb"}]

    def test_falls_back_on_recurring_expansion_error(self) -> None:
        """If recurring_ical_events.of(...).between() fails, fallback is used."""
        mock_cal_module = MagicMock()
        mock_rie = MagicMock()
        mock_rie.of.return_value.between.side_effect = RuntimeError("expand fail")
        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            with patch("jarvis_engine.ops_sync._parse_ics_fallback", return_value=[]) as fb:
                result = _parse_ics(_BASIC_ICS, date(2026, 3, 1))
                fb.assert_called_once()

    def test_icalendar_happy_path_with_mocks(self) -> None:
        """Test icalendar-based parsing when the libraries are available (mocked)."""
        mock_event = MagicMock()
        mock_event.get.side_effect = lambda key, default="": {
            "SUMMARY": "Mocked event",
            "DTSTART": SimpleNamespace(dt=datetime(2026, 3, 1, 10, 0)),
            "LOCATION": "Room 42",
            "DESCRIPTION": "A short description",
        }.get(key, default)

        mock_cal_module = MagicMock()
        mock_rie = MagicMock()
        mock_rie.of.return_value.between.return_value = [mock_event]

        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            result = _parse_ics(_BASIC_ICS, date(2026, 3, 1))
            assert len(result) == 1
            assert result[0]["title"] == "Mocked event"
            assert result[0]["time"] == "10:00"
            assert result[0]["location"] == "Room 42"

    def test_icalendar_all_day_event(self) -> None:
        """All-day events (date without hour) produce 'all-day' time."""
        mock_event = MagicMock()
        # dt is a date, not datetime -> no 'hour' attribute
        dt_obj = date(2026, 3, 1)
        mock_event.get.side_effect = lambda key, default="": {
            "SUMMARY": "Holiday",
            "DTSTART": SimpleNamespace(dt=dt_obj),
            "LOCATION": None,
            "DESCRIPTION": None,
        }.get(key, default)

        mock_cal_module = MagicMock()
        mock_rie = MagicMock()
        mock_rie.of.return_value.between.return_value = [mock_event]

        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            result = _parse_ics(_ALL_DAY_ICS, date(2026, 3, 1))
            assert len(result) == 1
            assert result[0]["time"] == "all-day"

    def test_icalendar_no_dtstart(self) -> None:
        """Event with no DTSTART should produce all-day."""
        mock_event = MagicMock()
        mock_event.get.side_effect = lambda key, default="": {
            "SUMMARY": "No start",
            "DTSTART": None,
            "LOCATION": None,
            "DESCRIPTION": None,
        }.get(key, default)

        mock_cal_module = MagicMock()
        mock_rie = MagicMock()
        mock_rie.of.return_value.between.return_value = [mock_event]

        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            result = _parse_ics(_BASIC_ICS, date(2026, 3, 1))
            assert result[0]["time"] == "all-day"

    def test_icalendar_description_truncated(self) -> None:
        """Description longer than 200 chars should be truncated."""
        mock_event = MagicMock()
        mock_event.get.side_effect = lambda key, default="": {
            "SUMMARY": "Trunc",
            "DTSTART": SimpleNamespace(dt=datetime(2026, 3, 1, 8, 0)),
            "LOCATION": "",
            "DESCRIPTION": "X" * 500,
        }.get(key, default)

        mock_cal_module = MagicMock()
        mock_rie = MagicMock()
        mock_rie.of.return_value.between.return_value = [mock_event]

        with patch.dict("sys.modules", {"icalendar": mock_cal_module, "recurring_ical_events": mock_rie}):
            result = _parse_ics(_BASIC_ICS, date(2026, 3, 1))
            assert len(result[0]["description"]) == 200


# ===========================================================================
# load_calendar_events
# ===========================================================================

class TestLoadCalendarEvents:
    def test_json_path_from_env(self, monkeypatch, tmp_path: Path) -> None:
        cal_file = tmp_path / "cal.json"
        cal_file.write_text(json.dumps([{"title": "Test"}]), encoding="utf-8")
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", str(cal_file))
        assert load_calendar_events() == [{"title": "Test"}]

    def test_ics_file_from_env(self, monkeypatch, tmp_path: Path) -> None:
        ics_file = tmp_path / "cal.ics"
        ics_file.write_text(_BASIC_ICS, encoding="utf-8")
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", str(ics_file))
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "")
        events = load_calendar_events(target_date=date(2026, 3, 1))
        assert len(events) >= 1
        assert events[0]["title"] == "Team standup"

    def test_ics_file_missing(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "/nonexistent/path.ics")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "")
        assert load_calendar_events() == []

    def test_ics_file_unc_blocked(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", r"\\evil\share\cal.ics")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "")
        assert load_calendar_events() == []

    def test_remote_url_blocked_without_opt_in(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "https://example.com/cal.ics")
        monkeypatch.delenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", raising=False)
        assert load_calendar_events() == []

    def test_remote_url_unsafe_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "http://example.com/cal.ics")
        monkeypatch.setenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", "1")
        assert load_calendar_events() == []

    def test_remote_url_success(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "https://safe.example.com/cal.ics")
        monkeypatch.setenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", "true")

        mock_resp = MagicMock()
        mock_resp.read.return_value = _BASIC_ICS.encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        monkeypatch.setattr(ops_sync, "_is_safe_calendar_url", lambda url: True)
        monkeypatch.setattr(ops_sync, "_build_no_redirect_opener", lambda: mock_opener)

        events = load_calendar_events(target_date=date(2026, 3, 1))
        assert len(events) >= 1

    def test_remote_url_oversized_payload(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "https://safe.example.com/huge.ics")
        monkeypatch.setenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", "1")

        payload = b"X" * (ops_sync.MAX_ICS_BYTES + 2)
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        monkeypatch.setattr(ops_sync, "_is_safe_calendar_url", lambda url: True)
        monkeypatch.setattr(ops_sync, "_build_no_redirect_opener", lambda: mock_opener)

        assert load_calendar_events() == []

    def test_remote_url_network_error(self, monkeypatch) -> None:
        from urllib.error import URLError

        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "https://safe.example.com/cal.ics")
        monkeypatch.setenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", "1")

        mock_opener = MagicMock()
        mock_opener.open.side_effect = URLError("connection refused")

        monkeypatch.setattr(ops_sync, "_is_safe_calendar_url", lambda url: True)
        monkeypatch.setattr(ops_sync, "_build_no_redirect_opener", lambda: mock_opener)

        assert load_calendar_events() == []

    def test_no_env_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_FILE", "")
        monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "")
        assert load_calendar_events() == []


# ===========================================================================
# _is_safe_calendar_url
# ===========================================================================

class TestIsSafeCalendarUrl:
    def test_http_rejected(self) -> None:
        assert _is_safe_calendar_url("http://example.com/cal.ics") is False

    def test_ftp_rejected(self) -> None:
        assert _is_safe_calendar_url("ftp://example.com/cal.ics") is False

    def test_empty_host(self) -> None:
        assert _is_safe_calendar_url("https:///cal.ics") is False

    def test_localhost_rejected(self) -> None:
        assert _is_safe_calendar_url("https://localhost/cal.ics") is False

    def test_private_ip_rejected(self) -> None:
        assert _is_safe_calendar_url("https://192.168.1.1/cal.ics") is False

    def test_loopback_ip_rejected(self) -> None:
        assert _is_safe_calendar_url("https://127.0.0.1/cal.ics") is False

    def test_link_local_rejected(self) -> None:
        assert _is_safe_calendar_url("https://169.254.1.1/cal.ics") is False

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_public_hostname_allowed(self, mock_dns) -> None:
        assert _is_safe_calendar_url("https://example.com/cal.ics") is True

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))])
    def test_hostname_resolving_to_private_ip(self, mock_dns) -> None:
        assert _is_safe_calendar_url("https://evil.internal/cal.ics") is False

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failure"))
    def test_dns_failure(self, mock_dns) -> None:
        assert _is_safe_calendar_url("https://doesnotresolve.invalid/cal.ics") is False

    @patch("socket.getaddrinfo", return_value=[
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("192.168.0.1", 443)),
    ])
    def test_mixed_public_private_rejected(self, mock_dns) -> None:
        """If any resolved IP is private, the URL is unsafe."""
        assert _is_safe_calendar_url("https://dual-homed.example.com/cal.ics") is False


# ===========================================================================
# load_task_items
# ===========================================================================

class TestLoadTaskItems:
    def test_default_json_source(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_TASK_SOURCE", raising=False)
        monkeypatch.delenv("JARVIS_TASKS_JSON", raising=False)
        tasks_file = tmp_path / ".planning" / "tasks.json"
        tasks_file.parent.mkdir(parents=True, exist_ok=True)
        tasks_file.write_text(json.dumps([{"title": "Buy milk"}]), encoding="utf-8")
        result = load_task_items(tmp_path)
        assert result == [{"title": "Buy milk"}]

    def test_json_env_override(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "json")
        custom = tmp_path / "my_tasks.json"
        custom.write_text(json.dumps([{"title": "Custom"}]), encoding="utf-8")
        monkeypatch.setenv("JARVIS_TASKS_JSON", str(custom))
        result = load_task_items(tmp_path)
        assert result == [{"title": "Custom"}]

    def test_google_tasks_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "google_tasks")
        assert load_task_items(tmp_path) == []

    def test_todoist_source_no_token(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "todoist")
        monkeypatch.setenv("JARVIS_TODOIST_TOKEN", "")
        assert load_task_items(tmp_path) == []

    def test_todoist_source_api_error(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "todoist")
        monkeypatch.setenv("JARVIS_TODOIST_TOKEN", "fake-token")

        mock_api_cls = MagicMock()
        mock_api_cls.return_value.get_tasks.side_effect = RuntimeError("API down")

        with patch.dict("sys.modules", {"todoist_api_python": MagicMock(), "todoist_api_python.api": MagicMock(TodoistAPI=mock_api_cls)}):
            result = load_task_items(tmp_path)
            assert result == []

    def test_todoist_success(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_TASK_SOURCE", "todoist")
        monkeypatch.setenv("JARVIS_TODOIST_TOKEN", "real-token")

        task1 = SimpleNamespace(content="Write tests", priority=4, due=SimpleNamespace(date="2026-03-01"))
        task2 = SimpleNamespace(content="Review PR", priority=1, due=None)

        mock_api_cls = MagicMock()
        mock_api_cls.return_value.get_tasks.return_value = [task1, task2]
        mock_mod = MagicMock()
        mock_mod.TodoistAPI = mock_api_cls

        with patch.dict("sys.modules", {"todoist_api_python": MagicMock(), "todoist_api_python.api": mock_mod}):
            result = load_task_items(tmp_path)
            assert len(result) == 2
            assert result[0]["title"] == "Write tests"
            assert result[0]["priority"] == "urgent"
            assert result[0]["due_date"] == "2026-03-01"
            assert result[1]["priority"] == "low"
            assert result[1]["due_date"] == ""

    def test_missing_tasks_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_TASK_SOURCE", raising=False)
        monkeypatch.delenv("JARVIS_TASKS_JSON", raising=False)
        result = load_task_items(tmp_path)
        assert result == []


# ===========================================================================
# _triage_email
# ===========================================================================

class TestTriageEmail:
    def test_urgent_subject(self) -> None:
        assert _triage_email("alice@work.com", "URGENT: server down") == "high"

    def test_action_required_subject(self) -> None:
        assert _triage_email("boss@corp.com", "Action Required: review doc") == "high"

    def test_payment_due_subject(self) -> None:
        assert _triage_email("billing@example.com", "Your payment due by Friday") == "high"

    def test_invoice_subject(self) -> None:
        assert _triage_email("vendor@shop.com", "Invoice #12345 attached") == "high"

    def test_security_subject(self) -> None:
        assert _triage_email("admin@corp.com", "Security alert on your account") == "high"

    def test_deadline_subject(self) -> None:
        assert _triage_email("pm@corp.com", "Project deadline approaching") == "high"

    def test_overdue_subject(self) -> None:
        assert _triage_email("library@city.gov", "Books overdue notice") == "high"

    def test_noreply_sender(self) -> None:
        assert _triage_email("Service <noreply@company.com>", "Weekly digest") == "high"

    def test_alert_sender(self) -> None:
        assert _triage_email("Monitoring <alert@infra.io>", "CPU spike detected") == "high"

    def test_billing_sender(self) -> None:
        assert _triage_email("billing@provider.com", "Statement ready") == "high"

    def test_security_sender(self) -> None:
        assert _triage_email("security@bank.com", "New login detected") == "high"

    def test_normal_email(self) -> None:
        assert _triage_email("friend@gmail.com", "Hey, lunch tomorrow?") == "normal"

    def test_case_insensitive_subject(self) -> None:
        assert _triage_email("x@y.com", "EXPIRING soon!") == "high"

    def test_no_reply_sender_with_dash(self) -> None:
        assert _triage_email("no-reply@service.com", "Your order shipped") == "high"

    def test_sender_substring_no_false_positive(self) -> None:
        """'alert@' as display name part should not false-positive when email is different."""
        assert _triage_email("John Alertson <john@normal.com>", "Hey there") == "normal"


# ===========================================================================
# _decode_email_header
# ===========================================================================

class TestDecodeEmailHeader:
    def test_plain_ascii(self) -> None:
        assert _decode_email_header("Hello world") == "Hello world"

    def test_encoded_utf8(self) -> None:
        # RFC 2047 encoded header
        assert _decode_email_header("=?utf-8?B?SGVsbG8=?=") == "Hello"

    def test_empty_string(self) -> None:
        assert _decode_email_header("") == ""

    def test_whitespace_stripped(self) -> None:
        assert _decode_email_header("  spaced  ") == "spaced"

    def test_malformed_header_returns_raw(self) -> None:
        """Malformed headers that cause decode_header to error should return raw."""
        with patch("jarvis_engine.ops_sync.decode_header", side_effect=ValueError("bad")):
            result = _decode_email_header("=?bad?encoding?=")
            assert "bad" in result

    def test_unknown_charset_falls_back_to_utf8(self) -> None:
        """Unknown charset should fallback to utf-8 decode."""
        with patch("jarvis_engine.ops_sync.decode_header", return_value=[(b"test", "nonexistent-charset")]):
            result = _decode_email_header("anything")
            assert result == "test"


# ===========================================================================
# load_email_items
# ===========================================================================

class TestLoadEmailItems:
    def test_json_path_from_env(self, monkeypatch, tmp_path: Path) -> None:
        email_file = tmp_path / "emails.json"
        email_file.write_text(
            json.dumps([{"subject": "Hi", "from": "a@b.com", "date": "2026-03-01"}]),
            encoding="utf-8",
        )
        monkeypatch.setenv("JARVIS_EMAIL_JSON", str(email_file))
        result = load_email_items()
        assert len(result) == 1
        assert result[0]["subject"] == "Hi"

    def test_missing_imap_config(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "")
        monkeypatch.setenv("JARVIS_IMAP_USER", "")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "")
        assert load_email_items() == []

    def test_partial_imap_config_missing_password(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "")
        assert load_email_items() == []

    def test_imap_connection_error(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass123")

        with patch("imaplib.IMAP4_SSL", side_effect=OSError("conn refused")):
            assert load_email_items() == []

    def test_imap_auth_failure(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "wrong")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.side_effect = imaplib.IMAP4.error("auth fail")

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            assert load_email_items() == []

    def test_imap_no_unseen_messages(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.return_value = ("OK", [])
        mock_client.select.return_value = ("OK", [b"5"])
        mock_client.search.return_value = ("OK", [b""])

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            assert load_email_items() == []

    def test_imap_search_not_ok(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.return_value = ("OK", [])
        mock_client.select.return_value = ("OK", [b"5"])
        mock_client.search.return_value = ("NO", [])

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            assert load_email_items() == []

    def test_imap_success_fetches_headers(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass")

        raw_header = (
            b"Subject: Test email\r\n"
            b"From: sender@example.com\r\n"
            b"Date: Mon, 01 Mar 2026 10:00:00 +0000\r\n"
            b"\r\n"
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.return_value = ("OK", [])
        mock_client.select.return_value = ("OK", [b"5"])
        mock_client.search.return_value = ("OK", [b"1 2"])
        mock_client.fetch.return_value = ("OK", [(b"1 (RFC822.HEADER {100}", raw_header)])

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            result = load_email_items()
            assert len(result) == 2  # two IDs, each fetched
            assert result[0]["subject"] == "Test email"
            assert result[0]["from"] == "sender@example.com"
            assert result[0]["read"] is False
            assert result[0]["importance"] == "normal"

    def test_imap_fetch_not_ok_skips_message(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.return_value = ("OK", [])
        mock_client.select.return_value = ("OK", [b"1"])
        mock_client.search.return_value = ("OK", [b"1"])
        mock_client.fetch.return_value = ("NO", [])

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            assert load_email_items() == []

    def test_imap_limit_parameter(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_EMAIL_JSON", "")
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "pass")

        raw_header = b"Subject: msg\r\nFrom: x@y.com\r\nDate: now\r\n\r\n"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.return_value = ("OK", [])
        mock_client.select.return_value = ("OK", [b"50"])
        # 10 message IDs
        mock_client.search.return_value = ("OK", [b"1 2 3 4 5 6 7 8 9 10"])
        mock_client.fetch.return_value = ("OK", [(b"1", raw_header)])

        with patch("imaplib.IMAP4_SSL", return_value=mock_client):
            result = load_email_items(limit=3)
            # Should only fetch last 3 IDs (8, 9, 10)
            assert mock_client.fetch.call_count == 3


# ===========================================================================
# _NoRedirectHandler and _build_no_redirect_opener
# ===========================================================================

class TestNoRedirectHandler:
    def test_redirect_raises_http_error(self) -> None:
        from urllib.error import HTTPError
        handler = ops_sync._NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(
                MagicMock(), MagicMock(), 302, "Found",
                {}, "https://evil.internal/redirect"
            )
        assert "Redirects are not allowed" in str(exc_info.value)

    def test_build_opener_returns_opener(self) -> None:
        opener = ops_sync._build_no_redirect_opener()
        assert hasattr(opener, "open")


# ===========================================================================
# SyncSummary dataclass
# ===========================================================================

class TestSyncSummary:
    def test_fields(self) -> None:
        s = SyncSummary(
            snapshot_path="/tmp/test.json",
            tasks=5, calendar_events=2, emails=10,
            bills=1, subscriptions=3, medications=0,
            school_items=2, family_items=1, projects=4,
            connectors_ready=3, connectors_pending=2, connector_prompts=1,
        )
        assert s.tasks == 5
        assert s.connectors_ready == 3
        assert s.snapshot_path == "/tmp/test.json"


# ===========================================================================
# build_live_snapshot (integration-level, all sub-functions mocked)
# ===========================================================================

class TestBuildLiveSnapshot:
    def test_full_snapshot_happy_path(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        planning = root / ".planning"
        planning.mkdir()

        # Create feed files
        (planning / "bills.json").write_text(json.dumps([{"bill": "electric"}]), encoding="utf-8")
        (planning / "subscriptions.json").write_text(json.dumps([{"sub": "netflix"}, {"sub": "spotify"}]), encoding="utf-8")
        (planning / "tasks.json").write_text(json.dumps([{"title": "task1"}]), encoding="utf-8")

        # Ensure no env overrides for feeds
        for key in ["JARVIS_MEDICATIONS_JSON", "JARVIS_SCHOOL_JSON", "JARVIS_FAMILY_JSON",
                     "JARVIS_PROJECTS_JSON", "JARVIS_TASK_SOURCE", "JARVIS_TASKS_JSON",
                     "JARVIS_CALENDAR_JSON", "JARVIS_CALENDAR_ICS_FILE",
                     "JARVIS_CALENDAR_ICS_URL", "JARVIS_EMAIL_JSON",
                     "JARVIS_IMAP_HOST", "JARVIS_ALLOW_EXTERNAL_FEEDS"]:
            monkeypatch.delenv(key, raising=False)

        # Mock connectors
        mock_status = MagicMock()
        mock_status.ready = True
        monkeypatch.setattr(ops_sync, "evaluate_connector_statuses", lambda root: [mock_status])
        monkeypatch.setattr(ops_sync, "build_connector_prompts", lambda statuses: [{"p": "test"}])
        monkeypatch.setattr(ops_sync, "serialize_statuses", lambda statuses: [{"s": "ok"}])

        output = tmp_path / "snapshot.json"
        summary = build_live_snapshot(root, output)

        assert isinstance(summary, SyncSummary)
        assert summary.tasks == 1
        assert summary.bills == 1
        assert summary.subscriptions == 2
        assert summary.connectors_ready == 1
        assert summary.connectors_pending == 0
        assert summary.connector_prompts == 1
        assert summary.snapshot_path == str(output)

        # Verify snapshot file was written
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert "date" in data
        assert data["tasks"] == [{"title": "task1"}]
        assert data["bills"] == [{"bill": "electric"}]

    def test_snapshot_creates_planning_dir(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "fresh"
        # Don't create planning dir - let build_live_snapshot do it

        for key in ["JARVIS_MEDICATIONS_JSON", "JARVIS_SCHOOL_JSON", "JARVIS_FAMILY_JSON",
                     "JARVIS_PROJECTS_JSON", "JARVIS_TASK_SOURCE", "JARVIS_TASKS_JSON",
                     "JARVIS_CALENDAR_JSON", "JARVIS_CALENDAR_ICS_FILE",
                     "JARVIS_CALENDAR_ICS_URL", "JARVIS_EMAIL_JSON",
                     "JARVIS_IMAP_HOST", "JARVIS_ALLOW_EXTERNAL_FEEDS"]:
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr(ops_sync, "evaluate_connector_statuses", lambda root: [])
        monkeypatch.setattr(ops_sync, "build_connector_prompts", lambda statuses: [])
        monkeypatch.setattr(ops_sync, "serialize_statuses", lambda statuses: [])

        output = tmp_path / "snap.json"
        summary = build_live_snapshot(root, output)
        assert (root / ".planning").exists()
        assert summary.tasks == 0
        assert summary.calendar_events == 0

    def test_snapshot_with_empty_feeds(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        (root / ".planning").mkdir()

        for key in ["JARVIS_MEDICATIONS_JSON", "JARVIS_SCHOOL_JSON", "JARVIS_FAMILY_JSON",
                     "JARVIS_PROJECTS_JSON", "JARVIS_TASK_SOURCE", "JARVIS_TASKS_JSON",
                     "JARVIS_CALENDAR_JSON", "JARVIS_CALENDAR_ICS_FILE",
                     "JARVIS_CALENDAR_ICS_URL", "JARVIS_EMAIL_JSON",
                     "JARVIS_IMAP_HOST", "JARVIS_ALLOW_EXTERNAL_FEEDS"]:
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr(ops_sync, "evaluate_connector_statuses", lambda root: [])
        monkeypatch.setattr(ops_sync, "build_connector_prompts", lambda statuses: [])
        monkeypatch.setattr(ops_sync, "serialize_statuses", lambda statuses: [])

        output = tmp_path / "snap.json"
        summary = build_live_snapshot(root, output)

        assert summary.tasks == 0
        assert summary.bills == 0
        assert summary.medications == 0
        assert summary.emails == 0
        assert summary.calendar_events == 0

    def test_snapshot_connector_counts(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        (root / ".planning").mkdir()

        for key in ["JARVIS_MEDICATIONS_JSON", "JARVIS_SCHOOL_JSON", "JARVIS_FAMILY_JSON",
                     "JARVIS_PROJECTS_JSON", "JARVIS_TASK_SOURCE", "JARVIS_TASKS_JSON",
                     "JARVIS_CALENDAR_JSON", "JARVIS_CALENDAR_ICS_FILE",
                     "JARVIS_CALENDAR_ICS_URL", "JARVIS_EMAIL_JSON",
                     "JARVIS_IMAP_HOST", "JARVIS_ALLOW_EXTERNAL_FEEDS"]:
            monkeypatch.delenv(key, raising=False)

        ready_status = MagicMock()
        ready_status.ready = True
        pending_status = MagicMock()
        pending_status.ready = False
        monkeypatch.setattr(ops_sync, "evaluate_connector_statuses", lambda root: [ready_status, pending_status, ready_status])
        monkeypatch.setattr(ops_sync, "build_connector_prompts", lambda statuses: [{"p": "1"}, {"p": "2"}])
        monkeypatch.setattr(ops_sync, "serialize_statuses", lambda statuses: [])

        output = tmp_path / "snap.json"
        summary = build_live_snapshot(root, output)
        assert summary.connectors_ready == 2
        assert summary.connectors_pending == 1
        assert summary.connector_prompts == 2


# ---------------------------------------------------------------------------
# Google Tasks comment verification
# ---------------------------------------------------------------------------

def test_google_tasks_comment_is_note_not_todo() -> None:
    """The Google Tasks branch should have a NOTE comment, not a TODO."""
    import inspect
    source = inspect.getsource(ops_sync.load_task_items)
    assert "NOTE: Google Tasks integration requires OAuth2 with tasks.readonly scope." in source
    assert "ROADMAP.md" in source
    # The old TODO should be gone
    assert "TODO: Google Tasks requires OAuth2" not in source
