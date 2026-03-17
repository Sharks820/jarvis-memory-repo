---
phase: 21
slug: unity-editor-bridge
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 21 — Validation Strategy

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (Python) + file validation (C#) |
| **Quick run command** | `python -m pytest engine/tests/ -x -q` |
| **Full suite command** | `python -m pytest engine/tests/ --tb=no` |
| **C# validation** | File existence + grep for key patterns (no Unity test runner in CI) |

## Phase Requirements → Test Map

| Requirement | Test Strategy |
|-------------|---------------|
| UNITY-01 | File exists: JarvisEditorBridge.cs with WebSocketServer on port 8091 |
| UNITY-02 | File exists: ReflectionCommandDispatcher.cs with Assembly scanning |
| UNITY-03 | File exists: domain reload handlers (beforeAssemblyReload/afterAssemblyReload) |
| UNITY-04 | `test_unity_tool.py`: mock WS, verify write_script/compile/create_project |
| CODE-04 | `test_unity_tool.py`: _assert_safe_code blocks Process.Start, File.Delete |
| CODE-05 | `test_unity_tool.py`: _assert_in_jail blocks ../traversal |

## Wave 0 Gaps

- [ ] `engine/tests/test_unity_tool.py` — Python UnityTool tests
- [ ] `unity/com.jarvis.editor-bridge/` — C# package directory structure

## Additional Checks

- `ruff check engine/src/jarvis_engine/agent/tools/` — must be clean
- All C# files must have `[InitializeOnLoad]` or `[MenuItem]` attributes where appropriate
