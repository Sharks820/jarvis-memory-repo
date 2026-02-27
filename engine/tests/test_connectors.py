"""Comprehensive tests for jarvis_engine.connectors module.

Covers connector definitions, permission management, status evaluation,
environment variable checks, fallback files, and prompt generation.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from jarvis_engine.connectors import (
    CONNECTORS,
    ConnectorDefinition,
    ConnectorStatus,
    _all_env_set,
    _any_env_set,
    _any_file_exists,
    _permissions_path,
    build_connector_prompts,
    evaluate_connector_statuses,
    grant_connector_permission,
    load_connector_permissions,
    serialize_statuses,
)


# ---------------------------------------------------------------------------
# CONNECTORS tuple tests
# ---------------------------------------------------------------------------

class TestConnectorDefinitions:
    def test_connectors_is_nonempty_tuple(self):
        assert isinstance(CONNECTORS, tuple)
        assert len(CONNECTORS) >= 5

    def test_each_has_required_fields(self):
        for c in CONNECTORS:
            assert isinstance(c, ConnectorDefinition)
            assert c.connector_id
            assert c.name
            assert c.setup_url

    def test_known_connector_ids(self):
        ids = {c.connector_id for c in CONNECTORS}
        assert "calendar" in ids
        assert "email" in ids
        assert "tasks" in ids
        assert "bills" in ids
        assert "mobile_ingest" in ids


# ---------------------------------------------------------------------------
# _any_env_set tests
# ---------------------------------------------------------------------------

class TestAnyEnvSet:
    def test_empty_keys_returns_false(self):
        assert _any_env_set(()) is False

    def test_no_keys_set_returns_false(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _any_env_set(("NONEXISTENT_KEY_1", "NONEXISTENT_KEY_2")) is False

    def test_one_key_set_returns_true(self):
        with patch.dict("os.environ", {"MY_TEST_KEY": "value"}, clear=False):
            assert _any_env_set(("MY_TEST_KEY", "OTHER_KEY")) is True

    def test_whitespace_only_value_returns_false(self):
        with patch.dict("os.environ", {"MY_TEST_KEY": "   "}, clear=False):
            assert _any_env_set(("MY_TEST_KEY",)) is False


# ---------------------------------------------------------------------------
# _all_env_set tests
# ---------------------------------------------------------------------------

class TestAllEnvSet:
    def test_empty_keys_returns_true(self):
        ok, missing = _all_env_set(())
        assert ok is True
        assert missing == []

    def test_all_keys_present(self):
        with patch.dict("os.environ", {"A": "1", "B": "2"}, clear=False):
            ok, missing = _all_env_set(("A", "B"))
            assert ok is True
            assert missing == []

    def test_some_missing(self):
        with patch.dict("os.environ", {"A": "1"}, clear=False):
            ok, missing = _all_env_set(("A", "MISSING_KEY_XYZ"))
            assert ok is False
            assert "MISSING_KEY_XYZ" in missing

    def test_whitespace_value_counts_as_missing(self):
        with patch.dict("os.environ", {"A": "  "}, clear=False):
            ok, missing = _all_env_set(("A",))
            assert ok is False
            assert "A" in missing


# ---------------------------------------------------------------------------
# _any_file_exists tests
# ---------------------------------------------------------------------------

class TestAnyFileExists:
    def test_empty_files_returns_false(self, tmp_path):
        ok, missing = _any_file_exists(tmp_path, ())
        assert ok is False
        assert missing == []

    def test_existing_file_found(self, tmp_path):
        (tmp_path / "tasks.json").write_text("{}", encoding="utf-8")
        ok, missing = _any_file_exists(tmp_path, ("tasks.json",))
        assert ok is True
        assert missing == []

    def test_missing_file_reported(self, tmp_path):
        ok, missing = _any_file_exists(tmp_path, ("tasks.json",))
        assert ok is False
        assert "tasks.json" in missing

    def test_first_found_returns_immediately(self, tmp_path):
        (tmp_path / "first.json").write_text("{}", encoding="utf-8")
        ok, missing = _any_file_exists(tmp_path, ("first.json", "second.json"))
        assert ok is True
        assert missing == []


# ---------------------------------------------------------------------------
# load_connector_permissions tests
# ---------------------------------------------------------------------------

class TestLoadConnectorPermissions:
    def test_no_file_returns_empty(self, tmp_path):
        result = load_connector_permissions(tmp_path)
        assert result == {"connectors": {}}

    def test_valid_permissions_file(self, tmp_path):
        perms_path = _permissions_path(tmp_path)
        perms_path.parent.mkdir(parents=True, exist_ok=True)
        perms_path.write_text(json.dumps({
            "connectors": {"calendar": {"granted": True, "scopes": ["read_calendar"]}}
        }), encoding="utf-8")
        result = load_connector_permissions(tmp_path)
        assert result["connectors"]["calendar"]["granted"] is True

    def test_invalid_json_returns_empty(self, tmp_path):
        perms_path = _permissions_path(tmp_path)
        perms_path.parent.mkdir(parents=True, exist_ok=True)
        perms_path.write_text("{bad json", encoding="utf-8")
        result = load_connector_permissions(tmp_path)
        assert result == {"connectors": {}}

    def test_non_dict_json_returns_empty(self, tmp_path):
        perms_path = _permissions_path(tmp_path)
        perms_path.parent.mkdir(parents=True, exist_ok=True)
        perms_path.write_text('"just a string"', encoding="utf-8")
        result = load_connector_permissions(tmp_path)
        assert result == {"connectors": {}}

    def test_missing_connectors_key_returns_empty(self, tmp_path):
        perms_path = _permissions_path(tmp_path)
        perms_path.parent.mkdir(parents=True, exist_ok=True)
        perms_path.write_text(json.dumps({"other_key": 42}), encoding="utf-8")
        result = load_connector_permissions(tmp_path)
        assert result == {"connectors": {}}


# ---------------------------------------------------------------------------
# grant_connector_permission tests
# ---------------------------------------------------------------------------

class TestGrantConnectorPermission:
    def test_grants_permission(self, tmp_path):
        result = grant_connector_permission(tmp_path, "calendar", ["read_calendar"])
        assert result["granted"] is True
        assert "read_calendar" in result["scopes"]
        assert "granted_utc" in result

    def test_unknown_connector_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown connector_id"):
            grant_connector_permission(tmp_path, "nonexistent", [])

    def test_strips_connector_id(self, tmp_path):
        result = grant_connector_permission(tmp_path, "  Calendar  ", ["read"])
        assert result["granted"] is True

    def test_filters_empty_scopes(self, tmp_path):
        result = grant_connector_permission(tmp_path, "calendar", ["read", "", "  ", "write"])
        assert result["scopes"] == ["read", "write"]

    def test_persists_to_file(self, tmp_path):
        grant_connector_permission(tmp_path, "calendar", ["read"])
        perms = load_connector_permissions(tmp_path)
        assert perms["connectors"]["calendar"]["granted"] is True

    def test_multiple_grants_coexist(self, tmp_path):
        grant_connector_permission(tmp_path, "calendar", ["read"])
        grant_connector_permission(tmp_path, "email", ["read", "write"])
        perms = load_connector_permissions(tmp_path)
        assert "calendar" in perms["connectors"]
        assert "email" in perms["connectors"]


# ---------------------------------------------------------------------------
# evaluate_connector_statuses tests
# ---------------------------------------------------------------------------

class TestEvaluateConnectorStatuses:
    def test_returns_status_for_each_connector(self, tmp_path, monkeypatch):
        # Clear all relevant env vars
        for c in CONNECTORS:
            for key in c.required_any_env:
                monkeypatch.delenv(key, raising=False)
            for key in c.required_all_env:
                monkeypatch.delenv(key, raising=False)
        statuses = evaluate_connector_statuses(tmp_path)
        assert len(statuses) == len(CONNECTORS)

    def test_all_statuses_are_connector_status(self, tmp_path, monkeypatch):
        for c in CONNECTORS:
            for key in c.required_any_env:
                monkeypatch.delenv(key, raising=False)
            for key in c.required_all_env:
                monkeypatch.delenv(key, raising=False)
        statuses = evaluate_connector_statuses(tmp_path)
        for s in statuses:
            assert isinstance(s, ConnectorStatus)

    def test_unconfigured_connector_not_ready(self, tmp_path, monkeypatch):
        for c in CONNECTORS:
            for key in c.required_any_env:
                monkeypatch.delenv(key, raising=False)
            for key in c.required_all_env:
                monkeypatch.delenv(key, raising=False)
        statuses = evaluate_connector_statuses(tmp_path)
        cal = next(s for s in statuses if s.connector_id == "calendar")
        assert cal.configured is False
        assert cal.ready is False

    def test_configured_with_permission_is_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "/tmp/cal.json")
        grant_connector_permission(tmp_path, "calendar", ["read"])
        statuses = evaluate_connector_statuses(tmp_path)
        cal = next(s for s in statuses if s.connector_id == "calendar")
        assert cal.configured is True
        assert cal.permission_granted is True
        assert cal.ready is True
        assert cal.message == "Connector ready."

    def test_configured_without_permission_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_CALENDAR_JSON", "/tmp/cal.json")
        # Don't grant permission
        statuses = evaluate_connector_statuses(tmp_path)
        cal = next(s for s in statuses if s.connector_id == "calendar")
        assert cal.configured is True
        assert cal.permission_granted is False
        assert cal.ready is False
        assert "permission" in cal.message.lower()

    def test_fallback_file_makes_configured(self, tmp_path, monkeypatch):
        # Clear env vars for tasks connector
        for key in ("JARVIS_TASKS_JSON", "JARVIS_TASK_SOURCE", "JARVIS_TODOIST_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        # Create fallback file
        (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".planning" / "tasks.json").write_text("[]", encoding="utf-8")
        statuses = evaluate_connector_statuses(tmp_path)
        tasks = next(s for s in statuses if s.connector_id == "tasks")
        assert tasks.configured is True
        # Tasks does not require permission, so should be ready
        assert tasks.ready is True

    def test_all_env_configured_email(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("JARVIS_IMAP_USER", "user@example.com")
        monkeypatch.setenv("JARVIS_IMAP_PASS", "password123")
        monkeypatch.delenv("JARVIS_EMAIL_JSON", raising=False)
        grant_connector_permission(tmp_path, "email", ["read"])
        statuses = evaluate_connector_statuses(tmp_path)
        email = next(s for s in statuses if s.connector_id == "email")
        assert email.configured is True
        assert email.ready is True

    def test_no_permission_required_connector_ready_with_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_BILLS_JSON", "/tmp/bills.json")
        statuses = evaluate_connector_statuses(tmp_path)
        bills = next(s for s in statuses if s.connector_id == "bills")
        assert bills.configured is True
        assert bills.permission_granted is True  # not required
        assert bills.ready is True


# ---------------------------------------------------------------------------
# build_connector_prompts tests
# ---------------------------------------------------------------------------

class TestBuildConnectorPrompts:
    def test_ready_connector_skipped(self):
        statuses = [
            ConnectorStatus("test", "Test", "http://x", False, True, True, True, [], [], "Ready"),
        ]
        prompts = build_connector_prompts(statuses)
        assert len(prompts) == 0

    def test_permission_missing_prompt(self):
        statuses = [
            ConnectorStatus("cal", "Calendar", "http://x", True, False, False, False, [], [], "Perm needed"),
        ]
        prompts = build_connector_prompts(statuses)
        assert len(prompts) == 1
        assert "Grant permission" in prompts[0]["title"]
        assert "option_voice" in prompts[0]
        assert "option_tap_url" in prompts[0]
        assert "connect-grant" in prompts[0]["next_step"]

    def test_config_missing_prompt(self):
        statuses = [
            ConnectorStatus("tasks", "Tasks", "http://x", False, True, False, False, ["JARVIS_TASKS_JSON"], [], "Setup needed"),
        ]
        prompts = build_connector_prompts(statuses)
        assert len(prompts) == 1
        assert "Complete setup" in prompts[0]["title"]
        assert prompts[0]["reason"] == "Configuration missing"

    def test_multiple_prompts(self, tmp_path, monkeypatch):
        for c in CONNECTORS:
            for key in c.required_any_env:
                monkeypatch.delenv(key, raising=False)
            for key in c.required_all_env:
                monkeypatch.delenv(key, raising=False)
        statuses = evaluate_connector_statuses(tmp_path)
        prompts = build_connector_prompts(statuses)
        assert len(prompts) >= 1
        for p in prompts:
            assert "connector_id" in p
            assert "option_voice" in p
            assert "option_tap_url" in p


# ---------------------------------------------------------------------------
# serialize_statuses tests
# ---------------------------------------------------------------------------

class TestSerializeStatuses:
    def test_returns_list_of_dicts(self):
        statuses = [
            ConnectorStatus("test", "Test", "http://x", False, True, True, True, [], [], "Ready"),
        ]
        result = serialize_statuses(statuses)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["connector_id"] == "test"

    def test_empty_list(self):
        assert serialize_statuses([]) == []
