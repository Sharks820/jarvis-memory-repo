from __future__ import annotations

import sys

from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.task_orchestrator import TaskOrchestrator, TaskRequest, run_shell_command


def test_task_orchestrator_code_dry_run(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    orch = TaskOrchestrator(store, tmp_path)
    req = TaskRequest(
        task_type="code",
        prompt="Write a hello world script",
        execute=False,
        has_explicit_approval=False,
        model="qwen3-coder:30b",
        endpoint="http://127.0.0.1:11434",
    )
    result = orch.run(req)
    assert result.allowed is True
    assert result.provider == "ollama"
    assert "Dry-run" in result.plan


def test_task_orchestrator_privileged_requires_approval(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    orch = TaskOrchestrator(store, tmp_path)
    req = TaskRequest(
        task_type="video",
        prompt="Create a short launch teaser",
        execute=False,
        has_explicit_approval=False,
        model="qwen3-coder:30b",
        endpoint="http://127.0.0.1:11434",
    )
    result = orch.run(req)
    assert result.allowed is False
    assert result.provider == "policy_gate"


def test_run_shell_command_empty_rejected() -> None:
    rc, stdout, stderr = run_shell_command("   ")
    assert rc == 2
    assert stdout == ""
    assert "Empty command" in stderr


def test_run_shell_command_timeout_returns_124() -> None:
    rc, stdout, stderr = run_shell_command("ping 127.0.0.1 -n 6", timeout_s=1)
    assert rc == 124
    assert isinstance(stdout, str)
    assert "timed out" in stderr.lower()
