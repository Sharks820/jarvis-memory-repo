---
phase: 22-core-agent-loop
plan: "01"
subsystem: agent
tags: [agent, tools, approval, streaming, tdd]
dependency_graph:
  requires: []
  provides:
    - FileTool (path-jailed file read/write)
    - ShellTool (sandboxed subprocess with timeout + blocklist)
    - WebTool (SSRF-safe web fetch wrapper)
    - ApprovalGate (tool safety gate with approve/reject lifecycle)
    - ProgressEventBus (bounded asyncio.Queue fan-out for SSE)
  affects:
    - agent/tool_registry.py (consumers register specs from these tools)
    - Plan 02 StepExecutor (invokes these tools via ToolRegistry)
tech_stack:
  added: []
  patterns:
    - TDD red-green-refactor
    - asyncio.Event for approval lifecycle
    - Path.resolve() for path jail
    - asyncio.create_subprocess_shell with wait_for timeout
    - loop.run_in_executor for blocking fetch
key_files:
  created:
    - engine/src/jarvis_engine/agent/tools/file_tool.py
    - engine/src/jarvis_engine/agent/tools/shell_tool.py
    - engine/src/jarvis_engine/agent/tools/web_tool.py
    - engine/src/jarvis_engine/agent/approval_gate.py
    - engine/src/jarvis_engine/agent/progress_bus.py
    - engine/tests/test_agent_tools.py
    - engine/tests/test_approval_gate.py
    - engine/tests/test_progress_bus.py
  modified: []
decisions:
  - WebTool wraps existing jarvis_engine.web.fetch.fetch_page_text (SSRF-safe) rather than reimplementing fetch logic
  - ShellTool uses asyncio.create_subprocess_shell with wait_for() to ensure non-blocking timeout
  - ApprovalGate uses asyncio.Event + mutable list[bool] container for approve/reject result passing
  - ProgressEventBus singleton uses module-level _bus variable, created on first get_progress_bus() call
  - WebTool.fetch_page_text is a module-level shim function to enable clean patching in tests
metrics:
  duration_minutes: 16
  completed_date: "2026-03-17"
  tasks_completed: 2
  files_created: 8
  tests_added: 50
  tests_total: 6156
---

# Phase 22 Plan 01: Agent Tools, ApprovalGate, and ProgressEventBus Summary

**One-liner:** Path-jailed FileTool + blocklist ShellTool + SSRF-safe WebTool + asyncio-based ApprovalGate + bounded fan-out ProgressEventBus, all registrable via get_tool_spec().

## What Was Built

Five production modules forming the tool layer of the agent ReAct loop:

**FileTool** (`agent/tools/file_tool.py`) — Async read/write confined to a `project_dir` root via `Path.resolve()`. Any attempt to escape via `../` or an absolute path outside the jail raises `PermissionError`. Returns a `ToolSpec` with `name="file"`, `requires_approval=False`.

**ShellTool** (`agent/tools/shell_tool.py`) — Async subprocess executor using `asyncio.create_subprocess_shell`. Validates commands against a configurable blocklist (`rm -rf /`, `format`, `del /s`, `:(){`, `mkfs`). Enforces a configurable timeout via `asyncio.wait_for`. Returns a `ToolSpec` with `name="shell"`, `requires_approval=True`, `is_destructive=True`.

**WebTool** (`agent/tools/web_tool.py`) — Delegates to the existing `jarvis_engine.web.fetch.fetch_page_text` pipeline (SSRF-safe, 3-tier HTTP client, HTML-to-text). Runs in a thread-pool executor to keep the event loop free. Truncates output to 10 000 characters. Returns a `ToolSpec` with `name="web"`, `requires_approval=False`.

**ApprovalGate** (`agent/approval_gate.py`) — `check(spec, params)` returns `ApprovalDecision.AUTO` for safe tools or `REQUIRES_APPROVAL` for destructive/costly ones. `wait_for_approval(task_id, step)` emits an `approval_needed` event on the `ProgressEventBus` then awaits an `asyncio.Event` set by `approve()` or `reject()`. Thread-safe via asyncio event loop.

**ProgressEventBus** (`agent/progress_bus.py`) — Fan-out event bus. Each `subscribe()` call returns a `asyncio.Queue(maxsize=256)`. `emit()` puts events on all subscriber queues; full queues drop the oldest item (no blocking). Module-level singleton via `get_progress_bus()`.

## Tests Added

| File | Tests | Coverage |
|------|-------|----------|
| `test_agent_tools.py` | 29 | FileTool (11), ShellTool (9), WebTool (9) |
| `test_approval_gate.py` | 12 | ApprovalDecision enum, check logic, approve/reject lifecycle, bus emit |
| `test_progress_bus.py` | 9 | subscribe, emit, fan-out, unsubscribe, drop-oldest, singleton |
| **Total** | **50** | |

## Verification

All 5 modules import cleanly:
```
python -c "from jarvis_engine.agent.tools.file_tool import FileTool; ..."  # OK
```

Test suite: 6156 passing (50 new), 10 skipped, 0 failures.

Ruff check: all new files clean.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing pattern] WebTool module-level shim for testability**
- **Found during:** Task 1 implementation
- **Issue:** The plan called for mocking `fetch_page_text` but the lazy import inside `execute()` made it unpatachable via standard `patch()`
- **Fix:** Added a module-level `fetch_page_text` shim function in `web_tool.py` so tests can `patch("jarvis_engine.agent.tools.web_tool.fetch_page_text")` cleanly
- **Files modified:** `engine/src/jarvis_engine/agent/tools/web_tool.py`
- **Commit:** cf391ee4

None - all other plan items executed exactly as written.

## Self-Check: PASSED

Files created:
- engine/src/jarvis_engine/agent/tools/file_tool.py - FOUND
- engine/src/jarvis_engine/agent/tools/shell_tool.py - FOUND
- engine/src/jarvis_engine/agent/tools/web_tool.py - FOUND
- engine/src/jarvis_engine/agent/approval_gate.py - FOUND
- engine/src/jarvis_engine/agent/progress_bus.py - FOUND

Commits:
- 4f98ebb1 (RED: tools tests)
- cf391ee4 (GREEN: tools implementation)
- 00804b8e (RED: gate+bus tests)
- fa003016 (GREEN: gate+bus implementation)
