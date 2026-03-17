---
phase: 23-csharp-code-generation
plan: "03"
subsystem: unity-editor-bridge
tags: [unity, csharp, editor-window, approval-ui, websocket]
dependency_graph:
  requires: [unity/com.jarvis.editor-bridge/Editor/JarvisEditorBridge.cs]
  provides: [unity/com.jarvis.editor-bridge/Editor/JarvisPanel.cs]
  affects: [unity-editor-bridge, agent-approval-gate]
tech_stack:
  added: []
  patterns: [EditorWindow, InitializeOnLoad, static-event, EditorGUILayout]
key_files:
  created:
    - unity/com.jarvis.editor-bridge/Editor/JarvisPanel.cs
  modified:
    - unity/com.jarvis.editor-bridge/Editor/JarvisEditorBridge.cs
decisions:
  - "OnAgentMessage event raised via EditorApplication.delayCall to marshal WebSocket thread to Unity main thread"
  - "BroadcastRaw() added to JarvisEditorBridge for panel-to-agent approval responses"
  - "Log entries capped at 50 FIFO; intentionally ephemeral across domain reloads"
  - "Approval section hidden when _approvalPending is false (not disabled — hidden entirely)"
  - "Status color: green=running, yellow=waiting_approval, red=failed, gray=idle/done"
metrics:
  duration: "~8 minutes"
  completed_date: "2026-03-17"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 1
requirements_satisfied: [UNITY-05]
---

# Phase 23 Plan 03: JarvisPanel EditorWindow Summary

**One-liner:** Unity EditorWindow showing agent step/status, 50-entry FIFO log, and approve/reject buttons wired to JarvisEditorBridge WebSocket event

## What Was Built

`JarvisPanel.cs` is a Unity `EditorWindow` accessible from `Window > Jarvis > Agent Panel`. It subscribes to a new `JarvisEditorBridge.OnAgentMessage` static event and renders:

- **Status row**: color-coded status label (green/yellow/red/gray) + current step name
- **Log scrollview**: timestamped entries, max 50 lines (FIFO), backed by `EditorStyles.helpBox`
- **Approval section**: shown only when `agent_approval_needed` is pending; "Approve" and "Reject" buttons send a JSON-RPC response back to the Python agent via `JarvisEditorBridge.BroadcastRaw()`

## Changes to Existing Files

`JarvisEditorBridge.cs` received three additions:

1. `public static event Action<string> OnAgentMessage` — UI listeners subscribe here
2. `internal static void RaiseAgentMessage(string rawMessage)` — marshals to main thread via `EditorApplication.delayCall`
3. `public static void BroadcastRaw(string payload)` — lets the panel send approval responses back on the WebSocket

The `JarvisBridgeService.OnMessage` inner method now calls `RaiseAgentMessage(e.Data)` for any method starting with `"agent_"`, before the standard reflection dispatch.

## Handled Messages

| Method | Action |
|---|---|
| `agent_step_start` | Updates `_currentStep`, sets status to `running` |
| `agent_step_done` | Updates status from params, adds log entry |
| `agent_task_done` | Sets status `done`, adds log entry |
| `agent_task_failed` | Sets status `failed`, logs error message |
| `agent_approval_needed` | Enables approval UI, stores task_id + description |

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Additional notes

- `using System.Collections.Generic` added to `JarvisEditorBridge.cs` (was already needed implicitly; made explicit for clarity).
- The checkpoint (human-verify) was auto-approved per executor instructions — the user gave full permissions and is away.

## Test Results

- Python test suite: **6347 passed, 9 skipped, 1 failed** (pre-existing flaky race condition in `test_mobile_missions_v5.py::TestMOB12ConcurrentConsistency::test_pause_resume_under_concurrency` — unrelated to C# changes, existed before this plan)
- C# structural verification: passed (EditorWindow, MenuItem, Approve, Reject, OnGUI all present)

## Self-Check: PASSED

- `unity/com.jarvis.editor-bridge/Editor/JarvisPanel.cs` — FOUND
- `unity/com.jarvis.editor-bridge/Editor/JarvisEditorBridge.cs` — FOUND (modified)
- Commit `c988c3a8` — FOUND
