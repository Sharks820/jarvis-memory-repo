---
phase: 20-infrastructure-foundations
plan: "01"
subsystem: agent
tags: [vram, unity, agent, cqrs, mutex, process-management]
dependency_graph:
  requires: []
  provides:
    - agent/vram_coordinator.py (VRAMCoordinator singleton, asyncio mutex)
    - ops/unity_process_manager.py (kill_unity_tree, ensure_unity_not_running)
    - commands/agent_commands.py (AgentRunCommand, AgentStatusCommand, AgentApproveCommand)
    - handlers/agent_handlers.py (stub handlers returning return_code=0)
    - app.py registration (_register_agent_handlers via lazy import)
  affects:
    - engine/src/jarvis_engine/app.py (3 new commands on the CQRS bus)
tech_stack:
  added: []
  patterns:
    - asyncio.Lock dataclass field(default_factory=asyncio.Lock, init=False) for GPU mutex
    - taskkill /f /t /pid for Windows Unity process tree kill
    - frozen dataclass CQRS commands inheriting ResultBase
    - lazy-import _register_*_handlers pattern in app.py
key_files:
  created:
    - engine/src/jarvis_engine/agent/vram_coordinator.py
    - engine/src/jarvis_engine/ops/unity_process_manager.py
    - engine/src/jarvis_engine/commands/agent_commands.py
    - engine/src/jarvis_engine/handlers/agent_handlers.py
    - engine/tests/test_vram_coordinator.py
    - engine/tests/test_unity_process_manager.py
    - engine/tests/test_agent_commands.py
  modified:
    - engine/src/jarvis_engine/app.py
decisions:
  - asyncio.Lock (not threading.Lock) chosen because VRAM coordinator sits on the async gateway call path
  - taskkill /f /t on Windows kills full process tree including UnityShaderCompiler.exe children
  - Stub handlers return return_code=0 with "not yet implemented" message — Phase 22 fills in real logic
  - Agent command imports at module level in app.py (not lazy) to match existing pattern for other command groups
metrics:
  duration: "~35 minutes"
  completed: "2026-03-17"
  tasks_completed: 2
  tests_added: 35
  files_created: 7
  files_modified: 1
---

# Phase 20 Plan 01: Infrastructure Foundations — VRAM, Unity Process, Agent CQRS Summary

VRAM coordinator asyncio mutex, Unity process tree kill, and 3 agent CQRS command stubs registered on the bus — all three blocking infrastructure pieces for Phase 22.

## What Was Built

### Task 1: VRAMCoordinator and UnityProcessManager

**`engine/src/jarvis_engine/agent/vram_coordinator.py`**
- `VRAMCoordinator` dataclass with `asyncio.Lock` as `_gpu_mutex`
- `acquire_generation()` / `release_generation()` — held during any Ollama inference
- `acquire_playmode()` / `release_playmode()` — held during any Unity play-mode entry
- Both sides share `_gpu_mutex` — only one can be held at a time (mutual exclusion)
- `status` property returns `{generation_active, playmode_active, locked}`
- `read_vram_used_mb()` — sync utility calling `nvidia-smi`, returns `int | None`
- `get_coordinator()` — module-level singleton factory
- `VRAM_PRESSURE_THRESHOLD_MB = 7500`

**`engine/src/jarvis_engine/ops/unity_process_manager.py`**
- `UNITY_SERVICE_NAME = "unity_editor"`
- `kill_unity_tree(pid)` — `taskkill /f /t /pid` on Win32, `os.killpg` on POSIX
- `ensure_unity_not_running(root)` — reads PID file via `process_manager.read_pid_file`, kills tree if stale, removes PID file

### Task 2: Agent CQRS Commands, Handlers, and Bus Registration

**`engine/src/jarvis_engine/commands/agent_commands.py`**
- `AgentRunCommand(frozen=True)`: `goal`, `task_id`, `token_budget=50000`
- `AgentRunResult(ResultBase)`: `task_id`, `status`
- `AgentStatusCommand(frozen=True)`: `task_id`
- `AgentStatusResult(ResultBase)`: `task_id`, `status`, `step_index`, `tokens_used`, `last_error`
- `AgentApproveCommand(frozen=True)`: `task_id`, `approved=True`, `reason`
- `AgentApproveResult(ResultBase)`: `task_id`, `action_taken`

**`engine/src/jarvis_engine/handlers/agent_handlers.py`**
- `AgentRunHandler`, `AgentStatusHandler`, `AgentApproveHandler`
- Each takes `root: Path`, has `.handle(cmd) -> Result`
- All return `return_code=0`, `message="Agent subsystem not yet implemented"`

**`engine/src/jarvis_engine/app.py`** (modified)
- Added `from jarvis_engine.commands.agent_commands import ...` at module level
- Added `_register_agent_handlers(bus, root)` with lazy imports of agent_handlers
- Called from `create_app()` after `_register_knowledge_handlers()`

## Test Results

| Test File | Passed | Skipped | Notes |
|-----------|--------|---------|-------|
| test_vram_coordinator.py | 13 | 0 | All mutex/nvidia-smi scenarios |
| test_unity_process_manager.py | 6 | 2 | 2 POSIX-only skipped on Win32 |
| test_agent_commands.py | 14 | 0 | Commands, handlers, bus round-trip |
| **Full suite** | **6073** | **9** | No regressions from 5979 baseline |

## Commits

| Hash | Description |
|------|-------------|
| `ef2b30f8` | test(20-01): add failing tests for VRAMCoordinator and UnityProcessManager |
| `1b270dcc` | feat(20-01): implement VRAMCoordinator mutex and UnityProcessManager |
| `643d0cc0` | test(20-01): add failing tests for agent CQRS commands and bus registration |
| `84623e82` | feat(20-01): implement agent CQRS commands, stub handlers, and bus registration |

## Deviations from Plan

None — plan executed exactly as written.

The `asyncio.Lock` `default_factory` pattern was used as specified. No new pip packages added. All imports follow the existing lazy-import handler registration pattern in `app.py`.

## Self-Check

- [x] `engine/src/jarvis_engine/agent/vram_coordinator.py` — created and verified
- [x] `engine/src/jarvis_engine/agent/__init__.py` — already existed as empty marker
- [x] `engine/src/jarvis_engine/ops/unity_process_manager.py` — created and verified
- [x] `engine/src/jarvis_engine/commands/agent_commands.py` — created and verified
- [x] `engine/src/jarvis_engine/handlers/agent_handlers.py` — created and verified
- [x] `engine/src/jarvis_engine/app.py` — modified with agent registration
- [x] All test files committed individually (RED then GREEN)
- [x] Full suite: 6073 passed, 0 failed
- [x] `ruff check engine/src/` — all checks passed
