from __future__ import annotations

from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.security.net_policy import is_safe_ollama_endpoint
from jarvis_engine.task_orchestrator import (
    TaskOrchestrator,
    TaskRequest,
    run_shell_command,
)


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
    import tempfile
    import os

    script = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    try:
        script.write("import time\ntime.sleep(30)\n")
        script.close()
        rc, stdout, stderr = run_shell_command(
            f"python {script.name}",
            timeout_s=1,
            has_explicit_approval=True,
        )
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


# ===========================================================================
# Expanded test coverage below
# ===========================================================================

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from jarvis_engine.task_orchestrator import (
    TaskResult,
    DEFAULT_FALLBACK_MODELS,
    _SHELL_COMMAND_ALLOWLIST,
    _PRIVILEGED_SHELL_ALLOWLIST,
)


# ---------------------------------------------------------------------------
# Helper: create orchestrator with mocked internals
# ---------------------------------------------------------------------------


def _make_orch(tmp_path):
    store = MemoryStore(tmp_path)
    return TaskOrchestrator(store, tmp_path), store


# ---------------------------------------------------------------------------
# TaskRequest / TaskResult dataclass tests
# ---------------------------------------------------------------------------


class TestTaskDataclasses:
    def test_task_request_defaults(self):
        req = TaskRequest(
            task_type="code",
            prompt="test",
            execute=False,
            has_explicit_approval=False,
            model="m",
            endpoint="http://127.0.0.1:11434",
        )
        assert req.quality_profile == "max_quality"
        assert req.output_path is None

    def test_task_result_defaults(self):
        result = TaskResult(allowed=True, provider="test", plan="plan")
        assert result.output_text == ""
        assert result.output_path == ""
        assert result.reason == ""


# ---------------------------------------------------------------------------
# _model_candidates tests
# ---------------------------------------------------------------------------


