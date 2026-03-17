"""Tests for CompileFixLoop -- autonomous compile-test-fix orchestrator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPT_PATH = "Assets/JarvisGenerated/Scripts/Player.cs"
_TEST_PATH = "Assets/JarvisGenerated/Tests/PlayerTests.cs"

_GOOD_CODE = """\
using UnityEngine;

public class Player : MonoBehaviour
{
    void Start() { }
}
"""

_BAD_CODE_CS0117 = """\
using UnityEngine;

public class Player : MonoBehaviour
{
    void Start()
    {
        // Uses removed API
        var x = SomeRemovedType.OldMethod();
    }
}
"""

_FIXED_CODE = """\
using UnityEngine;

public class Player : MonoBehaviour
{
    void Start()
    {
        var x = NewType.NewMethod();
    }
}
"""


def _make_unity_tool(
    compile_errors: list[str] | None = None,
    compile_success_on_attempt: int = 1,
    test_errors: list[str] | None = None,
) -> MagicMock:
    """Build a mock UnityTool with configurable compile/test behavior.

    Args:
        compile_errors: Errors to return from compile() each call.
            None means "success on first attempt".
        compile_success_on_attempt: After this many compile() calls, return success.
        test_errors: Test errors from RunTests call. None means tests pass.
    """
    tool = MagicMock()
    tool.write_script = AsyncMock(return_value={"status": "ok"})

    call_count = [0]

    async def _compile():
        call_count[0] += 1
        if call_count[0] < compile_success_on_attempt:
            return {"errors": compile_errors or ["CS0117: some error"], "warnings": []}
        return {"errors": [], "warnings": []}

    tool.compile = _compile

    async def _call(method, params=None):
        if method == "RunTests":
            if test_errors:
                return {"passed": False, "errors": test_errors}
            return {"passed": True, "errors": []}
        # EnterPlayMode / ExitPlayMode
        return {"status": "ok"}

    tool.call = _call
    return tool


def _make_gateway(response_texts: list[str] | None = None) -> MagicMock:
    """Return a mock ModelGateway with sequential response texts."""
    gateway = MagicMock()
    responses = iter(response_texts or [_FIXED_CODE])

    def _complete(messages, route_reason=""):
        resp = MagicMock()
        try:
            resp.text = next(responses)
        except StopIteration:
            resp.text = _FIXED_CODE
        return resp

    gateway.complete.side_effect = _complete
    return gateway


def _make_validator(alternative: str | None = None) -> MagicMock:
    """Return a mock ApiValidator."""
    validator = MagicMock()
    from jarvis_engine.agent.codegen.api_validator import ValidationResult
    validator.validate.return_value = ValidationResult(warnings=[], suggestions=[])
    validator.query_alternative.return_value = alternative
    return validator


def _make_coordinator() -> MagicMock:
    """Return a mock VRAMCoordinator."""
    coord = MagicMock()
    coord.acquire_playmode = AsyncMock()
    coord.release_playmode = MagicMock()
    return coord


def _make_prompt_builder(system_prompt: str = "Unity 6.3 system prompt") -> MagicMock:
    builder = MagicMock()
    builder.build_unity_system_prompt.return_value = system_prompt
    return builder


def _run(coro):
    """Run a coroutine synchronously (project convention: asyncio.run)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestImports:
    def test_import_compile_fix_loop(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop  # noqa: F401

    def test_import_compile_fix_result(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixResult  # noqa: F401

    def test_compile_fix_result_is_dataclass(self):
        import dataclasses
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixResult
        assert dataclasses.is_dataclass(CompileFixResult)

    def test_compile_fix_result_fields(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixResult
        r = CompileFixResult(
            success=True,
            final_code="code",
            iterations=1,
            errors=[],
            warnings=["w"],
        )
        assert r.success is True
        assert r.final_code == "code"
        assert r.iterations == 1
        assert r.errors == []
        assert r.warnings == ["w"]

    def test_compile_fix_loop_instantiates(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        assert loop is not None

    def test_max_retries_default_is_five(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        assert loop._max_retries == 5


# ---------------------------------------------------------------------------
# Happy path: clean compile, no tests
# ---------------------------------------------------------------------------

class TestCleanCompile:
    def test_clean_compile_returns_success_true(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert result.success is True

    def test_clean_compile_iterations_is_one(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert result.iterations == 1

    def test_clean_compile_no_errors(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert result.errors == []

    def test_clean_compile_write_script_called(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        tool = _make_unity_tool(compile_success_on_attempt=1)
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        tool.write_script.assert_called()

    def test_clean_compile_final_code_is_original(self):
        """On clean first compile, final_code should be the original code."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert result.final_code == _GOOD_CODE


# ---------------------------------------------------------------------------
# Error recovery: CS0117 / CS0619
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    def test_cs0117_queries_validator(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: 'SomeType' does not contain 'OldMethod'"],
            compile_success_on_attempt=2,
        )
        validator = _make_validator(alternative="Use NewType.NewMethod() instead")
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE]),
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        # validator.query_alternative should have been called for CS0117
        validator.query_alternative.assert_called()
        assert result.success is True

    def test_cs0117_includes_kg_suggestion_in_fix_prompt(self):
        """The fix prompt sent to the LLM must include the KG suggestion."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: 'SomeType' does not contain 'OldMethod'"],
            compile_success_on_attempt=2,
        )
        validator = _make_validator(alternative="Use NewType instead (Unity 6.3)")
        gateway = _make_gateway([_FIXED_CODE])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        # gateway.complete was called -- check the messages contain the KG suggestion
        gateway.complete.assert_called()
        call_args = gateway.complete.call_args
        messages = call_args[0][0]
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "NewType" in all_content or "Unity 6.3" in all_content

    def test_cs0619_queries_validator(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0619: 'OldApi' is obsolete"],
            compile_success_on_attempt=2,
        )
        validator = _make_validator(alternative="Use NewApi instead")
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE]),
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        validator.query_alternative.assert_called()

    def test_non_cs0117_error_does_not_query_validator(self):
        """Non-CS0117/CS0619 errors should NOT call query_alternative."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0246: type not found"],
            compile_success_on_attempt=2,
        )
        validator = _make_validator()
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE]),
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        validator.query_alternative.assert_not_called()

    def test_fix_attempt_updates_final_code(self):
        """After a successful fix, final_code should be the corrected code."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=2,
        )
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE]),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert result.success is True
        assert result.final_code == _FIXED_CODE


# ---------------------------------------------------------------------------
# Max retries exhausted
# ---------------------------------------------------------------------------

class TestMaxRetries:
    def test_five_failures_returns_success_false(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        # Always fails -- success never comes
        tool = _make_unity_tool(
            compile_errors=["CS0117: persistent error"],
            compile_success_on_attempt=999,
        )
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE] * 10),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
            max_retries=5,
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert result.success is False

    def test_five_failures_iterations_is_five(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: persistent error"],
            compile_success_on_attempt=999,
        )
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE] * 10),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
            max_retries=5,
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert result.iterations == 5

    def test_five_failures_errors_collected(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: persistent error"],
            compile_success_on_attempt=999,
        )
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE] * 10),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
            max_retries=5,
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        # Errors from all iterations should be collected
        assert len(result.errors) >= 1

    def test_custom_max_retries(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=999,
        )
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway([_FIXED_CODE] * 10),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
            max_retries=3,
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert result.success is False
        assert result.iterations == 3


