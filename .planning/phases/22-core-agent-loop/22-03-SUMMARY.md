---
phase: 22-core-agent-loop
plan: "03"
subsystem: agent
tags: [agent, cqrs, sse, integration-test, wiring]
dependency_graph:
  requires: [22-01, 22-02]
  provides: [agent-run-command-live, agent-status-live, agent-approve-live, sse-stream, e2e-tests]
  affects: [app.py, mobile_routes, handlers]
tech_stack:
  added: []
  patterns:
    - ThreadPoolExecutor background loop for non-blocking AgentRunHandler
    - asyncio.new_event_loop per thread for agent loop isolation
    - SSE endpoint via text/event-stream with keep-alive pings
    - Module-level ProgressEventBus singleton wired to app.py at startup
key_files:
  created:
    - engine/src/jarvis_engine/handlers/agent_handlers.py (replaced stub)
    - engine/src/jarvis_engine/mobile_routes/agent.py
    - engine/tests/test_agent_handlers.py
    - engine/tests/test_agent_e2e.py
  modified:
    - engine/src/jarvis_engine/mobile_routes/__init__.py
    - engine/src/jarvis_engine/mobile_routes/server.py
    - engine/src/jarvis_engine/app.py
    - engine/tests/test_agent_commands.py
decisions:
  - AgentRunHandler creates a new asyncio event loop per background thread (asyncio.new_event_loop) -- avoids event loop sharing issues between threads
  - _register_agent_handlers wires store/gate/bus/registry/gateway; fallback to stubs on import failure
  - AgentStateStore uses the MemoryEngine db connection when available; falls back to a separate sqlite3.connect on the same db_path
  - SSE endpoint uses asyncio.wait_for with 30s timeout for keep-alive instead of a separate heartbeat coroutine
  - test_agent_commands.py stub assertions updated to real-handler assertions (Rule 1 auto-fix)
metrics:
  duration_minutes: ~22
  completed_date: "2026-03-17"
  tasks_completed: 2
  new_tests: 22
  files_created: 3
  files_modified: 4
---

# Phase 22 Plan 03: Core Agent Loop Wiring Summary

**One-liner:** Agent loop wired end-to-end: real CQRS handlers, SSE streaming route, app.py DI composition, and 22 new integration tests covering happy path, replan, escalation, budget, and approval gating.

## What Was Built

### Task 1: Real Agent Handlers and SSE Route

**`engine/src/jarvis_engine/handlers/agent_handlers.py`** (replaced stubs):

- `AgentRunHandler.handle()` — generates task_id, checkpoints task to store with status="pending", submits `_run_agent_loop` to a module-level `ThreadPoolExecutor(max_workers=4)`, returns immediately.
- `AgentRunHandler._run_agent_loop()` — creates a fresh `asyncio.new_event_loop()` per thread, builds `TaskPlanner + StepExecutor + ReflectionLoop`, plans steps, updates task to "running", calls `loop.run_until_complete(reflection.run_loop(...))`.
- `AgentStatusHandler.handle()` — loads task from `AgentStateStore`, returns status/step_index/tokens_used/last_error. Returns `return_code=1` if task not found.
- `AgentApproveHandler.handle()` — calls `gate.approve(task_id)` or `gate.reject(task_id)`, returns "approved"/"rejected" action_taken.

**`engine/src/jarvis_engine/mobile_routes/agent.py`** (new):

- `AgentRoutesMixin` with four handler methods:
  - `handle_agent_run` — POST /agent/run
  - `handle_agent_status` — GET /agent/status?task_id=...
  - `handle_agent_approve` — POST /agent/approve
  - `handle_agent_stream` — GET /agent/stream (SSE, 30s keep-alive pings)

**`engine/src/jarvis_engine/app.py`** — `_register_agent_handlers` updated:
- Creates `ProgressEventBus` singleton, `AgentStateStore` (shared with MemoryEngine db), `ApprovalGate`, `ToolRegistry`.
- Registers `FileTool`, `ShellTool`, and optionally `WebTool`.
- Passes all subsystems to real handler constructors.
- Falls back to stubs on `SUBSYSTEM_ERRORS` import failure.

### Task 2: End-to-End Integration Tests

**`engine/tests/test_agent_e2e.py`** (7 tests):

1. `test_happy_path` — 2-step plan executes, status reaches "done", step_index=2.
2. `test_happy_path_emits_progress_events` — step_start, step_done, task_done events emitted.
3. `test_failure_and_replan` — first tool call fails, replan produces recovery step, task completes.
4. `test_escalation_after_3_same_errors` — perpetual same-error causes status="failed" after 3 attempts.
5. `test_token_budget_enforcement` — planning tokens exceed budget, task fails with budget message.
6. `test_approval_gate_blocks_and_resumes` — destructive tool blocks until `gate.approve()` called within event loop.
7. `test_agent_run_handler_full_integration` — full integration from handler through background thread to "done".

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_agent_commands.py stub assertions broken by real implementation**
- **Found during:** Task 2 / full suite run
- **Issue:** Three tests in `test_agent_commands.py` asserted the stub "not yet implemented" message and return_code=0 from handlers without store/gate. Real handlers return "Task submitted" and return_code=1 when dependencies are missing.
- **Fix:** Updated assertions to match real handler behavior (pending status, not-found/not-configured return codes).
- **Files modified:** `engine/tests/test_agent_commands.py`
- **Commit:** a3f6466c

**2. [Rule 1 - Bug] Unused imports in mobile_routes/agent.py**
- **Found during:** ruff check after Task 1
- **Issue:** `threading` and `SUBSYSTEM_ERRORS` imported but unused
- **Fix:** Removed unused imports
- **Files modified:** `engine/src/jarvis_engine/mobile_routes/agent.py`
- **Commit:** e810ef3d (ruff fix applied before commit)

**3. [Rule 1 - Bug] approval gate test used cross-thread asyncio.Event**
- **Found during:** Task 2 test run (1 failure)
- **Issue:** Original approach spawned a threading.Thread running asyncio.run(), then checked `gate._pending` from main thread and called `gate.approve()`. The asyncio.Event inside gate was created in the thread's loop, making `event.set()` from main thread unreliable.
- **Fix:** Rewrote test to use `asyncio.create_task()` within a single event loop — subscribe to bus, run loop as a coroutine task, detect `approval_needed` event, call `gate.approve()` from within the same loop.
- **Files modified:** `engine/tests/test_agent_e2e.py`
- **Commit:** a3f6466c

## Test Results

- Before: 6185 passing, 10 skipped
- After: 6236 passing, 9 skipped, 0 failures
- New tests added: 22 (15 handler tests + 7 e2e tests)
- ruff: clean on all new/modified agent files

## Self-Check: PASSED
