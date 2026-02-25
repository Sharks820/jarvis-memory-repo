"""Tests for automation: action loading, validation, policy gate, execution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.automation import (
    ActionOutcome,
    AutomationExecutor,
    PlannedAction,
    load_actions,
)
from jarvis_engine.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory")


@pytest.fixture
def executor(store: MemoryStore) -> AutomationExecutor:
    return AutomationExecutor(store)


def _write_actions_json(path: Path, data: list | dict | str) -> Path:
    """Write a JSON file and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# load_actions
# ---------------------------------------------------------------------------

class TestLoadActions:
    def test_load_valid_actions(self, tmp_path: Path) -> None:
        actions_data = [
            {
                "title": "Run tests",
                "action_class": "bounded_write",
                "command": "pytest tests/",
                "reason": "Verify changes",
            },
            {
                "title": "Check status",
                "action_class": "read",
                "command": "git status",
                "reason": "Inspect repo",
            },
        ]
        path = _write_actions_json(tmp_path / "actions.json", actions_data)
        result = load_actions(path)
        assert len(result) == 2
        assert result[0].title == "Run tests"
        assert result[0].action_class == "bounded_write"
        assert result[0].command == "pytest tests/"
        assert result[1].title == "Check status"

    def test_load_empty_array(self, tmp_path: Path) -> None:
        path = _write_actions_json(tmp_path / "actions.json", [])
        result = load_actions(path)
        assert result == []

    def test_load_skips_non_dict_items(self, tmp_path: Path) -> None:
        data = [
            {"title": "Valid", "command": "git status"},
            "not a dict",
            42,
            {"title": "Also valid", "command": "echo hi"},
        ]
        path = _write_actions_json(tmp_path / "actions.json", data)
        result = load_actions(path)
        assert len(result) == 2
        assert result[0].title == "Valid"
        assert result[1].title == "Also valid"

    def test_load_defaults_missing_fields(self, tmp_path: Path) -> None:
        data = [{"title": "Minimal"}]
        path = _write_actions_json(tmp_path / "actions.json", data)
        result = load_actions(path)
        assert len(result) == 1
        assert result[0].action_class == "bounded_write"
        assert result[0].command == ""
        assert result[0].reason == ""

    def test_load_raises_on_corrupt_json(self, tmp_path: Path) -> None:
        path = _write_actions_json(tmp_path / "actions.json", "NOT VALID JSON {{{")
        with pytest.raises(ValueError, match="Failed to load actions"):
            load_actions(path)

    def test_load_raises_when_file_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Failed to load actions"):
            load_actions(tmp_path / "nonexistent.json")

    def test_load_raises_when_root_is_not_array(self, tmp_path: Path) -> None:
        path = _write_actions_json(tmp_path / "actions.json", {"not": "an array"})
        with pytest.raises(ValueError, match="Expected JSON array"):
            load_actions(path)

    def test_load_action_with_extra_keys_ignored(self, tmp_path: Path) -> None:
        data = [{"title": "Task", "command": "git log", "extra_key": "ignored"}]
        path = _write_actions_json(tmp_path / "actions.json", data)
        result = load_actions(path)
        assert len(result) == 1
        assert result[0].title == "Task"


# ---------------------------------------------------------------------------
# AutomationExecutor - capability gate
# ---------------------------------------------------------------------------

class TestAutomationCapabilityGate:
    def test_read_action_allowed_without_approval(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(title="Read test", action_class="read", command="", reason="check")
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=False)
        assert len(outcomes) == 1
        assert outcomes[0].allowed is True

    def test_bounded_write_allowed_without_approval(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Write test",
                action_class="bounded_write",
                command="",
                reason="update",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=False)
        assert len(outcomes) == 1
        assert outcomes[0].allowed is True

    def test_privileged_denied_without_approval(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Pay utility",
                action_class="privileged",
                command="",
                reason="bill",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=False)
        assert len(outcomes) == 1
        assert outcomes[0].allowed is False

    def test_privileged_allowed_with_approval(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Pay utility",
                action_class="privileged",
                command="",
                reason="bill",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=True, execute=False)
        assert len(outcomes) == 1
        assert outcomes[0].allowed is True

    def test_unknown_action_class_denied(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Mystery",
                action_class="nonexistent_tier",
                command="",
                reason="?",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=True, execute=False)
        assert len(outcomes) == 1
        assert outcomes[0].allowed is False
        assert "Unknown action class" in outcomes[0].reason