# ---------------------------------------------------------------------------
# Play-mode entry with VRAM coordinator
# ---------------------------------------------------------------------------

class TestPlayMode:
    def test_clean_compile_acquires_playmode(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        coordinator = _make_coordinator()
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=coordinator,
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        coordinator.acquire_playmode.assert_awaited_once()

    def test_clean_compile_releases_playmode(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        coordinator = _make_coordinator()
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=coordinator,
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        coordinator.release_playmode.assert_called_once()

    def test_playmode_released_even_on_play_error(self):
        """release_playmode must be called even if play-mode errors."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        coordinator = _make_coordinator()
        tool = _make_unity_tool(compile_success_on_attempt=1)

        # Make EnterPlayMode raise
        original_call = tool.call

        async def _failing_call(method, params=None):
            if method == "EnterPlayMode":
                raise RuntimeError("Play mode failed")
            return await original_call(method, params)

        tool.call = _failing_call

        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=coordinator,
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        # release_playmode must still be called
        coordinator.release_playmode.assert_called()

    def test_playmode_enter_and_exit_called(self):
        """Both EnterPlayMode and ExitPlayMode are called via unity_tool.call."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(compile_success_on_attempt=1)
        called_methods = []

        original_call = tool.call

        async def _tracking_call(method, params=None):
            called_methods.append(method)
            return await original_call(method, params)

        tool.call = _tracking_call

        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert "EnterPlayMode" in called_methods
        assert "ExitPlayMode" in called_methods


# ---------------------------------------------------------------------------
# With NUnit test file
# ---------------------------------------------------------------------------

class TestWithTestFile:
    def test_test_file_is_written(self):
        """When test_path/test_content provided, write_script is called for both."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(compile_success_on_attempt=1)
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        test_content = "// test content"
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE, test_path=_TEST_PATH, test_content=test_content))
        # write_script should be called for both script and test
        calls = tool.write_script.call_args_list
        paths_written = [c.args[0] if c.args else c.kwargs.get("rel_path") for c in calls]
        assert _SCRIPT_PATH in paths_written
        assert _TEST_PATH in paths_written

    def test_test_runner_called_after_compile(self):
        """RunTests is called via unity_tool.call after successful compilation."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(compile_success_on_attempt=1)
        called_methods = []

        original_call = tool.call

        async def _tracking_call(method, params=None):
            called_methods.append(method)
            return await original_call(method, params)

        tool.call = _tracking_call

        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE, test_path=_TEST_PATH, test_content="// test"))
        assert "RunTests" in called_methods

    def test_test_failure_feeds_back_into_loop(self):
        """Test failures trigger another fix iteration."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        # Compile succeeds on first attempt, but tests fail first time
        call_count = [0]

        tool = MagicMock()
        tool.write_script = AsyncMock(return_value={"status": "ok"})

        async def _compile():
            return {"errors": [], "warnings": []}

        tool.compile = _compile

        test_fail_count = [0]

        async def _call(method, params=None):
            if method == "RunTests":
                test_fail_count[0] += 1
                if test_fail_count[0] < 2:
                    return {"passed": False, "errors": ["NUnit test 'Player_Exists' failed"]}
                return {"passed": True, "errors": []}
            return {"status": "ok"}

        tool.call = _call

        gateway = _make_gateway([_FIXED_CODE, _FIXED_CODE])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE, test_path=_TEST_PATH, test_content="// t"))
        # LLM was called at least once to fix the test failure
        gateway.complete.assert_called()

    def test_passing_tests_success_true(self):
        """When tests pass, result is success=True."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(compile_success_on_attempt=1)
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=_make_gateway(),
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE, test_path=_TEST_PATH, test_content="// t"))
        assert result.success is True


