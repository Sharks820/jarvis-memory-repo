---
phase: 21-unity-editor-bridge
plan: 01
subsystem: unity-bridge
tags: [csharp, unity, websocket, json-rpc, editor-plugin, upm]
dependency_graph:
  requires: []
  provides: [unity-bridge-c#-server, upm-package-structure]
  affects: [phase-21-plan-02-unity-tool]
tech_stack:
  added:
    - websocket-sharp (WebSocketSharp.Standard 1.0.3 NuGet DLL — manual install to Plugins/Editor/)
    - com.unity.nuget.newtonsoft-json 3.0.1 (UPM dependency in package.json)
  patterns:
    - InitializeOnLoad static constructor for domain reload recovery
    - Dictionary<string, List<MethodInfo>> reflection cache (overload-safe)
    - AssemblyReloadEvents.beforeAssemblyReload + afterAssemblyReload lifecycle hooks
    - JSON-RPC 2.0 request/response contract with factory methods
    - TypeCoercer: named-parameter JSON-to-CLR coercion (string/int/long/float/double/bool/Vector2/Vector3/Color)
    - StaticAnalysisGuard: regex-based C# dangerous API scanner (defense-in-depth)
    - Path jail enforcement in ReflectionCommandDispatcher for file operations
key_files:
  created:
    - unity/com.jarvis.editor-bridge/package.json
    - unity/com.jarvis.editor-bridge/Editor/Jarvis.EditorBridge.asmdef
    - unity/com.jarvis.editor-bridge/Editor/JarvisEditorBridge.cs
    - unity/com.jarvis.editor-bridge/Editor/ReflectionCommandDispatcher.cs
    - unity/com.jarvis.editor-bridge/Editor/StaticAnalysisGuard.cs
    - unity/com.jarvis.editor-bridge/Editor/Models/JsonRpcRequest.cs
    - unity/com.jarvis.editor-bridge/Editor/Models/JsonRpcResponse.cs
    - unity/com.jarvis.editor-bridge/Editor/Util/TypeCoercer.cs
    - unity/com.jarvis.editor-bridge/Plugins/Editor/.gitkeep
    - unity/com.jarvis.editor-bridge/Plugins/Editor/README.md
  modified: []
decisions:
  - "Cache uses List<MethodInfo> per key (not single-entry) to handle overloaded Unity APIs like Debug.Log — overload resolution by parameter count at dispatch time (see Pitfall 3 in research)"
  - "websocket-sharp DLL not committed to repo — README instructs user to download WebSocketSharp.Standard 1.0.3 from NuGet and place in Plugins/Editor/"
  - "StaticAnalysisGuard.cs is defense-in-depth only; Python-side _assert_safe_code() is the authoritative gate (Roslyn analyzers are IDE-scoped in Unity 6.3)"
  - "Path jail enforcement on C# side uses Path.GetFullPath() normalization before StartsWith check — prevents ../ traversal bypass (Pitfall 4)"
  - "ReuseAddress=true on WebSocketServer to prevent AddressAlreadyInUse on domain reload (Pitfall 2)"
  - "asmdef precompiledReferences includes websocket-sharp.dll and Newtonsoft.Json.dll — assembly must override references"
metrics:
  duration_minutes: 15
  completed_date: "2026-03-17"
  tasks_completed: 2
  tasks_total: 2
  files_created: 10
  files_modified: 0
---

# Phase 21 Plan 01: Unity Editor Bridge C# Package Summary

**One-liner:** UPM local package with [InitializeOnLoad] WebSocket JSON-RPC server on localhost:8091, reflection dispatch cache (List<MethodInfo> per key for overload safety), domain reload recovery via AssemblyReloadEvents, and TypeCoercer/StaticAnalysisGuard security layers.

## What Was Built

A complete Unity UPM local package at `unity/com.jarvis.editor-bridge/` containing the C#-side Unity Editor bridge. This is the server component that the Python `UnityTool` (Plan 02) connects to.

**Package structure:**
```
unity/com.jarvis.editor-bridge/
├── package.json                             # UPM manifest (com.unity.nuget.newtonsoft-json 3.0.1)
├── Editor/
│   ├── Jarvis.EditorBridge.asmdef          # Editor-only compilation, precompiledReferences
│   ├── JarvisEditorBridge.cs               # [InitializeOnLoad] WS server lifecycle + heartbeat
│   ├── ReflectionCommandDispatcher.cs      # Reflection cache + JSON-RPC dispatch
│   ├── StaticAnalysisGuard.cs              # Defense-in-depth C# API scanner
│   ├── Models/
│   │   ├── JsonRpcRequest.cs               # JSON-RPC 2.0 request model
│   │   └── JsonRpcResponse.cs              # JSON-RPC 2.0 response model with factory methods
│   └── Util/
│       └── TypeCoercer.cs                  # JSON-to-CLR parameter coercion
└── Plugins/Editor/
    ├── .gitkeep                             # Placeholder for websocket-sharp.dll
    └── README.md                            # Instructions for DLL download/placement
```

## Domain Reload Sequence Implemented

1. Unity detects .cs file change, begins compilation
2. `beforeAssemblyReload` fires → `StopServer()` gracefully closes WebSocket
3. AppDomain torn down (all static state destroyed)
4. New AppDomain created, `[InitializeOnLoad]` fires static constructor
5. `BuildCache()` rebuilds reflection cache (~200-400ms)
6. `StartServer()` binds port 8091 (`ReuseAddress=true` handles lingering socket)
7. `afterAssemblyReload` fires → `BroadcastReady()` sends `{"status":"ready"}`
8. Python client receives heartbeat, exits `WAITING_FOR_BRIDGE` state

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | UPM scaffold, JSON-RPC models, TypeCoercer, StaticAnalysisGuard | 5ce9e16e | package.json, asmdef, 5 .cs files, Plugins/README |
| 2 | JarvisEditorBridge and ReflectionCommandDispatcher | 96872ed2 | JarvisEditorBridge.cs, ReflectionCommandDispatcher.cs |

## Decisions Made

1. **List<MethodInfo> per cache key** — `Dictionary<string, List<MethodInfo>>` stores all overloads; dispatch resolves by matching required/optional parameter count. Prevents the first-wins bug on methods like `Debug.Log` (Pitfall 3).

2. **websocket-sharp DLL not committed** — Binary DLLs don't belong in git. README provides NuGet download instructions. .gitkeep preserves the directory.

3. **StaticAnalysisGuard is defense-in-depth** — Python's `_assert_safe_code()` is the primary gate (runs before any WebSocket transmission). C# guard only fires for write operations dispatched through the bridge.

4. **Path jail uses Path.GetFullPath()** — String prefix checks on non-normalized paths are bypassed by `../` sequences. Full path normalization prevents Pitfall 4.

5. **ReuseAddress=true** — Required to prevent `SocketException: Address already in use` when the static constructor fires immediately after domain reload before the OS fully releases the socket (Pitfall 2).

## Deviations from Plan

### Out of Scope Discovery (Pre-existing)

**Pre-existing xdist crash:** `test_write_script_enters_waiting` in `engine/tests/test_unity_tool.py` crashes pytest-xdist workers on Windows due to `asyncio.run()` inside xdist workers. This failure existed before this plan executed (confirmed by `git stash` check). The test passes in single-process mode (`--override-ini="addopts="`). This is documented in `deferred-items.md`. All 6072 other tests pass.

## Self-Check: PASSED

All 10 files exist at expected paths. Both task commits verified in git log.
- 5ce9e16e: `feat(21-01): UPM package scaffold, JSON-RPC models, TypeCoercer, and StaticAnalysisGuard` — FOUND
- 96872ed2: `feat(21-01): JarvisEditorBridge and ReflectionCommandDispatcher C# core classes` — FOUND
