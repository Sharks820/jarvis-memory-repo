"""Voice routes mixin for MobileIngestHandler.

Extracted from mobile_api.py to reduce file size. Contains voice command
validation, in-process execution, subprocess fallback, and result building.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Protocol

from jarvis_engine._constants import (
    ACTIONS_FILENAME,
    MAX_COMMAND_STDOUT_TAIL_LINES,
    MAX_COMMAND_TEXT_CHARS,
    OPS_SNAPSHOT_FILENAME,
)
from jarvis_engine._shared import make_thread_aware_repo_root
from jarvis_engine.mobile_routes._helpers import (
    MobileRouteHandlerProtocol,
    MobileRouteServerProtocol,
    _parse_bool,
    _thread_local,
)

logger = logging.getLogger(__name__)


def _unescape_response(text: str) -> str:
    """Reverse the ``response=`` line escaping applied by the CLI."""
    return text.replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")


class _VoiceServerProtocol(MobileRouteServerProtocol, Protocol):
    """Server protocol for voice routes, extending the shared base."""

    repo_root: Path


class _VoiceHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    """Handler protocol for voice routes, extending the shared base."""

    server: _VoiceServerProtocol

    def _command_failure_result(
        self,
        *,
        correlation_id: str,
        error: str,
        error_code: str,
        category: str,
        user_hint: str,
        retryable: bool,
        command_exit_code: int = 2,
        intent: str = "execution_error",
        reason: str = "",
        status_code: str = "",
        stdout_tail: list[str] | None = None,
        stderr_tail: list[str] | None = None,
        response: str = "",
    ) -> dict[str, Any]:
        ...

    def _normalize_command_output(
        self,
        *,
        response_text: str,
        stdout_lines: list[str],
    ) -> dict[str, Any]:
        ...


class VoiceRoutesMixin:
    """Voice command validation, execution, and result building.

    Extracted from MobileIngestHandler to reduce mobile_api.py line count.
    """

    # Voice command helpers (decomposed from _run_voice_command)

    def _validate_voice_text(
        self: _VoiceHandlerProtocol,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> str | dict[str, Any]:
        """Validate the ``text`` field in a voice command payload.

        Returns the cleaned text string on success, or an error-result dict
        that the caller should return immediately.
        """
        if "text" not in payload:
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Missing required field: text.",
                error_code="missing_text",
                category="validation",
                user_hint="Provide a non-empty 'text' field in the request payload.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="missing required field",
                status_code="400",
            )
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > MAX_COMMAND_TEXT_CHARS:
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Invalid text command.",
                error_code="invalid_text",
                category="validation",
                user_hint=f"Command text must be 1..{MAX_COMMAND_TEXT_CHARS} characters.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="invalid text command",
                status_code="400",
            )
        return text

    def _validate_voice_payload(
        self: _VoiceHandlerProtocol,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """Validate and parse the voice command payload.

        Returns ``None`` on success (fields stored on ``self._voice_params``)
        or an error-result dict that the caller should return immediately.
        """
        text_or_err = self._validate_voice_text(payload, correlation_id)
        if isinstance(text_or_err, dict):
            return text_or_err
        text: str = text_or_err
        root = self._root

        voice_user = str(payload.get("voice_user", "conner")).strip() or "conner"
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", voice_user):
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Invalid voice_user.",
                error_code="invalid_voice_user",
                category="validation",
                user_hint="voice_user must match [a-zA-Z0-9._-]{1,64}.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="invalid voice_user",
                status_code="400",
            )
        voice_auth_wav = str(payload.get("voice_auth_wav", "")).strip()
        if voice_auth_wav:
            try:
                wav_resolved = Path(voice_auth_wav).resolve()
                wav_resolved.relative_to(root.resolve())
            except (ValueError, OSError):
                return self._command_failure_result(
                    correlation_id=correlation_id,
                    error="voice_auth_wav path outside project root.",
                    error_code="invalid_voice_auth_path",
                    category="validation",
                    user_hint="Use a project-local path for voice_auth_wav.",
                    retryable=False,
                    command_exit_code=1,
                    intent="validation_error",
                    reason="voice_auth_wav path outside project root",
                    status_code="400",
                )

        voice_threshold_raw = payload.get("voice_threshold", 0.82)
        try:
            voice_threshold = float(voice_threshold_raw)
        except (TypeError, ValueError):
            voice_threshold = 0.82
        voice_threshold = min(0.99, max(0.1, voice_threshold))

        # Stash parsed params so the caller can access them without re-parsing.
        self._voice_params: dict[str, Any] = {  # type: ignore[attr-defined]
            "text": text,
            "execute": _parse_bool(payload.get("execute", False)),
            "approve_privileged": _parse_bool(payload.get("approve_privileged", False)),
            "speak": _parse_bool(payload.get("speak", False)),
            "voice_user": voice_user,
            "voice_auth_wav": voice_auth_wav,
            "master_password": str(payload.get("master_password", "")).strip(),
            "model_override": str(payload.get("model_override", "")).strip(),
            "voice_threshold": voice_threshold,
        }
        return None  # validation passed

    @staticmethod
    def _parse_voice_stdout(
        stdout_lines: list[str],
        default_status_code: str = "",
    ) -> dict[str, str]:
        """Extract intent/reason/status_code/response from voice command stdout."""
        intent = ""
        reason = ""
        response_text = ""
        status_code = default_status_code
        for line in stdout_lines:
            if line.startswith("intent="):
                intent = line.split("=", 1)[1]
            elif line.startswith("reason="):
                reason = line.split("=", 1)[1]
            elif line.startswith("status_code="):
                status_code = line.split("=", 1)[1]
            elif line.startswith("response="):
                raw = line.split("=", 1)[1]
                response_text = _unescape_response(raw)
        return {
            "intent": intent,
            "reason": reason,
            "status_code": status_code,
            "response_text": response_text,
        }

    def _build_voice_result(
        self: _VoiceHandlerProtocol,
        *,
        rc: int,
        correlation_id: str,
        parsed: dict[str, str],
        stdout_lines: list[str],
        stderr_lines: list[str] | None = None,
        stdout_truncated: bool = False,
    ) -> dict[str, Any]:
        """Build the final voice command result dict from parsed output."""
        normalized = self._normalize_command_output(
            response_text=parsed["response_text"],
            stdout_lines=stdout_lines[-MAX_COMMAND_STDOUT_TAIL_LINES:],
        )
        if stdout_truncated:
            normalized["stdout_truncated"] = True
        reason = parsed["reason"]
        return {
            "ok": rc == 0,
            "lifecycle_state": "completed" if rc == 0 else "failed",
            "correlation_id": correlation_id,
            "diagnostic_id": correlation_id[:12],
            "command_exit_code": rc,
            "intent": parsed["intent"],
            "response": normalized["response"],
            "response_chunks": normalized["response_chunks"],
            "response_truncated": normalized["response_truncated"],
            "status_code": parsed["status_code"],
            "reason": reason,
            "stdout_tail": normalized["stdout_tail"],
            "stdout_truncated": normalized["stdout_truncated"],
            "stderr_tail": (stderr_lines or [])[-20:],
            "error": "" if rc == 0 else (reason or "Command execution failed."),
            "error_code": "" if rc == 0 else "command_failed",
            "category": "" if rc == 0 else "execution",
            "retryable": rc != 0,
            "user_hint": "" if rc == 0 else "Retry or rephrase the request. Check diagnostic_id if it keeps failing.",
        }

    def _run_voice_in_process(
        self: _VoiceHandlerProtocol,
        params: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Execute voice command in-process via ``cmd_voice_run``."""
        import sqlite3 as _voice_sqlite3

        from jarvis_engine.mobile_routes._helpers import _ThreadCapturingStdout

        root = self._root
        try:
            import jarvis_engine.main as main_mod

            # Thread-local repo_root override -- no global lock needed.
            _thread_local.repo_root_override = root
            original_repo_root = main_mod.repo_root
            if not hasattr(main_mod, "_original_repo_root"):
                main_mod._original_repo_root = original_repo_root  # type: ignore[attr-defined]

            # Install thread-aware repo_root if not already done
            if not getattr(main_mod, "_repo_root_patched", False):
                _orig = main_mod._original_repo_root  # type: ignore[attr-defined]
                main_mod.repo_root = make_thread_aware_repo_root(_orig, _thread_local)  # type: ignore[assignment]
                main_mod._repo_root_patched = True  # type: ignore[attr-defined]

            # Per-thread stdout capture -- concurrent requests run in parallel.
            _ThreadCapturingStdout.install()
            _ThreadCapturingStdout.start_capture()
            try:
                rc = main_mod.cmd_voice_run(
                    text=params["text"],
                    execute=params["execute"],
                    approve_privileged=params["approve_privileged"],
                    speak=params["speak"],
                    snapshot_path=root / ".planning" / OPS_SNAPSHOT_FILENAME,
                    actions_path=root / ".planning" / ACTIONS_FILENAME,
                    voice_user=params["voice_user"],
                    voice_auth_wav=params["voice_auth_wav"],
                    voice_threshold=params["voice_threshold"],
                    master_password=params["master_password"],
                    model_override=params["model_override"],
                    skip_voice_auth_guard=True,
                )
            finally:
                _thread_local.repo_root_override = None
        except (RuntimeError, OSError, ValueError, TimeoutError, KeyError, TypeError, AttributeError, ImportError, _voice_sqlite3.Error) as exc:
            logger.error("Voice command execution failed: %s", exc)
            _ThreadCapturingStdout.stop_capture()  # discard
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Command execution failed.",
                error_code="execution_exception",
                category="execution",
                user_hint="Retry once. If this keeps failing, inspect diagnostic_id in server logs.",
                retryable=True,
                intent="execution_error",
                reason="internal error",
                status_code="500",
            )
        except BaseException:
            _ThreadCapturingStdout.stop_capture()  # ensure cleanup on unexpected exceptions
            raise

        stdout_text, capture_truncated = _ThreadCapturingStdout.stop_capture()
        stdout_lines = stdout_text.splitlines()
        parsed = self._parse_voice_stdout(stdout_lines, default_status_code=str(rc))
        return self._build_voice_result(
            rc=rc,
            correlation_id=correlation_id,
            parsed=parsed,
            stdout_lines=stdout_lines,
            stderr_lines=[],
            stdout_truncated=capture_truncated,
        )

    def _run_voice_subprocess(
        self: _VoiceHandlerProtocol,
        params: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Execute voice command via subprocess (fallback when in-process import fails)."""
        root = self._root
        cmd = [
            sys.executable,
            "-m",
            "jarvis_engine.main",
            "voice-run",
            "--text",
            params["text"],
            "--voice-user",
            params["voice_user"],
            "--voice-threshold",
            str(params["voice_threshold"]),
        ]
        if params["execute"]:
            cmd.append("--execute")
        if params["approve_privileged"]:
            cmd.append("--approve-privileged")
        if params["speak"]:
            cmd.append("--speak")
        if params["voice_auth_wav"]:
            cmd.extend(["--voice-auth-wav", params["voice_auth_wav"]])
        if params["model_override"]:
            cmd.extend(["--model-override", params["model_override"]])
        cmd.append("--skip-voice-auth-guard")

        engine_dir = root / "engine"
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        # NOTE: master_password is intentionally NOT passed via env var
        # (visible to any local process via /proc/*/environ).  The in-process
        # path above is the primary execution method; this subprocess fallback
        # is deprecated and does not support master_password.
        try:
            result = subprocess.run(
                cmd,
                cwd=str(engine_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error("Voice subprocess failed: %s", exc)
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Command execution failed.",
                error_code="subprocess_failure",
                category="execution",
                user_hint="Retry once. If persistent, check engine process and logs.",
                retryable=True,
                intent="execution_error",
                reason="subprocess failed",
                status_code="500",
            )

        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        parsed = self._parse_voice_stdout(stdout_lines)
        return self._build_voice_result(
            rc=result.returncode,
            correlation_id=correlation_id,
            parsed=parsed,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
        )

    # Main voice command orchestrator

    def _run_voice_command(
        self: _VoiceHandlerProtocol,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not correlation_id:
            correlation_id = uuid.uuid4().hex

        # 1. Validate and parse the payload.
        validation_error = self._validate_voice_payload(payload, correlation_id)
        if validation_error is not None:
            return validation_error
        params = self._voice_params  # type: ignore[attr-defined]

        # 2. Prefer in-process execution; fall back to subprocess.
        _can_import_in_process = True
        try:
            import jarvis_engine.main  # noqa: F401 -- probe: is the module importable?
        except ImportError:
            _can_import_in_process = False

        if _can_import_in_process:
            return self._run_voice_in_process(params, correlation_id)
        return self._run_voice_subprocess(params, correlation_id)
