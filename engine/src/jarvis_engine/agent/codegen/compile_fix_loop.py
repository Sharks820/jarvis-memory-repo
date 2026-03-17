"""Compile-test-fix loop orchestrator with KG-backed error recovery.

Autonomously compiles generated C# code, runs NUnit tests, and fixes errors
up to a configurable retry cap. CS0117/CS0619 compile errors trigger
KnowledgeGraph alternative lookups via ApiValidator instead of blind retry.

VRAM safety: play-mode entry acquires VRAMCoordinator.acquire_playmode() and
always releases in a finally block to prevent GPU contention.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis_engine.agent.codegen.api_validator import ApiValidator
    from jarvis_engine.agent.codegen.prompt_builder import UnityPromptBuilder
    from jarvis_engine.agent.tools.unity_tool import UnityTool
    from jarvis_engine.agent.vram_coordinator import VRAMCoordinator
    from jarvis_engine.gateway.models import ModelGateway

logger = logging.getLogger(__name__)

# Compiler error codes that benefit from KG alternative lookup
_KG_ERROR_CODES = {"CS0117", "CS0619"}

# Regex to extract error code from compiler error message strings
# e.g. "CS0117: ..." or "error CS0117: ..."
_RE_ERROR_CODE = re.compile(r"\b(CS\d{4})\b")

# Code fence stripping pattern (matches ```csharp / ```cs / ``` fences)
_RE_CODE_FENCE = re.compile(r"^```(?:csharp|cs)?\s*\n?([\s\S]*?)\n?```\s*$", re.DOTALL)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CompileFixResult:
    """Result of a CompileFixLoop.run() invocation.

    Attributes:
        success: True if compilation (and tests, if provided) passed.
        final_code: The C# source code after all fix iterations.
        iterations: Number of compile iterations attempted.
        errors: All compile/test error messages collected across iterations.
        warnings: Pre-validation warnings from ApiValidator.
    """

    success: bool
    final_code: str
    iterations: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Remove markdown code fence wrappers from *text*.

    Returns the inner content if *text* is wrapped in triple-backtick fences,
    otherwise returns *text* unchanged (preserving original whitespace).
    """
    match = _RE_CODE_FENCE.match(text.strip())
    if match:
        return match.group(1).strip()
    return text


def _parse_compile_errors(result: Any) -> list[str]:
    """Extract error strings from a compile() result dict.

    Handles both ``{"errors": [...]}`` and raw error string results.
    """
    if isinstance(result, dict):
        return result.get("errors") or []
    return []


def _parse_test_errors(result: Any) -> list[str]:
    """Extract error strings from a RunTests call result dict."""
    if isinstance(result, dict):
        if result.get("passed", True):
            return []
        return result.get("errors") or ["NUnit tests failed (no details)"]
    return []


# ---------------------------------------------------------------------------
# CompileFixLoop
# ---------------------------------------------------------------------------

