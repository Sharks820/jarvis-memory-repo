---
phase: 23-csharp-code-generation
plan: "02"
subsystem: agent/codegen
tags: [unity, codegen, nunit, compile-fix-loop, vram-coordination, kg-error-recovery]
dependency_graph:
  requires:
    - 23-01 (ApiValidator.query_alternative, UnityPromptBuilder.build_unity_system_prompt)
    - 21-02 (UnityTool.compile, UnityTool.write_script, UnityTool.call)
    - 21-01 (VRAMCoordinator.acquire_playmode / release_playmode)
  provides:
    - agent/codegen/nunit_generator.py -- NUnitGenerator, generate_nunit_test
    - agent/codegen/compile_fix_loop.py -- CompileFixLoop, CompileFixResult
  affects:
    - Phase 25 (agent orchestration) -- CompileFixLoop closes the code generation loop
    - Phase 24 (3D asset pipeline) -- NUnitGenerator pattern reusable for Blender script tests
tech_stack:
  added: []
  patterns:
    - TYPE_CHECKING guard for all cross-module imports (consistent with project pattern)
    - Dataclass for result type (CompileFixResult)
    - asyncio.run() pattern for async tests (no pytest-asyncio, matches project convention)
    - try/finally for VRAM coordinator release (prevents GPU mutex leaks)
    - Module-level convenience functions wrapping class methods
    - Code fence stripping for LLM responses (```csharp / ``` triple-backtick)
key_files:
  created:
    - engine/src/jarvis_engine/agent/codegen/nunit_generator.py
    - engine/src/jarvis_engine/agent/codegen/compile_fix_loop.py
    - engine/tests/test_codegen_nunit_generator.py
    - engine/tests/test_codegen_compile_fix_loop.py
  modified: []
decisions:
  - "NUnitGenerator falls back to structural scaffold when LLM response is empty -- avoids silent failures that produce no test file"
  - "CompileFixLoop releases playmode in finally block even on EnterPlayMode error -- GPU mutex cannot be held on error"
  - "_strip_code_fences returns original text unchanged when no fences present (not stripped) -- preserves trailing newlines in LLM responses for clean final_code comparison"
  - "CS0117/CS0619 triggers query_alternative() per error message (not per error code) -- same code can appear multiple times with different type references"
metrics:
  duration_minutes: 14
  completed_date: "2026-03-17"
  tasks_completed: 2
  files_created: 4
  tests_added: 70
---

# Phase 23 Plan 02: NUnitGenerator + CompileFixLoop Summary

**One-liner:** NUnit test scaffolding generator with LLM fallback and autonomous compile-test-fix orchestrator using KG-backed CS0117/CS0619 error recovery and VRAM-coordinated play-mode entry.

## What Was Built

Two modules completing the autonomous code generation loop in `agent/codegen/`:

### NUnitGenerator (`nunit_generator.py`)

Generates paired NUnit test files for Unity game scripts:

- **Path convention**: `Assets/JarvisGenerated/Scripts/{Name}.cs` → `Assets/JarvisGenerated/Tests/{Name}Tests.cs`. Falls back to `Assets/JarvisGenerated/Tests/` root if no `Scripts/` directory in path.
- **Class name extraction**: regex `\bclass\s+(\w+)` extracts primary class name; falls back to filename stem.
- **Scaffold mode** (no gateway): produces `[TestFixture]` class with `[Test]` method (`_Exists()` using `AddComponent<T>`) and `[UnityTest]` `IEnumerator` coroutine (`_StartsCorrectly()` yielding one frame and asserting `component.enabled`).
- **LLM mode** (with gateway): calls `gateway.complete()` with Unity NUnit prompt; strips markdown code fences; falls back to scaffold if LLM returns empty.
- **Module-level** `generate_nunit_test()` convenience function.

### CompileFixLoop (`compile_fix_loop.py`)

Orchestrates compile-test-fix loop up to `max_retries` (default 5):

1. **Pre-validate** code with `ApiValidator.validate()` to collect pre-compilation warnings.
2. **Write script** (and test file if provided) via `unity_tool.write_script()`.
3. **Compile** via `unity_tool.compile()`.
4. **On errors**: extract CS0117/CS0619 codes, call `validator.query_alternative()` for each, build fix prompt using `prompt_builder.build_unity_system_prompt()` as system message, call LLM, strip fences, retry.
5. **On compile success + test file**: call `unity_tool.call("RunTests", ...)` and feed test failures back into fix loop.
6. **On full success**: acquire `coordinator.acquire_playmode()`, enter/exit play mode via `unity_tool.call()`, always release in `finally` block.
7. **After max_retries**: return `CompileFixResult(success=False, ...)` with all collected errors.

**CompileFixResult** dataclass: `success`, `final_code`, `iterations`, `errors`, `warnings`.

## Test Results

- 70 new tests: 36 nunit_generator + 34 compile_fix_loop
- Full suite: 6347 passing, 10 skipped (was 6277)
- ruff: clean on `engine/src/jarvis_engine/agent/codegen/`
- No regressions

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Code fence stripping preserved original whitespace**
- **Found during:** Task 2 (TestErrorRecovery::test_fix_attempt_updates_final_code)
- **Issue:** Initial `_strip_code_fences()` called `.strip()` on text before checking for fences, which stripped trailing newlines from LLM responses. This caused `final_code != _FIXED_CODE` comparison failures when LLM returned code without fences.
- **Fix:** Return `text` unchanged (not `text.strip()`) when no fences found; only strip inner content when fences are present.
- **Files modified:** `engine/src/jarvis_engine/agent/codegen/compile_fix_loop.py`
- **Commit:** 42e0a42a

**2. [Rule 1 - Bug] Removed dead `_extract_error_codes` helper**
- **Found during:** Task 2 (ruff check)
- **Issue:** `_extract_error_codes()` helper was defined but became unused after inlining error code extraction into `_fix_with_llm()`. Ruff flagged the assigned result variable as unused; function itself was dead code.
- **Fix:** Removed the unused helper function.
- **Files modified:** `engine/src/jarvis_engine/agent/codegen/compile_fix_loop.py`
- **Commit:** 42e0a42a (same commit, part of GREEN phase)

## Self-Check: PASSED
