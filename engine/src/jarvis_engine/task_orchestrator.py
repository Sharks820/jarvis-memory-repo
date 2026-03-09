from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError

from jarvis_engine.gateway.ollama_client import call_ollama_generate
from jarvis_engine.adapters import ImageAdapter, Model3DAdapter, VideoAdapter
from jarvis_engine.capability import CapabilityGate
from jarvis_engine.memory_store import MemoryStore

TaskType = Literal["image", "code", "video", "model3d"]
DEFAULT_FALLBACK_MODELS = ["qwen3:14b", "qwen3:latest", "deepseek-r1:8b"]


@dataclass
class TaskRequest:
    task_type: TaskType
    prompt: str
    execute: bool
    has_explicit_approval: bool
    model: str
    endpoint: str
    quality_profile: str = "max_quality"
    output_path: str | None = None


@dataclass
class TaskResult:
    allowed: bool
    provider: str
    plan: str
    output_text: str = ""
    output_path: str = ""
    reason: str = ""


class TaskOrchestrator:
    def __init__(self, store: MemoryStore, root: Path) -> None:
        self._gate = CapabilityGate()
        self._store = store
        self._root = root.resolve()
        self._adapters = {
            "image": ImageAdapter(root),
            "video": VideoAdapter(root),
            "model3d": Model3DAdapter(root),
        }

    def run(self, request: TaskRequest) -> TaskResult:
        action_class = "bounded_write"
        if request.task_type in {"video", "model3d"}:
            action_class = "privileged"

        decision = self._gate.authorize(
            action_class=action_class,
            has_explicit_approval=request.has_explicit_approval,
            task_requires_expansion=False,
        )
        if not decision.allowed:
            result = TaskResult(
                allowed=False,
                provider="policy_gate",
                plan="Execution blocked by capability gate.",
                reason=decision.reason,
            )
            self._log(request, result)
            return result

        if request.task_type == "code":
            result = self._run_code_task(request)
            self._log(request, result)
            return result

        result = self._run_adapter_task(request)
        self._log(request, result)
        return result

    def _try_generate_with_fallback(
        self,
        endpoint: str,
        candidate_models: list[str],
        prompt: str,
        options: dict,
        timeout_s: int,
    ) -> tuple[str, str, list[str]]:
        """Try each model in *candidate_models*. Returns (output, chosen_model, errors)."""
        errors: list[str] = []
        for model in candidate_models:
            raw, err = self._call_ollama(
                endpoint=endpoint, model=model, prompt=prompt,
                options=options, timeout_s=timeout_s,
            )
            if err:
                errors.append(f"{model}: {err}")
                continue
            if not raw:
                errors.append(f"{model}: empty response payload.")
                continue
            output = self._extract_output(raw)
            if output:
                return output, model, errors
            raw_error = str(raw.get("error", "")).strip()
            errors.append(f"{model}: {raw_error}" if raw_error else f"{model}: empty model output.")
        return "", "", errors

    def _refine_code_output(self, endpoint: str, model: str, request: TaskRequest, output: str) -> str:
        """Apply critique-revise cycle and syntax fixing for max_quality."""
        critique = self._single_pass_generate(
            endpoint=endpoint, model=model,
            prompt=self._critique_prompt(output),
            options={"num_ctx": 32768, "num_predict": 1200, "temperature": 0.0, "top_p": 0.9},
            timeout_s=240,
        )
        if critique:
            revised = self._single_pass_generate(
                endpoint=endpoint, model=model,
                prompt=self._revision_prompt(original=request.prompt, draft_code=output, critique=critique),
                options={"num_ctx": 32768, "num_predict": 3072, "temperature": 0.03, "top_p": 0.9},
                timeout_s=360,
            )
            if revised:
                output = revised

        if self._looks_like_python(request.prompt, request.output_path):
            syntax_issue = self._python_syntax_issue(output)
            if syntax_issue:
                fixed = self._single_pass_generate(
                    endpoint=endpoint, model=model,
                    prompt=self._python_fix_prompt(output, syntax_issue),
                    options={"num_ctx": 32768, "num_predict": 3072, "temperature": 0.0, "top_p": 0.85},
                    timeout_s=240,
                )
                if fixed and not self._python_syntax_issue(fixed):
                    output = fixed
        return output

    def _run_code_task(self, request: TaskRequest) -> TaskResult:
        candidate_models = self._model_candidates(request.model)
        plan = (
            f"Generate high-quality code using endpoint={request.endpoint} "
            f"with model fallback chain={candidate_models}."
        )
        if not request.execute:
            return TaskResult(allowed=True, provider="ollama",
                              plan=plan + " Dry-run only.", reason="Set --execute to run generation.")

        timeout_s = 360 if request.quality_profile == "max_quality" else 180
        output, chosen_model, errors = self._try_generate_with_fallback(
            request.endpoint, candidate_models,
            self._compose_code_prompt(request.prompt, request.quality_profile),
            self._quality_options(request.quality_profile), timeout_s,
        )
        if not output:
            reason = " | ".join(errors[:4]) if errors else "No model produced output."
            return TaskResult(allowed=False, provider="ollama", plan=plan, reason=reason)

        if request.quality_profile == "max_quality":
            output = self._refine_code_output(request.endpoint, chosen_model, request, output)

        path = request.output_path or ""
        if path and output:
            try:
                p = self._safe_output_path(path)
            except ValueError as exc:
                return TaskResult(allowed=False, provider="ollama", plan=plan, reason=str(exc))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(output, encoding="utf-8")
            path = str(p)

        return TaskResult(
            allowed=True, provider="ollama", plan=plan,
            output_text=output, output_path=path,
            reason=f"Code generation completed with model={chosen_model}.",
        )

    def _run_adapter_task(self, request: TaskRequest) -> TaskResult:
        adapter = self._adapters.get(request.task_type)
        if not adapter:
            return TaskResult(
                allowed=False,
                provider="adapter",
                plan=f"No adapter found for type={request.task_type}",
                reason="Missing adapter.",
            )

        plan = adapter.plan(request.prompt)
        if not request.execute:
            return TaskResult(
                allowed=True,
                provider=adapter.provider,
                plan=plan + " Dry-run only.",
                reason="Set --execute to run adapter.",
            )

        safe_output_path: str | None = request.output_path
        if request.output_path:
            try:
                safe_output_path = str(self._safe_output_path(request.output_path))
            except ValueError as exc:
                return TaskResult(
                    allowed=False,
                    provider=adapter.provider,
                    plan=plan,
                    reason=str(exc),
                )

        result = adapter.execute(request.prompt, safe_output_path, request.quality_profile)
        return TaskResult(
            allowed=result.ok,
            provider=result.provider,
            plan=result.plan,
            output_text=result.output_text,
            output_path=result.output_path,
            reason=result.reason,
        )

    def _model_candidates(self, primary: str) -> list[str]:
        raw_fallbacks = os.getenv("JARVIS_CODE_MODEL_FALLBACKS", "")
        env_fallbacks = [x.strip() for x in raw_fallbacks.split(",") if x.strip()]
        candidates = [primary.strip()] + (env_fallbacks if env_fallbacks else DEFAULT_FALLBACK_MODELS)
        seen: set[str] = set()
        out: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
        return out

    def _quality_options(self, quality_profile: str) -> dict[str, Any]:
        if quality_profile == "max_quality":
            return {
                "num_ctx": 32768,
                "num_predict": 3072,
                "temperature": 0.05,
                "top_p": 0.9,
                "top_k": 40,
                "min_p": 0.05,
                "repeat_penalty": 1.08,
            }
        if quality_profile == "balanced":
            return {
                "num_ctx": 16384,
                "num_predict": 1536,
                "temperature": 0.12,
                "top_p": 0.92,
                "top_k": 40,
                "repeat_penalty": 1.05,
            }
        return {
            "num_ctx": 8192,
            "num_predict": 768,
            "temperature": 0.2,
            "top_p": 0.95,
            "top_k": 30,
            "repeat_penalty": 1.02,
        }

    _MAX_PROMPT_CHARS = 24000

    def _compose_code_prompt(self, prompt: str, quality_profile: str) -> str:
        truncated = prompt[: self._MAX_PROMPT_CHARS]
        if quality_profile == "max_quality":
            return (
                "You are a principal software engineer. Produce production-grade code.\n"
                "Requirements:\n"
                "- Return only code, no prose.\n"
                "- Include robust input validation and error handling.\n"
                "- Keep code maintainable and performant.\n"
                "- Prefer deterministic behavior where possible.\n\n"
                f"Task:\n{truncated}"
            )
        return truncated

    def _critique_prompt(self, draft_code: str) -> str:
        return (
            "Review this code and return a concise checklist of concrete issues only.\n"
            "Focus on correctness, edge cases, security, and performance.\n\n"
            f"{draft_code}"
        )

    def _revision_prompt(self, original: str, draft_code: str, critique: str) -> str:
        return (
            "Rewrite the code to fully resolve the checklist.\n"
            "Return only final code with no markdown fences and no explanation.\n\n"
            "Original request:\n"
            f"{original}\n\n"
            "Checklist:\n"
            f"{critique}\n\n"
            "Code to improve:\n"
            f"{draft_code}"
        )

    def _python_fix_prompt(self, code: str, syntax_issue: str) -> str:
        return (
            "Fix this Python code so it compiles cleanly and preserves behavior.\n"
            "Return only corrected Python code.\n\n"
            f"SyntaxError: {syntax_issue}\n\n"
            f"{code}"
        )

    def _looks_like_python(self, prompt: str, output_path: str | None) -> bool:
        if output_path and output_path.lower().endswith(".py"):
            return True
        lowered = prompt.lower()
        return "python" in lowered or "pytest" in lowered

    def _python_syntax_issue(self, code: str) -> str:
        import ast
        try:
            ast.parse(code, filename="<jarvis_generated>")
            return ""
        except SyntaxError as exc:
            return f"{exc.msg} (line {exc.lineno})"

    def _extract_output(self, raw: dict[str, Any]) -> str:
        response = str(raw.get("response", "")).strip()
        return response

    def _single_pass_generate(
        self,
        *,
        endpoint: str,
        model: str,
        prompt: str,
        options: dict[str, Any],
        timeout_s: int,
    ) -> str:
        raw, _ = self._call_ollama(
            endpoint=endpoint,
            model=model,
            prompt=prompt,
            options=options,
            timeout_s=timeout_s,
        )
        if not raw:
            return ""
        return self._extract_output(raw)

    def _call_ollama(
        self,
        *,
        endpoint: str,
        model: str,
        prompt: str,
        options: dict[str, Any],
        timeout_s: int,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            data = call_ollama_generate(
                endpoint, model, prompt, options, timeout_s=timeout_s,
            )
            return data, ""
        except json.JSONDecodeError:
            return None, "Invalid JSON response from Ollama."
        except ValueError as exc:
            return None, str(exc)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return None, f"HTTP {exc.code}: {body[:240]}"
        except URLError as exc:
            return None, f"Network error: {exc}"
        except TimeoutError:
            return None, f"Timed out after {timeout_s}s."

    def _safe_output_path(self, raw_path: str) -> Path:
        try:
            path = Path(raw_path).expanduser()
        except RuntimeError:
            raise ValueError("Cannot expand user home directory in output path.")
        if path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (self._root / path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise ValueError("Security policy: output path must remain inside the repository root.")
        return resolved

    def _log(self, request: TaskRequest, result: TaskResult) -> None:
        message = (
            f"type={request.task_type} execute={request.execute} allowed={result.allowed} "
            f"provider={result.provider} reason={result.reason}"
        )
        self._store.append(event_type="task_orchestrator", message=message)


_SHELL_COMMAND_ALLOWLIST = {"git", "npm", "node", "pytest", "jarvis"}
_PRIVILEGED_SHELL_ALLOWLIST = {"python", "python3", "pip", "pip3"}


def run_shell_command(
    command: str, timeout_s: int = 60, *, has_explicit_approval: bool = False,
) -> tuple[int, str, str]:
    try:
        args = shlex.split(command, posix=False)
    except ValueError as exc:
        return 2, "", f"Invalid command syntax: {exc}"
    if not args:
        return 2, "", "Empty command."
    executable = Path(args[0]).stem.lower()
    if executable in _PRIVILEGED_SHELL_ALLOWLIST:
        if not has_explicit_approval:
            return 2, "", (
                f"Command '{args[0]}' requires explicit approval "
                f"(privileged allowlist: {sorted(_PRIVILEGED_SHELL_ALLOWLIST)})"
            )
    elif executable not in _SHELL_COMMAND_ALLOWLIST:
        return 2, "", f"Command '{args[0]}' not in allowlist: {sorted(_SHELL_COMMAND_ALLOWLIST | _PRIVILEGED_SHELL_ALLOWLIST)}"
    try:
        proc = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        detail = stderr or f"Command timed out after {timeout_s}s."
        return 124, stdout, detail