class CompileFixLoop:
    """Autonomous compile-test-fix orchestrator with KG-backed error recovery.

    Orchestrates the following loop (up to *max_retries* times):
    1. Pre-validate code with ApiValidator.
    2. Write script (and test file if provided) via UnityTool.
    3. Compile via UnityTool.
    4. On compile errors: query KG alternatives for CS0117/CS0619, ask LLM to fix.
    5. On compile success + tests provided: run NUnit tests.
    6. On test failure: feed errors back into fix loop.
    7. On full success: enter play mode (VRAM-coordinated), exit, return result.

    Args:
        unity_tool: UnityTool WebSocket bridge client.
        gateway: ModelGateway for LLM-backed fix generation.
        validator: ApiValidator for pre-compilation checks and KG lookups.
        coordinator: VRAMCoordinator for play-mode GPU mutex.
        prompt_builder: UnityPromptBuilder for system prompt construction.
        max_retries: Maximum compile-fix iterations before giving up (default 5).
    """

    def __init__(
        self,
        unity_tool: "UnityTool",
        gateway: "ModelGateway",
        validator: "ApiValidator",
        coordinator: "VRAMCoordinator",
        prompt_builder: "UnityPromptBuilder",
        max_retries: int = 5,
    ) -> None:
        self._unity_tool = unity_tool
        self._gateway = gateway
        self._validator = validator
        self._coordinator = coordinator
        self._prompt_builder = prompt_builder
        self._max_retries = max_retries

    async def run(
        self,
        script_path: str,
        script_content: str,
        test_path: str | None = None,
        test_content: str | None = None,
    ) -> CompileFixResult:
        """Run the compile-test-fix loop.

        Args:
            script_path: Relative path of the game script (must be inside
                Assets/JarvisGenerated/).
            script_content: Initial C# source code to compile.
            test_path: Optional path of the paired NUnit test file.
            test_content: Optional C# source of the paired NUnit test file.

        Returns:
            CompileFixResult describing the outcome.
        """
        current_code = script_content
        all_errors: list[str] = []
        all_warnings: list[str] = []
        iterations = 0

        # Derive test class name from test_path for RunTests filter
        test_class_name: str | None = None
        if test_path:
            stem = re.sub(r"\.cs$", "", test_path.rsplit("/", 1)[-1])
            test_class_name = stem

        for attempt in range(1, self._max_retries + 1):
            iterations = attempt

            # --- Step 1: Pre-validate ---
            validation = self._validator.validate(current_code)
            if validation.warnings:
                all_warnings.extend(validation.warnings)

            # --- Step 2: Write script ---
            await self._unity_tool.write_script(script_path, current_code)

            # --- Step 3: Write test file if provided ---
            if test_path and test_content:
                await self._unity_tool.write_script(test_path, test_content)

            # --- Step 4: Compile ---
            compile_result = await self._unity_tool.compile()
            compile_errors = _parse_compile_errors(compile_result)

            if compile_errors:
                all_errors.extend(compile_errors)
                logger.info(
                    "CompileFixLoop attempt %d: %d compile error(s)", attempt, len(compile_errors)
                )
                current_code = await self._fix_with_llm(current_code, compile_errors)
                continue

            # --- Compilation succeeded ---
            # --- Step 5: Run NUnit tests if provided ---
            if test_path and test_class_name:
                test_result = await self._unity_tool.call(
                    "RunTests", {"testFilter": test_class_name}
                )
                test_errors = _parse_test_errors(test_result)
                if test_errors:
                    all_errors.extend(test_errors)
                    logger.info(
                        "CompileFixLoop attempt %d: %d test failure(s)", attempt, len(test_errors)
                    )
                    current_code = await self._fix_with_llm(current_code, test_errors)
                    continue

            # --- Step 6: Enter play mode with VRAM coordination ---
            await self._enter_play_mode()

            return CompileFixResult(
                success=True,
                final_code=current_code,
                iterations=iterations,
                errors=[],
                warnings=all_warnings,
            )

        # Max retries exhausted
        logger.warning("CompileFixLoop: exhausted %d retries", self._max_retries)
        return CompileFixResult(
            success=False,
            final_code=current_code,
            iterations=iterations,
            errors=all_errors,
            warnings=all_warnings,
        )

    async def _fix_with_llm(self, current_code: str, errors: list[str]) -> str:
        """Ask the LLM to fix *current_code* given *errors*.

        Returns the corrected code (fences stripped).
        """
        kg_suggestions: list[str] = []

        # Query KG for CS0117/CS0619 alternatives
        for error_msg in errors:
            code_matches = _RE_ERROR_CODE.findall(error_msg)
            for code in code_matches:
                if code in _KG_ERROR_CODES:
                    suggestion = self._validator.query_alternative(code, error_msg)
                    if suggestion:
                        kg_suggestions.append(suggestion)

        # Build fix prompt using prompt_builder for system message
        system_prompt = self._prompt_builder.build_unity_system_prompt(
            task_context="fix compile errors",
            extra_context="Fix the compilation errors listed below. Return ONLY the corrected C# code.",
        )

        # Build user message with error context and KG suggestions
        user_parts: list[str] = [
            "The following C# script has compilation/test errors that must be fixed.",
            "",
            "Current code:",
            f"```csharp\n{current_code}\n```",
            "",
            "Errors:",
        ]
        for err in errors:
            user_parts.append(f"- {err}")

        if kg_suggestions:
            user_parts.append("")
            user_parts.append("KG-backed suggestions (Unity 6.3 alternatives):")
            for suggestion in kg_suggestions:
                user_parts.append(f"- {suggestion}")

        user_parts.extend([
            "",
            "Return ONLY the complete corrected C# code, no explanation or fences.",
        ])
        user_message = "\n".join(user_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        response = self._gateway.complete(messages, route_reason="compile_fix_loop")
        raw_text = response.text if response and response.text else ""

        corrected = _strip_code_fences(raw_text)
        if not corrected:
            return current_code
        return corrected

    async def _enter_play_mode(self) -> None:
        """Enter Unity play mode with VRAM coordination.

        Acquires the coordinator mutex before entering play mode and always
        releases it in a finally block to prevent GPU leaks.
        """
        await self._coordinator.acquire_playmode()
        try:
            await self._unity_tool.call("EnterPlayMode", {})
            await self._unity_tool.call("ExitPlayMode", {})
        except Exception:  # noqa: BLE001
            logger.warning("CompileFixLoop: play-mode error (non-fatal)", exc_info=True)
        finally:
            self._coordinator.release_playmode()
