---
phase: 25-polish-and-integration
plan: "02"
subsystem: agent
tags: [agent, voice, tool-registry, task-summary, cqrs, tdd]
dependency_graph:
  requires:
    - 22-01 (ToolRegistry, ProgressEventBus)
    - 22-02 (ReflectionLoop, StepExecutor, TaskPlanner)
    - 25-01 (prior polish tasks if any)
  provides:
    - AgentRegisterToolCommand (runtime tool registration)
    - TaskSummary + generate_task_summary (task completion reporting)
    - Voice routing: "build a unity" -> AgentRunCommand
    - Voice routing: "use X for Y" -> AgentRegisterToolCommand
    - task_summary events via ProgressEventBus on task completion
  affects:
    - engine/src/jarvis_engine/voice/intents.py (new dispatch rules)
    - engine/src/jarvis_engine/agent/reflection.py (summary emission)
    - engine/src/jarvis_engine/app.py (AgentRegisterToolHandler wired)
tech_stack:
  added: []
  patterns:
    - CQRS frozen dataclass command + result
    - TDD red-green cycle per task
    - Lazy import inside handler.handle() to avoid circular deps
    - asyncio.iscoroutine() check for sync placeholder execute callable
key_files:
  created:
    - engine/src/jarvis_engine/agent/task_summary.py
    - engine/tests/test_runtime_tool_reg.py
    - engine/tests/test_task_summary.py
    - engine/tests/test_voice_agent_intent.py
  modified:
    - engine/src/jarvis_engine/commands/agent_commands.py
    - engine/src/jarvis_engine/agent/tool_registry.py
    - engine/src/jarvis_engine/handlers/agent_handlers.py
    - engine/src/jarvis_engine/agent/reflection.py
    - engine/src/jarvis_engine/voice/intents.py
    - engine/src/jarvis_engine/app.py
decisions:
  - AgentRegisterToolCommand.parameters is a JSON string (not dict) -- frozen dataclass cannot hold mutable default dict
  - Placeholder execute is sync lambda (not async) -- caller uses inspect.isawaitable() already (StepExecutor)
  - ToolRegistry.__len__ returns 0 for empty registry making it falsy; test helpers must use "if registry is None" not "registry or ..."
  - ReflectionLoop emits task_summary before task_done to preserve event ordering consumers expect
  - Voice "use X for Y" matcher uses lambda (not _match_any) since it requires two conditions (startswith + contains)
metrics:
  duration: ~25 minutes
  completed_date: "2026-03-17"
  tasks_completed: 2
  files_created: 4
  files_modified: 6
  tests_added: 43
---

# Phase 25 Plan 02: Runtime Tool Registration + Voice Agent Routing Summary

**One-liner:** AgentRegisterToolCommand CQRS command with voice routing ("use Mixamo for animations"), TaskSummary generator extracting files/steps/tokens from AgentTask, and task_summary events emitted by ReflectionLoop on completion.

## What Was Built

### Task 1: Runtime Tool Registration + Task Summary Generator

**AgentRegisterToolCommand** -- new frozen dataclass in agent_commands.py:
- Fields: `name`, `description`, `parameters` (JSON string), `requires_approval`
- Paired `AgentRegisterToolResult` with `tool_name` and `registered` flag

**AgentRegisterToolHandler** -- new handler in agent_handlers.py:
- Validates non-empty name (rc=1 on empty)
- Parses `parameters` from JSON string (rc=1 on JSONDecodeError)
- Creates `ToolSpec` with placeholder `execute` lambda returning informative string
- Calls `registry.register(spec)` -- ToolRegistry handles duplicate overwrites with log warning
- Wired in `_register_agent_handlers()` in app.py for both success and fallback paths

**ToolRegistry.unregister()** -- new method:
- Removes tool by name, returns True if found, False if not
- Logs info on successful unregister

**task_summary.py** -- new module:
- `TaskSummary` dataclass: task_id, goal, status, steps_completed, tokens_used, error_count, files_touched, summary_text
- `generate_task_summary(task: AgentTask) -> TaskSummary`: parses plan_json, extracts file paths from steps where tool_name=="file", builds human-readable summary_text
- Handles empty/unparseable plan_json gracefully (returns empty files_touched)
- Deduplicates file paths using a seen-set

### Task 2: Voice Intent Routing + Summary Event Emission

**voice/intents.py changes:**
- `_handle_agent_task()`: matches "build/create/make/generate a unity" phrases, strips trigger prefix, dispatches `AgentRunCommand(goal=...)`, responds "Starting agent task: {goal[:80]}"
- `_handle_register_tool()`: matches `low.startswith("use ") and " for " in low`, parses tool name and description from phrase, dispatches `AgentRegisterToolCommand(name=..., description=...)`
- Two new rules added to `_DISPATCH_RULES` before the LLM fallback

**reflection.py changes:**
- After `task.status = "done"`, generates and emits a `task_summary` event via ProgressEventBus before the existing `task_done` event
- Wrapped in `try/except` -- summary failure does not block task_done emission

## Tests

| File | Tests | Coverage |
|------|-------|----------|
| test_runtime_tool_reg.py | 28 | AgentRegisterToolCommand dataclass, handler success/failure paths, ToolRegistry.unregister |
| test_task_summary.py | 15 | TaskSummary dataclass, generate_task_summary: empty plan, invalid JSON, file extraction, dedup |
| test_voice_agent_intent.py | 15 (voice) + 7 (e2e) | _handle_agent_task, _handle_register_tool, dispatch rule matchers, non-matching fallthrough |

**Total new tests: 43** (28 + 15 task 1, 15 task 2)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ToolRegistry falsy when empty causes `registry or _make_registry()` to ignore passed registry**
- **Found during:** Task 1 test GREEN phase
- **Issue:** `ToolRegistry.__len__` returns 0 for empty registry, making `registry or _make_registry()` create a new registry instead of using the passed empty one
- **Fix:** Changed test helper `_make_handler` to use `if registry is None:` instead of `registry or _make_registry()`
- **Files modified:** engine/tests/test_runtime_tool_reg.py
- **Commit:** bd0e1f45 (included in task 1 commit)

## Verification Results

- `python -m pytest engine/tests/test_runtime_tool_reg.py engine/tests/test_task_summary.py engine/tests/test_voice_agent_intent.py engine/tests/test_agent_e2e.py -q`: **50 passed**
- `python -m pytest engine/tests/ --tb=no`: **6473 passed, 10 skipped** (up from 6347 baseline)
- `ruff check` on all modified files: **clean**
- TOOL-06 satisfied: users can register new tools at runtime via voice ("use Mixamo for animations") or text command
- Voice commands route to AgentRunCommand for Unity tasks
- Task completion summaries include files created, steps taken, tokens used, errors encountered

## Self-Check: PASSED
