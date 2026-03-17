---
phase: 21-unity-editor-bridge
plan: 02
subsystem: agent/tools
tags: [unity, websocket, security, path-jail, static-analysis, json-rpc, tdd]
dependency_graph:
  requires: [tool_registry.py, agent/__init__.py]
  provides: [UnityTool, BridgeState, _assert_in_jail, _assert_safe_code]
  affects: [agent StepExecutor (Plan 03), ToolRegistry registrations]
tech_stack:
  added: []
  patterns: [asyncio-run-in-sync-tests, _send_rpc-bypass-pattern, lazy-import-ToolSpec]
key_files:
  created:
    - engine/src/jarvis_engine/agent/tools/__init__.py
    - engine/src/jarvis_engine/agent/tools/unity_tool.py
    - engine/tests/test_unity_tool.py
  modified: []
decisions:
  - "write_script() uses _send_rpc() directly (bypasses WAITING_FOR_BRIDGE guard in call()); state transitions to WAITING_FOR_BRIDGE AFTER the send, not before"
  - "Async tests use asyncio.run() pattern (no pytest-asyncio) to match existing project convention"
  - "_send_rpc() extracted as low-level bypass for write_script() to avoid deadlock on WAITING_FOR_BRIDGE state"
metrics:
  duration_seconds: 1131
  completed: "2026-03-17"
  tasks_completed: 2
  files_created: 3
  tests_added: 35
  suite_before: 6073
  suite_after: 6106
requirements: [UNITY-04, CODE-04, CODE-05]
---

# Phase 21 Plan 02: UnityTool WebSocket Client Summary

Python-side WebSocket JSON-RPC 2.0 client for the Unity Editor Bridge, with normpath-based path jail and 10-pattern C# static analysis guard enforced before every script write.

## What Was Built

**`engine/src/jarvis_engine/agent/tools/__init__.py`** — Agent tools subpackage init.

**`engine/src/jarvis_engine/agent/tools/unity_tool.py`** (330 lines) — Full async client:

- `_assert_in_jail(rel_path)` — Normalises separators with `os.path.normpath`, checks result starts with `Assets/JarvisGenerated/` (trailing slash prevents sibling-prefix bypass). Raises `PermissionError` on any violation including empty paths and `..` traversal.
- `_assert_safe_code(content)` — Scans generated C# against 10 compiled regex patterns: `Process.Start`, `System.Diagnostics.Process`, `File.Delete`, `Directory.Delete`, `FileUtil.DeleteFileOrDirectory`, `AssetDatabase.DeleteAsset`, path traversal `../`, `Assembly.LoadFrom`, `Assembly.Load`, and `GetMethod().Invoke` reflection chain.
- `BridgeState` enum — `DISCONNECTED`, `CONNECTED`, `WAITING_FOR_BRIDGE`.
- `UnityTool` class — `connect()`, `disconnect()`, `call()`, `_send_rpc()`, `write_script()`, `compile()`, `create_project()`, `_listen_for_heartbeat()`, `_handle_heartbeat_message()`, `get_tool_spec()`.

**`engine/tests/test_unity_tool.py`** (380 lines) — 35 tests across three classes.

## Key Design Decision

`write_script()` uses `_send_rpc()` directly (bypassing `call()`'s WAITING_FOR_BRIDGE gate) and only transitions to `WAITING_FOR_BRIDGE` **after** the RPC send completes. If `write_script()` set the state before calling the WS, `call()` would then wait for a heartbeat that never arrives (deadlock). The sequence: validate jail -> validate code -> check DISCONNECTED -> send via `_send_rpc()` -> set WAITING_FOR_BRIDGE -> clear ready_event.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Deadlock in write_script() / WAITING_FOR_BRIDGE state**
- **Found during:** Task 2 (test_write_script_enters_waiting crashed worker)
- **Issue:** Plan specified "set state=WAITING_FOR_BRIDGE, clear ready_event, return await self.call(...)". `call()` checks for WAITING_FOR_BRIDGE and waits 30 seconds for `_ready_event` — causing a deadlock since the event was just cleared and no heartbeat was incoming.
- **Fix:** Extracted `_send_rpc()` as a low-level bypass method. `write_script()` calls `_send_rpc()` directly and transitions to WAITING_FOR_BRIDGE only after the send.
- **Files modified:** `unity_tool.py`
- **Commit:** f876645f

**2. [Rule 3 - Blocking] pytest-asyncio not installed**
- **Found during:** Task 1 (tests would not run with `@pytest.mark.asyncio`)
- **Fix:** Rewrote async tests using `asyncio.run()` inside regular sync test methods — consistent with existing project test pattern.
- **Files modified:** `test_unity_tool.py`
- **Commit:** 0e8140df

## Test Results

- Task 1 tests (path jail + static analysis): 21 passing
- Task 2 tests (BridgeState machine + WebSocket): 14 passing
- Total new tests: 35
- Full suite: 6106 passed, 11 skipped, 0 failures (was 6073)

## Self-Check: PASSED
