from __future__ import annotations

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
    # Use a script file approach that works with shlex.split(posix=False) on Windows
    import tempfile, os
    script = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    try:
        script.write("import time\ntime.sleep(30)\n")
        script.close()
        rc, stdout, stderr = run_shell_command(f"python {script.name}", timeout_s=1)
        assert rc == 124
        assert isinstance(stdout, str)
        assert "timed out" in stderr.lower()
    finally:
        os.unlink(script.name)


def test_run_shell_command_rejects_unlisted_command() -> None:
    rc, stdout, stderr = run_shell_command("ping 127.0.0.1 -n 1")
    assert rc == 2
    assert "not in allowlist" in stderr


def test_task_orchestrator_rejects_output_path_outside_repo(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    orch = TaskOrchestrator(store, tmp_path)
    req = TaskRequest(
        task_type="image",
        prompt="Generate image",
        execute=True,
        has_explicit_approval=False,
        model="qwen3-coder:30b",
        endpoint="http://127.0.0.1:11434",
        output_path=str((tmp_path.parent / "outside.png").resolve()),
    )
    result = orch.run(req)
    assert result.allowed is False
    assert "output path" in result.reason.lower()