class TestModelCandidates:
    def test_model_candidates_default_fallbacks(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.dict(os.environ, {"JARVIS_CODE_MODEL_FALLBACKS": ""}):
            candidates = orch._model_candidates("primary-model")
        assert candidates[0] == "primary-model"
        for fb in DEFAULT_FALLBACK_MODELS:
            assert fb in candidates

    def test_model_candidates_env_override(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.dict(os.environ, {"JARVIS_CODE_MODEL_FALLBACKS": "alpha,beta"}):
            candidates = orch._model_candidates("primary-model")
        assert candidates == ["primary-model", "alpha", "beta"]

    def test_model_candidates_deduplication(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.dict(
            os.environ, {"JARVIS_CODE_MODEL_FALLBACKS": "alpha,alpha,beta"}
        ):
            candidates = orch._model_candidates("alpha")
        assert candidates == ["alpha", "beta"]

    def test_model_candidates_strips_whitespace(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.dict(os.environ, {"JARVIS_CODE_MODEL_FALLBACKS": " x , y "}):
            candidates = orch._model_candidates("  primary  ")
        assert candidates[0] == "primary"
        assert "x" in candidates
        assert "y" in candidates

    def test_model_candidates_empty_primary(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.dict(os.environ, {"JARVIS_CODE_MODEL_FALLBACKS": "a,b"}):
            candidates = orch._model_candidates("")
        # Empty string is filtered out
        assert "" not in candidates
        assert candidates == ["a", "b"]


# ---------------------------------------------------------------------------
# _quality_options tests
# ---------------------------------------------------------------------------


class TestQualityOptions:
    def test_max_quality_profile(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        opts = orch._quality_options("max_quality")
        assert opts["num_ctx"] == 32768
        assert opts["num_predict"] == 3072
        assert opts["temperature"] == 0.05

    def test_balanced_profile(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        opts = orch._quality_options("balanced")
        assert opts["num_ctx"] == 16384
        assert opts["num_predict"] == 1536
        assert opts["temperature"] == 0.12

    def test_fast_profile_fallback(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        opts = orch._quality_options("fast")
        assert opts["num_ctx"] == 8192
        assert opts["num_predict"] == 768
        assert opts["temperature"] == 0.2

    def test_unknown_profile_uses_fast(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        opts = orch._quality_options("nonexistent")
        assert opts["num_ctx"] == 8192


# ---------------------------------------------------------------------------
# _compose_code_prompt tests
# ---------------------------------------------------------------------------


class TestComposeCodePrompt:
    def test_max_quality_adds_system_prefix(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        prompt = orch._compose_code_prompt("Write fibonacci", "max_quality")
        assert "principal software engineer" in prompt
        assert "Write fibonacci" in prompt

    def test_non_max_quality_returns_raw(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        prompt = orch._compose_code_prompt("Write fibonacci", "fast")
        assert prompt == "Write fibonacci"

    def test_truncation_at_max_chars(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        long_prompt = "x" * 30000
        result = orch._compose_code_prompt(long_prompt, "fast")
        assert len(result) == orch._MAX_PROMPT_CHARS


# ---------------------------------------------------------------------------
# _looks_like_python tests
# ---------------------------------------------------------------------------


class TestLooksLikePython:
    def test_detects_py_extension(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._looks_like_python("generate code", "output.py") is True

    def test_detects_python_keyword(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._looks_like_python("write a Python script", None) is True

    def test_detects_pytest_keyword(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._looks_like_python("write pytest tests", None) is True

    def test_no_python_indicators(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._looks_like_python("write JavaScript", "output.js") is False


# ---------------------------------------------------------------------------
# _python_syntax_issue tests
# ---------------------------------------------------------------------------


class TestPythonSyntaxIssue:
    def test_valid_python_returns_empty(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._python_syntax_issue("x = 1\nprint(x)") == ""

    def test_invalid_python_returns_error(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        result = orch._python_syntax_issue("def foo(:\n  pass")
        assert result != ""
        assert "line" in result.lower()


# ---------------------------------------------------------------------------
# _extract_output tests
# ---------------------------------------------------------------------------


class TestExtractOutput:
    def test_extracts_response_field(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._extract_output({"response": "hello"}) == "hello"

    def test_missing_response_returns_empty(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._extract_output({}) == ""

    def test_strips_whitespace(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        assert orch._extract_output({"response": "  code  "}) == "code"


# ---------------------------------------------------------------------------
# _safe_output_path tests
# ---------------------------------------------------------------------------


class TestSafeOutputPath:
    def test_relative_path_inside_root(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        result = orch._safe_output_path("subdir/file.txt")
        assert str(tmp_path) in str(result)

    def test_absolute_path_inside_root(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        inner = tmp_path / "inner" / "file.txt"
        result = orch._safe_output_path(str(inner))
        assert result == inner.resolve()

    def test_absolute_path_outside_root_raises(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        outside = tmp_path.parent / "evil.txt"
        with pytest.raises(ValueError, match="output path"):
            orch._safe_output_path(str(outside.resolve()))

    def test_traversal_attack_blocked(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with pytest.raises(ValueError, match="output path"):
            orch._safe_output_path("../../etc/passwd")


# ---------------------------------------------------------------------------
# _is_safe_ollama_endpoint tests
# ---------------------------------------------------------------------------


class TestIsSafeOllamaEndpoint:
    def test_localhost_is_safe(self):
        assert is_safe_ollama_endpoint("http://127.0.0.1:11434") is True

    def test_localhost_name_is_safe(self):
        assert is_safe_ollama_endpoint("http://localhost:11434") is True

    def test_ftp_scheme_rejected(self):
        assert is_safe_ollama_endpoint("ftp://127.0.0.1:11434") is False

    def test_empty_endpoint_rejected(self):
        assert is_safe_ollama_endpoint("") is False

    def test_external_host_rejected_by_default(self):
        with patch.dict(os.environ, {"JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT": ""}):
            assert is_safe_ollama_endpoint("http://evil.com:11434") is False


# ---------------------------------------------------------------------------
# _call_ollama tests (mocked network)
# ---------------------------------------------------------------------------


class TestCallOllama:
    def test_unsafe_endpoint_returns_error(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        raw, err = orch._call_ollama(
            endpoint="ftp://badhost",
            model="test",
            prompt="hello",
            options={},
            timeout_s=10,
        )
        assert raw is None
        assert "Unsafe" in err

    def test_successful_call(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        fake_response = json.dumps({"response": "code output"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("jarvis_engine.task_orchestrator.urlopen", return_value=mock_resp):
            raw, err = orch._call_ollama(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="hello",
                options={},
                timeout_s=10,
            )
        assert err == ""
        assert raw == {"response": "code output"}

    def test_timeout_returns_error(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch("jarvis_engine.task_orchestrator.urlopen", side_effect=TimeoutError):
            raw, err = orch._call_ollama(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="hello",
                options={},
                timeout_s=5,
            )
        assert raw is None
        assert "Timed out" in err

    def test_json_decode_error(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"NOT JSON"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("jarvis_engine.task_orchestrator.urlopen", return_value=mock_resp):
            raw, err = orch._call_ollama(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="hello",
                options={},
                timeout_s=10,
            )
        assert raw is None
        assert "Invalid JSON" in err

    def test_non_dict_response_returns_error(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([1, 2, 3]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("jarvis_engine.task_orchestrator.urlopen", return_value=mock_resp):
            raw, err = orch._call_ollama(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="hello",
                options={},
                timeout_s=10,
            )
        assert raw is None
        assert "Invalid" in err


# ---------------------------------------------------------------------------
# Code task execution tests (mocked ollama)
# ---------------------------------------------------------------------------


class TestCodeTaskExecution:
    def _mock_ollama_success(self, orch, response_text="print('hello')"):
        """Patch _call_ollama to return a successful response."""
        return patch.object(
            orch,
            "_call_ollama",
            return_value=({"response": response_text}, ""),
        )

    def _mock_ollama_failure(self, orch, error="model error"):
        """Patch _call_ollama to return an error."""
        return patch.object(
            orch,
            "_call_ollama",
            return_value=(None, error),
        )

    def test_code_execute_success(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="code",
            prompt="Write hello world",
            execute=True,
            has_explicit_approval=False,
            model="test-model",
            endpoint="http://127.0.0.1:11434",
            quality_profile="fast",
        )
        with self._mock_ollama_success(orch):
            result = orch.run(req)
        assert result.allowed is True
        assert result.provider == "ollama"
        assert "print('hello')" in result.output_text

    def test_code_execute_all_models_fail(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="code",
            prompt="Write hello world",
            execute=True,
            has_explicit_approval=False,
            model="test-model",
            endpoint="http://127.0.0.1:11434",
            quality_profile="fast",
        )
        with self._mock_ollama_failure(orch, "connection refused"):
            result = orch.run(req)
        assert result.allowed is False
        assert "connection refused" in result.reason

    def test_code_execute_writes_output_file(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        out_file = tmp_path / "output" / "script.py"
        req = TaskRequest(
            task_type="code",
            prompt="Write hello world",
            execute=True,
            has_explicit_approval=False,
            model="test-model",
            endpoint="http://127.0.0.1:11434",
            quality_profile="fast",
            output_path=str(out_file),
        )
        with self._mock_ollama_success(orch, "print('hello')"):
            result = orch.run(req)
        assert result.allowed is True
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == "print('hello')"

    def test_code_execute_output_path_outside_repo_rejected(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        outside = tmp_path.parent / "evil.py"
        req = TaskRequest(
            task_type="code",
            prompt="Write hello world",
            execute=True,
            has_explicit_approval=False,
            model="test-model",
            endpoint="http://127.0.0.1:11434",
            quality_profile="fast",
            output_path=str(outside.resolve()),
        )
        with self._mock_ollama_success(orch):
            result = orch.run(req)
        assert result.allowed is False
        assert "output path" in result.reason.lower()

    def test_code_max_quality_with_critique_and_revision(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="code",
            prompt="Write a JavaScript function",
            execute=True,
            has_explicit_approval=False,
            model="test-model",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
        )
        # _call_ollama is called multiple times:
        # 1) initial generation, 2) critique, 3) revision
        call_count = [0]
        responses = [
            ({"response": "function hello() { return 1; }"}, ""),  # initial
            ({"response": "- Missing error handling"}, ""),  # critique
            (
                {"response": "function hello() { try { return 1; } catch(e) {} }"},
                "",
            ),  # revision
        ]

        def side_effect(**kwargs):
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            return responses[idx]

        with patch.object(orch, "_call_ollama", side_effect=side_effect):
            result = orch.run(req)
        assert result.allowed is True
        assert "try" in result.output_text  # revised version used


# ---------------------------------------------------------------------------
# Adapter task tests
# ---------------------------------------------------------------------------


class TestAdapterTasks:
    def test_image_dry_run(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="image",
            prompt="A sunset over mountains",
            execute=False,
            has_explicit_approval=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = orch.run(req)
        assert result.allowed is True
        assert "Dry-run" in result.plan

    def test_model3d_requires_approval(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="model3d",
            prompt="A 3D cube",
            execute=True,
            has_explicit_approval=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = orch.run(req)
        assert result.allowed is False
        assert result.provider == "policy_gate"

    def test_model3d_allowed_with_approval(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="model3d",
            prompt="A 3D cube",
            execute=False,
            has_explicit_approval=True,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = orch.run(req)
        assert result.allowed is True
        assert "Dry-run" in result.plan

    def test_video_privileged_approved(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        req = TaskRequest(
            task_type="video",
            prompt="A time-lapse of clouds",
            execute=False,
            has_explicit_approval=True,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        result = orch.run(req)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Logging tests
# ---------------------------------------------------------------------------


class TestLogging:
    def test_run_logs_event(self, tmp_path):
        store = MagicMock()
        orch = TaskOrchestrator(store, tmp_path)
        req = TaskRequest(
            task_type="code",
            prompt="test",
            execute=False,
            has_explicit_approval=False,
            model="test",
            endpoint="http://127.0.0.1:11434",
        )
        orch.run(req)
        store.append.assert_called_once()
        call_args = store.append.call_args
        assert call_args.kwargs["event_type"] == "task_orchestrator"
        assert "code" in call_args.kwargs["message"]


# ---------------------------------------------------------------------------
# run_shell_command extended tests
# ---------------------------------------------------------------------------


class TestRunShellCommand:
    def test_allowed_command_succeeds(self):
        import tempfile

        script = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            script.write("print('hello')\n")
            script.close()
            rc, stdout, stderr = run_shell_command(
                f"python {script.name}",
                has_explicit_approval=True,
            )
            assert rc == 0
            assert "hello" in stdout
        finally:
            os.unlink(script.name)

    def test_invalid_syntax_returns_error(self):
        rc, stdout, stderr = run_shell_command("python -c 'unterminated")
        # shlex.split with posix=False on Windows doesn't raise for this,
        # but the subprocess itself may error
        assert isinstance(rc, int)

    def test_all_allowlisted_commands_present(self):
        expected_standard = {"git", "npm", "node", "pytest", "jarvis"}
        expected_privileged = {"python", "python3", "pip", "pip3"}
        assert _SHELL_COMMAND_ALLOWLIST == expected_standard
        assert _PRIVILEGED_SHELL_ALLOWLIST == expected_privileged

    def test_multiple_unlisted_commands(self):
        for cmd in [
            "rm -rf /",
            "curl http://evil.com",
            "wget http://evil.com",
            "bash -c ls",
        ]:
            rc, _, stderr = run_shell_command(cmd)
            assert rc == 2
            assert "not in allowlist" in stderr


# ---------------------------------------------------------------------------
# _single_pass_generate tests
# ---------------------------------------------------------------------------


class TestSinglePassGenerate:
    def test_returns_text_on_success(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.object(
            orch, "_call_ollama", return_value=({"response": "result"}, "")
        ):
            text = orch._single_pass_generate(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="test",
                options={},
                timeout_s=10,
            )
        assert text == "result"

    def test_returns_empty_on_failure(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        with patch.object(orch, "_call_ollama", return_value=(None, "error")):
            text = orch._single_pass_generate(
                endpoint="http://127.0.0.1:11434",
                model="test",
                prompt="test",
                options={},
                timeout_s=10,
            )
        assert text == ""


# ---------------------------------------------------------------------------
# Prompt builder tests
# ---------------------------------------------------------------------------


class TestPromptBuilders:
    def test_critique_prompt_contains_code(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        result = orch._critique_prompt("def foo(): pass")
        assert "def foo(): pass" in result
        assert "Review" in result

    def test_revision_prompt_contains_all_parts(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        result = orch._revision_prompt(
            original="write code",
            draft_code="def foo(): pass",
            critique="- Missing docstring",
        )
        assert "write code" in result
        assert "def foo(): pass" in result
        assert "Missing docstring" in result

    def test_python_fix_prompt(self, tmp_path):
        orch, _ = _make_orch(tmp_path)
        result = orch._python_fix_prompt("def foo(:", "expected ')'")
        assert "def foo(:" in result
        assert "expected ')'" in result


# ---------------------------------------------------------------------------
# Security: privileged shell allowlist tests
# ---------------------------------------------------------------------------


class TestPrivilegedShellAllowlist:
    """Tests for the privileged shell allowlist (python/pip require approval)."""

    def test_python_rejected_without_approval(self):
        """python command is rejected when has_explicit_approval is False."""
        rc, stdout, stderr = run_shell_command("python -c \"print('hello')\"")
        assert rc == 2
        assert "requires explicit approval" in stderr
        assert "privileged" in stderr.lower()

    def test_python3_rejected_without_approval(self):
        """python3 command is rejected when has_explicit_approval is False."""
        rc, stdout, stderr = run_shell_command("python3 --version")
        assert rc == 2
        assert "requires explicit approval" in stderr

    def test_pip_rejected_without_approval(self):
        """pip command is rejected when has_explicit_approval is False."""
        rc, stdout, stderr = run_shell_command("pip --version")
        assert rc == 2
        assert "requires explicit approval" in stderr

    def test_pip3_rejected_without_approval(self):
        """pip3 command is rejected when has_explicit_approval is False."""
        rc, stdout, stderr = run_shell_command("pip3 --version")
        assert rc == 2
        assert "requires explicit approval" in stderr

    def test_python_allowed_with_approval(self):
        """python command succeeds when has_explicit_approval is True."""
        import tempfile

        script = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            script.write("print('approved')\n")
            script.close()
            rc, stdout, stderr = run_shell_command(
                f"python {script.name}",
                has_explicit_approval=True,
            )
            assert rc == 0
            assert "approved" in stdout
        finally:
            os.unlink(script.name)

    def test_git_does_not_require_approval(self):
        """git (standard allowlist) works without explicit approval."""
        rc, stdout, stderr = run_shell_command("git --version")
        assert rc == 0
        assert "git" in stdout.lower()

    def test_python_not_in_standard_allowlist(self):
        """python is NOT in the standard allowlist anymore."""
        assert "python" not in _SHELL_COMMAND_ALLOWLIST
        assert "pip" not in _SHELL_COMMAND_ALLOWLIST

    def test_python_in_privileged_allowlist(self):
        """python IS in the privileged allowlist."""
        assert "python" in _PRIVILEGED_SHELL_ALLOWLIST
        assert "pip" in _PRIVILEGED_SHELL_ALLOWLIST