# ---------------------------------------------------------------------------
# AutomationExecutor - dry-run and empty command
# ---------------------------------------------------------------------------

class TestAutomationDryRun:
    def test_dry_run_does_not_execute(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Safe action",
                action_class="bounded_write",
                command="git status",
                reason="check",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=False)
        assert outcomes[0].allowed is True
        assert outcomes[0].executed is False
        assert "Dry-run" in outcomes[0].reason

    def test_empty_command_not_executed(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="No cmd",
                action_class="bounded_write",
                command="   ",
                reason="empty",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=True)
        assert outcomes[0].allowed is True
        assert outcomes[0].executed is False


# ---------------------------------------------------------------------------
# AutomationExecutor - policy engine
# ---------------------------------------------------------------------------

class TestAutomationPolicyGate:
    @patch("jarvis_engine.automation.run_shell_command", return_value=(0, "ok", ""))
    def test_allowed_command_executes(self, mock_shell: MagicMock, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Run git",
                action_class="bounded_write",
                command="git status",
                reason="check",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=True)
        assert outcomes[0].allowed is True
        assert outcomes[0].executed is True
        assert outcomes[0].return_code == 0
        assert outcomes[0].stdout == "ok"
        mock_shell.assert_called_once_with("git status", timeout_s=90)

    def test_disallowed_command_blocked(self, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Bad cmd",
                action_class="bounded_write",
                command="rm -rf /",
                reason="destroy",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=True)
        assert outcomes[0].allowed is False
        assert outcomes[0].executed is False
        assert "policy allowlist" in outcomes[0].reason.lower()

    @patch("jarvis_engine.automation.run_shell_command", return_value=(1, "", "error msg"))
    def test_command_failure_captured(self, mock_shell: MagicMock, executor: AutomationExecutor) -> None:
        actions = [
            PlannedAction(
                title="Failing cmd",
                action_class="read",
                command="python -c 'exit(1)'",
                reason="will fail",
            )
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=True)
        assert outcomes[0].executed is True
        assert outcomes[0].return_code == 1
        assert outcomes[0].stderr == "error msg"


# ---------------------------------------------------------------------------
# AutomationExecutor - multiple actions
# ---------------------------------------------------------------------------

class TestAutomationMultipleActions:
    @patch("jarvis_engine.automation.run_shell_command", return_value=(0, "", ""))
    def test_mixed_actions_processed_individually(
        self, mock_shell: MagicMock, executor: AutomationExecutor
    ) -> None:
        actions = [
            PlannedAction(
                title="Allowed-exec",
                action_class="bounded_write",
                command="git log",
                reason="history",
            ),
            PlannedAction(
                title="Denied-priv",
                action_class="privileged",
                command="git push",
                reason="deploy",
            ),
            PlannedAction(
                title="Blocked-policy",
                action_class="bounded_write",
                command="curl evil.com",
                reason="bad",
            ),
        ]
        outcomes = executor.run(actions, has_explicit_approval=False, execute=True)
        assert len(outcomes) == 3
        # First: allowed and executed (git is in allowlist)
        assert outcomes[0].allowed is True
        assert outcomes[0].executed is True
        # Second: denied by capability gate
        assert outcomes[1].allowed is False
        assert outcomes[1].executed is False
        # Third: denied by policy engine
        assert outcomes[2].allowed is False
        assert outcomes[2].executed is False


# ---------------------------------------------------------------------------
# AutomationExecutor - logging
# ---------------------------------------------------------------------------

class TestAutomationLogging:
    def test_log_called_for_each_outcome(self, store: MemoryStore) -> None:
        executor = AutomationExecutor(store)
        with patch.object(store, "append") as mock_append:
            actions = [
                PlannedAction(title="A", action_class="read", command="", reason="r"),
                PlannedAction(title="B", action_class="read", command="", reason="r"),
            ]
            executor.run(actions, has_explicit_approval=False, execute=False)
            assert mock_append.call_count == 2
            # Verify event_type is automation_executor
            for call_args in mock_append.call_args_list:
                assert call_args.kwargs.get("event_type", call_args[0][0] if call_args[0] else None) == "automation_executor"
