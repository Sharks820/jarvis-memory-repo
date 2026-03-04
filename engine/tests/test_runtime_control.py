"""Tests for runtime_control: read/write/reset control state, defaults, corruption handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine.runtime_control import (
    DEFAULT_CONTROL_STATE,
    control_state_path,
    read_control_state,
    reset_control_state,
    write_control_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Project root with the runtime directory pre-created."""
    runtime = tmp_path / ".planning" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# control_state_path
# ---------------------------------------------------------------------------

class TestControlStatePath:
    def test_returns_expected_path(self, tmp_path: Path) -> None:
        result = control_state_path(tmp_path)
        assert result == tmp_path / ".planning" / "runtime" / "control.json"


# ---------------------------------------------------------------------------
# DEFAULT_CONTROL_STATE
# ---------------------------------------------------------------------------

class TestDefaultControlState:
    def test_default_values(self) -> None:
        assert DEFAULT_CONTROL_STATE["daemon_paused"] is False
        assert DEFAULT_CONTROL_STATE["safe_mode"] is False
        assert DEFAULT_CONTROL_STATE["reason"] == ""
        assert DEFAULT_CONTROL_STATE["updated_utc"] == ""

    def test_default_has_six_keys(self) -> None:
        assert set(DEFAULT_CONTROL_STATE.keys()) == {
            "daemon_paused",
            "safe_mode",
            "reason",
            "updated_utc",
            "muted",
            "mute_until_utc",
        }


# ---------------------------------------------------------------------------
# read_control_state
# ---------------------------------------------------------------------------

class TestReadControlState:
    def test_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        state = read_control_state(tmp_path)
        assert state == DEFAULT_CONTROL_STATE

    def test_returns_defaults_on_corrupt_json(self, root: Path) -> None:
        path = control_state_path(root)
        path.write_text("NOT JSON AT ALL {{{", encoding="utf-8")
        state = read_control_state(root)
        assert state == DEFAULT_CONTROL_STATE

    def test_returns_defaults_when_json_is_array(self, root: Path) -> None:
        path = control_state_path(root)
        path.write_text("[1, 2, 3]", encoding="utf-8")
        state = read_control_state(root)
        assert state == DEFAULT_CONTROL_STATE

    def test_returns_defaults_when_json_is_string(self, root: Path) -> None:
        path = control_state_path(root)
        path.write_text('"just a string"', encoding="utf-8")
        state = read_control_state(root)
        assert state == DEFAULT_CONTROL_STATE

    def test_reads_valid_state(self, root: Path) -> None:
        path = control_state_path(root)
        data = {
            "daemon_paused": True,
            "safe_mode": True,
            "reason": "maintenance",
            "updated_utc": "2026-01-15T00:00:00+00:00",
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_control_state(root)
        assert state["daemon_paused"] is True
        assert state["safe_mode"] is True
        assert state["reason"] == "maintenance"

    def test_reason_truncated_at_200(self, root: Path) -> None:
        path = control_state_path(root)
        long_reason = "x" * 300
        data = {"reason": long_reason}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_control_state(root)
        assert len(state["reason"]) == 200

    def test_reason_stripped(self, root: Path) -> None:
        path = control_state_path(root)
        data = {"reason": "   padded   "}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_control_state(root)
        assert state["reason"] == "padded"

    def test_missing_keys_get_defaults(self, root: Path) -> None:
        path = control_state_path(root)
        path.write_text("{}", encoding="utf-8")
        state = read_control_state(root)
        assert state["daemon_paused"] is False
        assert state["safe_mode"] is False
        assert state["reason"] == ""

    def test_bool_coercion(self, root: Path) -> None:
        """Truthy non-bool values are coerced to bool."""
        path = control_state_path(root)
        data = {"daemon_paused": 1, "safe_mode": "yes"}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_control_state(root)
        assert state["daemon_paused"] is True
        assert state["safe_mode"] is True


# ---------------------------------------------------------------------------
# write_control_state
# ---------------------------------------------------------------------------

class TestWriteControlState:
    def test_write_creates_file(self, root: Path) -> None:
        state = write_control_state(root, daemon_paused=True)
        assert state["daemon_paused"] is True
        assert control_state_path(root).exists()

    def test_write_sets_updated_utc(self, root: Path) -> None:
        state = write_control_state(root, daemon_paused=False)
        assert state["updated_utc"] != ""

    def test_partial_update_daemon_paused_only(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True, safe_mode=True)
        state = write_control_state(root, daemon_paused=False)
        assert state["daemon_paused"] is False
        assert state["safe_mode"] is True  # unchanged

    def test_partial_update_safe_mode_only(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True, safe_mode=False)
        state = write_control_state(root, safe_mode=True)
        assert state["daemon_paused"] is True  # unchanged
        assert state["safe_mode"] is True

    def test_reason_truncated_on_write(self, root: Path) -> None:
        long_reason = "r" * 300
        state = write_control_state(root, reason=long_reason)
        assert len(state["reason"]) == 200

    def test_blank_reason_is_not_applied(self, root: Path) -> None:
        write_control_state(root, reason="original reason")
        state = write_control_state(root, reason="   ")
        assert state["reason"] == "original reason"

    def test_no_args_only_updates_timestamp(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True, safe_mode=True, reason="test")
        state = write_control_state(root)
        assert state["daemon_paused"] is True
        assert state["safe_mode"] is True
        assert state["reason"] == "test"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """write_control_state works even if .planning/runtime/ doesn't exist."""
        state = write_control_state(tmp_path, daemon_paused=True)
        assert state["daemon_paused"] is True
        assert control_state_path(tmp_path).exists()

    def test_roundtrip_persistence(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True, safe_mode=True, reason="roundtrip")
        state = read_control_state(root)
        assert state["daemon_paused"] is True
        assert state["safe_mode"] is True
        assert state["reason"] == "roundtrip"


# ---------------------------------------------------------------------------
# reset_control_state
# ---------------------------------------------------------------------------

class TestResetControlState:
    def test_reset_clears_all_flags(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True, safe_mode=True, reason="testing")
        state = reset_control_state(root)
        assert state["daemon_paused"] is False
        assert state["safe_mode"] is False
        assert state["reason"] == ""

    def test_reset_sets_updated_utc(self, root: Path) -> None:
        state = reset_control_state(root)
        assert state["updated_utc"] != ""

    def test_reset_persists_to_disk(self, root: Path) -> None:
        write_control_state(root, daemon_paused=True)
        reset_control_state(root)
        state = read_control_state(root)
        assert state["daemon_paused"] is False

    def test_reset_works_without_prior_state(self, tmp_path: Path) -> None:
        state = reset_control_state(tmp_path)
        assert state["daemon_paused"] is False
        assert state["safe_mode"] is False