# ---------------------------------------------------------------------------
# Prompt builder integration
# ---------------------------------------------------------------------------

class TestPromptBuilder:
    def test_prompt_builder_used_in_fix_prompt(self):
        """build_unity_system_prompt is called when building fix messages."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=2,
        )
        prompt_builder = _make_prompt_builder("CUSTOM SYSTEM PROMPT")
        gateway = _make_gateway([_FIXED_CODE])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=prompt_builder,
        )
        _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        prompt_builder.build_unity_system_prompt.assert_called()

    def test_system_prompt_in_gateway_messages(self):
        """The gateway.complete messages must include the system prompt."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=2,
        )
        gateway = _make_gateway([_FIXED_CODE])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder("CUSTOM SYSTEM PROMPT"),
        )
        _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        call_args = gateway.complete.call_args
        messages = call_args[0][0]
        system_messages = [m for m in messages if m.get("role") == "system"]
        assert any("CUSTOM SYSTEM PROMPT" in m.get("content", "") for m in system_messages)


# ---------------------------------------------------------------------------
# Pre-validation (warnings collection)
# ---------------------------------------------------------------------------

class TestPreValidation:
    def test_validator_validate_called(self):
        """validator.validate() is called before writing the script."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        validator = _make_validator()
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        validator.validate.assert_called()

    def test_validator_warnings_in_result(self):
        """Warnings from validator.validate should appear in CompileFixResult.warnings."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop
        from jarvis_engine.agent.codegen.api_validator import ValidationResult

        validator = _make_validator()
        validator.validate.return_value = ValidationResult(
            warnings=["use SerializeField correctly"], suggestions=[]
        )
        loop = CompileFixLoop(
            unity_tool=_make_unity_tool(compile_success_on_attempt=1),
            gateway=_make_gateway(),
            validator=validator,
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _GOOD_CODE))
        assert "use SerializeField correctly" in result.warnings


# ---------------------------------------------------------------------------
# Code fence stripping in LLM response
# ---------------------------------------------------------------------------

class TestCodeFenceStripping:
    def test_llm_response_with_csharp_fence_stripped(self):
        """CompileFixLoop should strip ```csharp fences from LLM fix responses."""
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        fenced_code = f"```csharp\n{_FIXED_CODE}\n```"
        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=2,
        )
        gateway = _make_gateway([fenced_code])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert "```" not in result.final_code

    def test_llm_response_without_fence_unchanged(self):
        from jarvis_engine.agent.codegen.compile_fix_loop import CompileFixLoop

        tool = _make_unity_tool(
            compile_errors=["CS0117: error"],
            compile_success_on_attempt=2,
        )
        gateway = _make_gateway([_FIXED_CODE])
        loop = CompileFixLoop(
            unity_tool=tool,
            gateway=gateway,
            validator=_make_validator(),
            coordinator=_make_coordinator(),
            prompt_builder=_make_prompt_builder(),
        )
        result = _run(loop.run(_SCRIPT_PATH, _BAD_CODE_CS0117))
        assert _FIXED_CODE.strip() in result.final_code
