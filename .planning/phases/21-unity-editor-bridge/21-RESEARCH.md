# Phase 21: Unity Editor Bridge - Research

**Researched:** 2026-03-17
**Domain:** C# WebSocket server in Unity Editor + Python WebSocket client tool + C# static analysis
**Confidence:** HIGH (WebSocket library choice, domain reload handling, reflection dispatch, path jail) / MEDIUM (Roslyn pre-compile blocking, TypeCoercer edge cases)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| UNITY-01 | JarvisEditorBridge C# plugin communicates via WebSocket JSON-RPC on localhost:8091 | websocket-sharp is the confirmed library; port 8091 confirmed clear; [InitializeOnLoad] server startup pattern documented |
| UNITY-02 | Bridge uses reflection-based command dispatch covering full Unity Editor API | BuildCache() at startup pattern confirmed; Assembly.GetTypes() once-only; Dictionary<string,MethodInfo> zero-alloc lookup |
| UNITY-03 | Bridge handles domain reload gracefully (heartbeat + reconnect + WAITING_FOR_BRIDGE state) | CompilationPipeline.compilationFinished + AssemblyReloadEvents.afterAssemblyReload signal sequence documented; Python WAITING_FOR_BRIDGE pattern designed |
| UNITY-04 | UnityTool creates projects, writes C# scripts, compiles, builds via bridge | websockets 16.0 Python client API confirmed; JSON-RPC 2.0 request/response format; AsyncIterator reconnect pattern available |
| CODE-04 | Pre-compilation static analysis blocks dangerous APIs (Process.Start, File.Delete outside jail) | Python regex scan (simple) + C# Roslyn DiagnosticDescriptor (deep) — two-layer approach documented |
| CODE-05 | Generated code confined to Assets/JarvisGenerated/ path jail | Path.GetFullPath() normalization pattern; Application.dataPath anchor; both Python side and C# side validation required |
</phase_requirements>

---

## Summary

Phase 21 produces two tightly coupled artifacts: a C# Unity Editor plugin (the bridge server) and a Python `UnityTool` (the bridge client). The C# side hosts a WebSocket server using websocket-sharp (the industry standard for Unity Editor server use cases — `System.Net.WebSockets.HttpListener` does not support server mode in Unity's Mono runtime). The Python side uses the `websockets` library (v16.0 current, v14.0+ required) which is already decided in STATE.md.

The single biggest architectural risk for this phase is domain reload. Every `.cs` file written to the project triggers a reload that tears down the C# AppDomain — including the bridge's WebSocket server. The bridge MUST re-register via `[InitializeOnLoad]`, send a `{"status":"ready"}` JSON message, and the Python side MUST enter `WAITING_FOR_BRIDGE` state and halt command dispatch until that heartbeat arrives. This is not an optional refinement; without it the automation pipeline produces silent command drops that look like Unity hangs.

Security is the other non-negotiable. Generated C# must be scanned for dangerous API patterns (`System.Diagnostics.Process`, `System.IO.File.Delete`, path traversal) before the file is written to disk. The path jail (`Assets/JarvisGenerated/`) must be enforced on both the Python side (before any WebSocket call that writes a file) and the C# bridge side (before any file operation executes). Both layers are required — neither alone is sufficient.

**Primary recommendation:** Build in order — (1) websocket-sharp C# server with `[InitializeOnLoad]` + heartbeat, (2) `ReflectionCommandDispatcher` with cache build, (3) Python `UnityTool` client with `WAITING_FOR_BRIDGE` state machine, (4) path jail on both sides, (5) Python pre-compile static analysis scanner, (6) `CompilationPipeline` result relay.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| websocket-sharp | 1.0.3 (NuGet: WebSocketSharp.Standard) | C# WebSocket server inside Unity Editor | System.Net.WebSockets.HttpListener.IsWebSocketRequest always returns false in Unity's Mono — websocket-sharp is the only working server option |
| com.unity.nuget.newtonsoft-json | 3.0.1 | JSON serialization/deserialization in C# | Official Unity package; IL2CPP-safe builds; used by mcp-unity and all major Unity automation tools |
| websockets (Python) | >=14.0 (current: 16.0) | Python WebSocket client connecting to C# bridge | Already decided in STATE.md; asyncio-native; v14+ fixes Windows selector event loop issues |
| System.Reflection (C# stdlib) | Built into .NET/Mono | Dynamic method dispatch for Unity API coverage | No additional package; cache at startup eliminates per-call allocation |
| CompilationPipeline (UnityEditor) | Built into Unity 6.3 | compilationFinished callback for domain reload signal | Official Unity API for tracking compile + reload cycle completion |
| AssemblyReloadEvents (UnityEditor) | Built into Unity 6.3 | afterAssemblyReload callback — fires after domain is fully reconstituted | Required alongside CompilationPipeline for complete reload detection |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| jsonrpc-websocket (Python) | 3.2.0 | JSON-RPC over websockets helper | Optional: only if raw JSON handling becomes verbose; current design handles JSON-RPC manually |
| UnityEditor.Compilation namespace | Built-in | assemblyCompilationFinished — per-assembly compile errors | Extracting compiler errors from compilation to relay back to Python |
| AssetDatabase (UnityEditor) | Built-in | Refresh, StartAssetEditing/StopAssetEditing, IsValidFolder | Batch import control and path validation on C# side |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| websocket-sharp | System.Net.WebSockets (stdlib) | HttpListener.IsWebSocketRequest returns false in Unity Mono — stdlib server mode does not work |
| websocket-sharp | NativeWebSocket | NativeWebSocket is a client library only, no server mode |
| websocket-sharp | Custom TcpListener + WebSocket handshake | 200+ lines of RFC 6455 handshake code vs. 5-line websocket-sharp setup; not worth it |
| Newtonsoft.Json | System.Text.Json | System.Text.Json is available but Newtonsoft is the established Unity ecosystem standard; mcp-unity uses it |
| Python pre-compile regex scan | Roslyn DiagnosticDescriptor (C# analyzer) | Roslyn analyzers in Unity only run in IDE context, not as compilation gate — Python regex is the reliable pre-write gate |

**Installation (C# side — package.json / manifest.json additions):**
```json
{
  "dependencies": {
    "com.unity.nuget.newtonsoft-json": "3.0.1"
  }
}
```
websocket-sharp DLL added to `Assets/Plugins/Editor/` (not UPM — UPM variant is unmaintained).

**Installation (Python side — already in project):**
```bash
pip install "websockets>=14.0"
```

---

## Architecture Patterns

### Recommended Project Structure

```
UnityProject/
└── Assets/
    ├── Editor/
    │   └── Jarvis/
    │       ├── JarvisEditorBridge.cs         # [InitializeOnLoad] entry point, WS server lifecycle
    │       ├── ReflectionCommandDispatcher.cs # Build cache + JSON-RPC dispatch
    │       ├── JarvisAssetPostprocessor.cs   # OnPostprocessAllAssets hook
    │       ├── StaticAnalysisGuard.cs        # C#-side dangerous API check before execute
    │       ├── Models/
    │       │   ├── JsonRpcRequest.cs
    │       │   └── JsonRpcResponse.cs
    │       └── Util/
    │           └── TypeCoercer.cs            # JSON value → C# parameter type coercion
    └── JarvisGenerated/                      # PATH JAIL: all agent output goes here
        └── .gitkeep

engine/src/jarvis_engine/agent/
├── __init__.py
├── tool_registry.py        # EXISTS (Phase 20)
├── state_store.py          # EXISTS (Phase 20)
├── vram_coordinator.py     # EXISTS (Phase 20)
├── kg_seeder.py            # EXISTS (Phase 20)
└── tools/
    ├── __init__.py
    └── unity_tool.py       # NEW (Phase 21) — WebSocket JSON-RPC client

engine/tests/
└── test_unity_tool.py      # NEW (Phase 21)
```

### Pattern 1: Unity Editor WebSocket Server with Domain Reload Recovery

**What:** `[InitializeOnLoad]` starts the websocket-sharp server. When domain reload fires, the AppDomain is torn down — the server dies. `[InitializeOnLoad]` fires again after reload completes, restarting the server and sending a `{"status":"ready"}` JSON notification. The Python side recognizes this and exits `WAITING_FOR_BRIDGE` state.

**When to use:** Every time any `.cs` file is written to the Unity project (triggers reload). The bridge must be designed with this cycle in mind from day one.

**Example:**
```csharp
// Source: ARCHITECTURE.md + websocket-sharp pattern
using UnityEditor;
using WebSocketSharp.Server;

[InitializeOnLoad]
public static class JarvisEditorBridge
{
    private static WebSocketServer _server;

    static JarvisEditorBridge()
    {
        // Called on every domain reload — always restarts server
        StopServer();
        StartServer();
        // Notify Python side that bridge is ready
        // (sent as first message when client connects, or via a push if already connected)
    }

    private static void StartServer()
    {
        _server = new WebSocketServer("ws://localhost:8091");
        _server.AddWebSocketService<JarvisBridgeService>("/jarvis");
        _server.Start();
        EditorApplication.quitting += StopServer;
    }

    private static void StopServer()
    {
        if (_server != null && _server.IsListening)
            _server.Stop();
        _server = null;
    }
}
```

**Domain reload signal sequence (C# side):**
```csharp
// Register for reload completion events
[InitializeOnLoad]
public static class BridgeReloadHandler
{
    static BridgeReloadHandler()
    {
        // assemblyCompilationFinished fires per-assembly as compilation completes
        CompilationPipeline.assemblyCompilationFinished += OnAssemblyCompiled;
        // afterAssemblyReload fires once when domain is fully reconstituted
        AssemblyReloadEvents.afterAssemblyReload += OnAfterReload;
    }

    private static void OnAfterReload()
    {
        // Bridge server is running again — send ready heartbeat to connected Python client
        JarvisEditorBridge.SendReadyHeartbeat();
    }
}
```

### Pattern 2: Reflection Command Dispatcher (Build Cache Once, Lookup Zero-Alloc)

**What:** On bridge startup (inside static constructor), scan `UnityEditor` and `UnityEngine` assemblies once. Build `Dictionary<string, MethodInfo>`. Each JSON-RPC call does a single dictionary lookup — no allocation, no repeat scanning.

**When to use:** This is the required pattern for full Unity API coverage. Reflection per-call causes GC pressure in the Editor.

**Example:**
```csharp
// Source: ARCHITECTURE.md verified pattern
public class ReflectionCommandDispatcher
{
    private readonly Dictionary<string, MethodInfo> _cache = new();

    public void BuildCache()
    {
        var assemblies = new[]
        {
            typeof(UnityEditor.EditorApplication).Assembly,   // UnityEditor
            typeof(UnityEngine.GameObject).Assembly,          // UnityEngine
        };
        foreach (var asm in assemblies)
        {
            foreach (var type in asm.GetTypes())
            {
                foreach (var method in type.GetMethods(
                    BindingFlags.Public | BindingFlags.Static))
                {
                    var key = $"{type.Name}.{method.Name}";
                    _cache.TryAdd(key, method);  // first overload wins
                }
            }
        }
    }

    public object Dispatch(string methodKey, JObject args)
    {
        if (!_cache.TryGetValue(methodKey, out var method))
            throw new KeyNotFoundException($"Unknown method: {methodKey}");
        var parameters = TypeCoercer.Coerce(method.GetParameters(), args);
        return method.Invoke(null, parameters);
    }
}
```

**Startup timing:** BuildCache() is called from `JarvisEditorBridge`'s static constructor (which runs on every domain reload). ~200-400ms startup cost, one-time per reload.

### Pattern 3: Python UnityTool — WebSocket Client with WAITING_FOR_BRIDGE State

**What:** Python `UnityTool` maintains a persistent `websockets` connection. After writing any `.cs` file, it transitions to `WAITING_FOR_BRIDGE` state and blocks further commands until a `{"status":"ready"}` message arrives (or 30-second timeout).

**When to use:** Every call to `write_script()` or `compile()` that changes C# source files.

**Example:**
```python
# Source: websockets 16.0 official docs + ARCHITECTURE.md pattern
import asyncio
import json
import logging
from enum import Enum, auto

logger = logging.getLogger(__name__)
BRIDGE_URL = "ws://localhost:8091/jarvis"
READY_TIMEOUT = 30.0  # seconds

class BridgeState(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    WAITING_FOR_BRIDGE = auto()  # post-domain-reload; commands blocked

class UnityTool:
    def __init__(self) -> None:
        self._ws = None
        self._state = BridgeState.DISCONNECTED
        self._ready_event = asyncio.Event()

    async def connect(self) -> None:
        from websockets.asyncio.client import connect
        self._ws = await connect(BRIDGE_URL)
        self._state = BridgeState.CONNECTED
        self._ready_event.set()

    async def call(self, method: str, params: dict) -> dict:
        if self._state == BridgeState.WAITING_FOR_BRIDGE:
            await asyncio.wait_for(self._ready_event.wait(), timeout=READY_TIMEOUT)
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        await self._ws.send(json.dumps(request))
        raw = await self._ws.recv()
        return json.loads(raw)

    async def write_script(self, rel_path: str, content: str) -> dict:
        # Path jail enforced here before sending
        _assert_in_jail(rel_path)
        _assert_safe_code(content)  # pre-compile static analysis
        # Entering WAITING_FOR_BRIDGE: script write will trigger domain reload
        self._state = BridgeState.WAITING_FOR_BRIDGE
        self._ready_event.clear()
        result = await self.call("FileUtil.WriteAllText",
                                 {"path": rel_path, "content": content})
        return result

    async def _listen_for_heartbeat(self) -> None:
        """Background task — sets ready_event on {"status":"ready"}."""
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg.get("status") == "ready":
                self._state = BridgeState.CONNECTED
                self._ready_event.set()
```

### Pattern 4: Path Jail — Two-Layer Enforcement

**What:** All agent-generated file operations must be confined to `Assets/JarvisGenerated/`. Enforced at TWO points: (1) Python side before sending WebSocket call, (2) C# side before executing file write.

**Why two layers:** Python validation can be bypassed by a bug in the agent prompt parsing. C# validation is the last defense before actual filesystem access.

**Python side (pre-call validation):**
```python
import os
from pathlib import Path

_JAIL_PREFIX = "Assets/JarvisGenerated"

def _assert_in_jail(rel_path: str) -> None:
    """Raise if rel_path escapes the JarvisGenerated jail."""
    # Normalize to collapse ../ traversal
    normalized = os.path.normpath(rel_path).replace("\\", "/")
    if not normalized.startswith(_JAIL_PREFIX):
        raise PermissionError(
            f"Path jail violation: '{rel_path}' is outside {_JAIL_PREFIX}"
        )
```

**C# side (bridge validation before filesystem access):**
```csharp
private static readonly string JailPrefix =
    Path.GetFullPath(Path.Combine(Application.dataPath, "JarvisGenerated"));

public static bool IsInJail(string absolutePath)
{
    var normalized = Path.GetFullPath(absolutePath);
    return normalized.StartsWith(JailPrefix, StringComparison.OrdinalIgnoreCase);
}

// Called before any file write in the bridge
public static void AssertInJail(string path)
{
    if (!IsInJail(path))
        throw new UnauthorizedAccessException(
            $"Bridge path jail violation: {path}");
}
```

**Key normalization detail:** Use `Path.GetFullPath()` on C# side (collapses `..`) and `os.path.normpath()` on Python side. String prefix matching alone is insufficient — `Assets/JarvisGenerated/../Dangerous` passes a naive prefix check.

### Pattern 5: Pre-Compile Static Analysis (Python Side)

**What:** Before writing any generated C# file to disk, a Python scanner checks for dangerous API patterns using regex over the generated code string. This runs entirely in Python before any WebSocket call — it blocks the file write if violations are detected.

**When to use:** Every time `write_script()` is called with LLM-generated C# content.

**Banned patterns:**
```python
import re

_DANGEROUS_PATTERNS = [
    # Process execution
    (r'\bProcess\.Start\b', "Process.Start is forbidden in generated code"),
    (r'\bSystem\.Diagnostics\.Process\b', "System.Diagnostics.Process is forbidden"),
    # File deletion outside jail
    (r'\bFile\.Delete\b', "File.Delete requires explicit approval"),
    (r'\bDirectory\.Delete\b', "Directory.Delete requires explicit approval"),
    (r'\bFileUtil\.DeleteFileOrDirectory\b', "FileUtil.DeleteFileOrDirectory forbidden"),
    (r'\bAssetDatabase\.DeleteAsset\b', "AssetDatabase.DeleteAsset requires approval"),
    # Path traversal markers
    (r'\.\.[\\/]', "Path traversal sequence detected"),
    (r'\bApplication\.dataPath\b.*\.\.',
     "dataPath combined with .. traversal is forbidden"),
    # Dynamic assembly loading
    (r'\bAssembly\.LoadFrom\b', "Assembly.LoadFrom is forbidden"),
    (r'\bAssembly\.Load\b\s*\(', "Assembly.Load is forbidden"),
    # Reflection on non-Editor code
    (r'\bGetMethod\b.*\bInvoke\b', "Runtime reflection dispatch forbidden in generated game code"),
]

def _assert_safe_code(content: str) -> None:
    """Raise if content contains dangerous API patterns."""
    for pattern, message in _DANGEROUS_PATTERNS:
        if re.search(pattern, content):
            raise ValueError(f"Static analysis blocked: {message}")
```

**Why regex over Roslyn:** Roslyn analyzers in Unity 6.3 run at IDE edit time (Visual Studio / Rider) and are NOT guaranteed to run as compilation gates in the Unity Editor's own compiler. The Python pre-write scan is the reliable enforcement point. A Roslyn analyzer can be added as defense-in-depth but cannot be the primary gate.

### Pattern 6: Compilation Result Relay

**What:** After a script write triggers domain reload and compilation, the bridge waits for `CompilationPipeline.assemblyCompilationFinished` events, collects all compiler errors, and sends them back to Python as a JSON-RPC notification. Python `UnityTool` surfaces these errors as a `ToolResult` for the agent's `ReflectionLoop`.

**Example:**
```csharp
// Compilation error collection in bridge
[InitializeOnLoad]
public static class CompilationWatcher
{
    static CompilationWatcher()
    {
        CompilationPipeline.assemblyCompilationFinished +=
            (assemblyPath, messages) =>
            {
                var errors = messages
                    .Where(m => m.type == CompilerMessageType.Error)
                    .Select(m => new { m.message, m.file, m.line })
                    .ToList();
                if (errors.Any())
                    JarvisEditorBridge.SendNotification("compilation_errors", errors);
            };
    }
}
```

### Anti-Patterns to Avoid

- **Calling Assembly.GetTypes() per request:** Causes ~200ms GC allocation on every JSON-RPC call. Build cache once in static constructor.
- **Using System.Net.WebSockets server mode in Unity:** `HttpListener.IsWebSocketRequest` always returns false in Unity's Mono runtime. The stdlib server does not work.
- **Sending commands while domain reload is in progress:** Unity's C# AppDomain is torn down during reload — the bridge does not exist. Python must enter `WAITING_FOR_BRIDGE` and wait for the ready heartbeat.
- **String prefix check without normalization for path jail:** `Assets/JarvisGenerated/../Dangerous` passes a naive `StartsWith` check. Always use `Path.GetFullPath()` before comparing.
- **Writing one .cs file per command:** Each write triggers a domain reload (~2-15 seconds). Batch all .cs files needed for one agent step into a single write command, triggering one reload.
- **Placing bridge code outside Editor/ folder:** Any .cs file not under an `Editor/` folder compiles into game builds and is subject to IL2CPP stripping and reflection limitations.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket server in C# Unity Editor | Custom TcpListener + RFC 6455 handshake | websocket-sharp (WebSocketSharp.Standard 1.0.3) | 200+ lines of handshake code with edge cases; websocket-sharp is battle-tested and handles ping/pong, fragmentation, close handshake |
| JSON serialization in C# | Custom JSON parser | Newtonsoft.Json (com.unity.nuget.newtonsoft-json 3.0.1) | IL2CPP-safe; JObject API handles dynamic dispatch parameters cleanly |
| Python JSON-RPC framing | Custom request/response class | Plain `json.dumps/loads` with `{"jsonrpc":"2.0","id":N,"method":M,"params":P}` | JSON-RPC 2.0 spec is 5 fields — no library needed; `jsonrpc-websocket` is optional if verbosity grows |
| Domain reload detection | Polling `EditorApplication.isCompiling` | `AssemblyReloadEvents.afterAssemblyReload` callback | Polling with sleep is unreliable under load; event callback is exact and zero-cost |
| Compiler error collection | Parsing Unity console text | `CompilationPipeline.assemblyCompilationFinished` event | Official API returns structured `CompilerMessage` objects with file, line, message fields |

**Key insight:** The C# side of this phase is almost entirely Unity Editor APIs with websocket-sharp as the only external library. Unity provides everything needed for compilation events, asset management, and editor scripting — the challenge is knowing which APIs to use and in what order.

---

## Common Pitfalls

### Pitfall 1: Silent Command Drop During Domain Reload

**What goes wrong:** Python sends a JSON-RPC command while Unity is in mid-reload. The WebSocket server is gone (C# AppDomain torn down). The message is accepted at the TCP layer (port still bound briefly) but never processed. Python gets a timeout that looks like a Unity hang.

**Why it happens:** Domain reload takes 2-15 seconds. Any `.cs` file write triggers one. The bridge server is torn down at the start of reload, not at the end.

**How to avoid:** Python `UnityTool.write_script()` transitions to `WAITING_FOR_BRIDGE` state before sending the write command. `_listen_for_heartbeat()` background task watches for `{"status":"ready"}` from bridge after reload completes. No further commands sent until heartbeat received.

**Warning signs:** Repeated `asyncio.TimeoutError` on `recv()` followed by a successful command — reload was in progress.

### Pitfall 2: websocket-sharp Port Binding on Domain Reload

**What goes wrong:** The static constructor fires after domain reload and tries to bind port 8091. But the previous server instance's `Stop()` call didn't fully release the socket before the new one tries to bind. `AddressAlreadyInUse` exception is thrown, bridge fails to start.

**Why it happens:** `[InitializeOnLoad]` static constructor runs immediately after reload — sometimes before the OS fully releases the port from the previous binding. The `StopServer()` call in the new constructor fires on the NEW domain's reference, which is null (old server object was garbage collected with the old domain).

**How to avoid:**
1. Call `StopServer()` in `EditorApplication.quitting` AND in `AssemblyReloadEvents.beforeAssemblyReload` callbacks.
2. Add a `try/catch` around `_server.Start()` with a 500ms retry using `EditorApplication.delayCall`.
3. Use `SO_REUSEADDR` socket option (websocket-sharp supports this via `WebSocketServer.ReuseAddress = true`).

**Warning signs:** Unity console shows `System.Net.Sockets.SocketException (0x80004005): Address already in use` on Editor startup.

### Pitfall 3: TypeCoercer Fails on Overloaded Methods

**What goes wrong:** `ReflectionCommandDispatcher` caches `first-overload-wins` for duplicate method names. When Python sends `{"method":"EditorApplication.OpenProject","params":{"path":"..."}}`, the cache may have bound the wrong overload (e.g., the one expecting `(string, OpenProjectMode)` instead of `(string)`). The TypeCoercer fails to match parameters and throws `ArgumentException`.

**Why it happens:** `Dictionary.TryAdd` uses first-wins. `Assembly.GetTypes()` returns types in an undefined order. Overload resolution is not performed.

**How to avoid:**
1. Cache by `$"{TypeName}.{MethodName}"` as the primary key.
2. When a dispatch request comes in, if multiple overloads exist, disambiguate by matching parameter count: filter `_cache` values where `method.GetParameters().Length == args.Count`.
3. Store `List<MethodInfo>` per key (not single entry) and select best match at dispatch time. This is the correct implementation despite the Architecture.md showing single-entry — the single-entry approach works for ~90% of APIs but fails on common multi-overload methods like `Debug.Log`.

**Warning signs:** `ArgumentException: Object of type X cannot be converted to type Y` from TypeCoercer on simple Unity API calls.

### Pitfall 4: Path Jail Bypass via Application.dataPath + Relative Prefix

**What goes wrong:** Agent generates C# code containing `Application.dataPath + "/JarvisGenerated/../../SomeOtherFolder"`. The Python-side regex check passes because it sees `JarvisGenerated` in the string. The C# execution normalizes the path and writes outside the jail.

**Why it happens:** String prefix checks on non-normalized paths are easily bypassed by `../` sequences. This is a well-documented directory traversal pattern.

**How to avoid:** Always normalize before checking. Python: `os.path.normpath(path)`. C#: `Path.GetFullPath(path)` with `Application.dataPath` as base. The normalized path must start with the jail prefix, not the pre-normalization string.

### Pitfall 5: Roslyn Analyzer Does Not Block Unity Compilation

**What goes wrong:** Developer adds a Roslyn analyzer `.dll` with a custom rule blocking `Process.Start`. It shows warnings in Visual Studio/Rider. But when Unity compiles scripts (its own compiler invocation), the analyzer either does not run or runs as a suggestion (not error) and compilation proceeds.

**Why it happens:** Unity 6.3's Roslyn analyzer support is IDE-scoped. Custom `DiagnosticDescriptor` with `DiagnosticSeverity.Error` in a `.dll` placed in `Assets/` will show errors in IDE but Unity's own compiler may not honor the ruleset as a hard block. This is confirmed by Unity's own documentation: "Roslyn analyzers are only compatible with the IDEs that Unity publicly supports."

**How to avoid:** Do NOT rely on a Roslyn analyzer as the primary gate for dangerous API blocking. Use the Python-side pre-write regex scan (`_assert_safe_code()`) as the authoritative gate. A Roslyn analyzer provides IDE feedback but is not a runtime enforcement mechanism.

---

## Code Examples

Verified patterns from official sources:

### websocket-sharp Server Startup (C#)
```csharp
// Source: websocket-sharp official docs (sta.github.io/websocket-sharp)
// ReuseAddress prevents port binding failures on domain reload
var server = new WebSocketServer("ws://localhost:8091");
server.ReuseAddress = true;
server.AddWebSocketService<JarvisBridgeService>("/jarvis");
server.Start();
```

### websocket-sharp Service Handler (C#)
```csharp
// Source: websocket-sharp official docs
public class JarvisBridgeService : WebSocketBehavior
{
    protected override void OnMessage(MessageEventArgs e)
    {
        var request = JsonConvert.DeserializeObject<JsonRpcRequest>(e.Data);
        try
        {
            var result = _dispatcher.Dispatch(request.Method, request.Params);
            var response = new JsonRpcResponse
            {
                Id = request.Id,
                Result = result
            };
            Send(JsonConvert.SerializeObject(response));
        }
        catch (Exception ex)
        {
            var errorResponse = new JsonRpcResponse
            {
                Id = request.Id,
                Error = new JsonRpcError { Code = -32603, Message = ex.Message }
            };
            Send(JsonConvert.SerializeObject(errorResponse));
        }
    }

    protected override void OnOpen()
    {
        // Send ready heartbeat immediately on new connection
        Send(JsonConvert.SerializeObject(new { status = "ready" }));
    }
}
```

### Python websockets Client — Connect and Call (Python)
```python
# Source: websockets 16.0 official docs (websockets.readthedocs.io)
import asyncio
import json
from websockets.asyncio.client import connect

async def call_unity(method: str, params: dict) -> dict:
    async with connect("ws://localhost:8091/jarvis") as ws:
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        await ws.send(json.dumps(request))
        raw = await ws.recv()
        return json.loads(raw)
```

### Python websockets Auto-Reconnect Pattern (Python)
```python
# Source: websockets 16.0 official docs — reconnect on domain reload
async def persistent_bridge_client():
    async for ws in connect("ws://localhost:8091/jarvis"):
        try:
            async for message in ws:
                await handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            continue  # reconnects with exponential backoff
```

### AssemblyReloadEvents Ready Heartbeat (C#)
```csharp
// Source: Unity 6.3 scripting API
[InitializeOnLoad]
public static class BridgeHeartbeat
{
    static BridgeHeartbeat()
    {
        AssemblyReloadEvents.afterAssemblyReload += () =>
        {
            // Domain fully reconstituted — signal Python client
            JarvisEditorBridge.BroadcastReady();
        };
    }
}
```

### CompilationPipeline Error Collection (C#)
```csharp
// Source: Unity 6.3 scripting API
// docs.unity3d.com/6000.3/Documentation/ScriptReference/Compilation.CompilationPipeline-assemblyCompilationFinished.html
CompilationPipeline.assemblyCompilationFinished +=
    (path, messages) =>
    {
        var errors = messages
            .Where(m => m.type == CompilerMessageType.Error)
            .Select(m => $"{m.file}({m.line}): {m.message}")
            .ToArray();
        if (errors.Length > 0)
            JarvisEditorBridge.SendNotification("compile_errors",
                new { assembly = path, errors });
    };
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Unity Python Scripting package (stdin/stdout, port 18861) | WebSocket JSON-RPC (custom bridge) | Unity 6.1 — package deprecated | Python Scripting required Python 2.7 and ran inside Unity process; WebSocket is language-agnostic and out-of-process |
| HTTP REST polling for Unity commands | Persistent WebSocket | ~2024 MCP tooling wave | HTTP creates new TCP connection per call (~5ms overhead); WebSocket is persistent (~0.5ms per message) |
| Manual wrapper per Unity API method | Reflection-based dispatch | Established pattern in mcp-unity (2024) | 300+ wrapper methods vs. automatic full API coverage via Assembly.GetTypes() cache |
| Roslyn analyzer as security gate | Python pre-write regex scan + Roslyn as IDE hint | Unity 6.3 documentation clarification (2025) | Roslyn analyzers are IDE-scoped, not Unity compiler-level gates |

**Deprecated/outdated:**
- `com.unity.scripting.python`: Requires Python 2.7, deprecated as of Unity 6.1, runs inside Unity process (can't reach Jarvis Python code)
- `System.Net.WebSockets` server mode in Unity Mono: `HttpListener.IsWebSocketRequest` always returns false — never worked for server use
- `Assembly.GetTypes()` per request: GC-allocating, slow — replaced by startup cache pattern

---

## Open Questions

1. **websocket-sharp version for Unity 6.3 / .NET Standard 2.1**
   - What we know: websocket-sharp main repo (sta/websocket-sharp) has not had a release since 2022. WebSocketSharp.Standard 1.0.3 on NuGet is the maintained fork targeting .NET Standard.
   - What's unclear: Whether WebSocketSharp.Standard 1.0.3 is compatible with Unity 6.3's Mono/.NET Standard 2.1 runtime without modification.
   - Recommendation: Test `WebSocketSharp.Standard 1.0.3` DLL in Unity 6.3 as a first implementation step (Wave 0). If incompatible, fallback is mcp-unity's embedded websocket-sharp fork (CoderGamester uses a bundled copy, MIT license, extractable from their repo).

2. **TypeCoercer completeness for common Unity types**
   - What we know: Simple types (string, int, float, bool) are trivial to coerce from JSON. Unity types (Vector3, Quaternion, Color) require custom deserialization from `{"x":1,"y":0,"z":0}`.
   - What's unclear: Which Unity types appear most frequently in the 20 most-used Unity Editor API methods the agent will initially call.
   - Recommendation: Implement TypeCoercer for string/int/float/bool/Vector2/Vector3/Color in Phase 21. Extend in later phases as new method invocations reveal new type requirements.

3. **Port 8091 availability on target machine**
   - What we know: Port 8091 was chosen to avoid collision with mobile API (8787), Unity Python Scripting (18861), mcp-unity (8090).
   - What's unclear: Whether any other software on Conner's Windows 11 system uses 8091.
   - Recommendation: `UnityTool` should be configurable via Jarvis `config.json` (key: `unity_bridge_port`, default: 8091). Bridge reads same port from a `ProjectSettings/JarvisSettings.json` file.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, 6073 tests passing) |
| Config file | engine/tests/ (no separate config file, standard pytest discovery) |
| Quick run command | `python -m pytest engine/tests/test_unity_tool.py -x -q` |
| Full suite command | `python -m pytest engine/tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UNITY-01 | `UnityTool` sends JSON-RPC request and parses response | unit (mock WS server) | `pytest engine/tests/test_unity_tool.py::test_call_sends_jsonrpc -x` | Wave 0 |
| UNITY-02 | Reflection cache covers 1200+ public static methods | manual-only (requires Unity Editor) | Manual: launch Unity, check bridge log for cache size | N/A |
| UNITY-03 | `UnityTool` enters WAITING_FOR_BRIDGE when WS closes and resumes on reconnect | unit (mock reconnect) | `pytest engine/tests/test_unity_tool.py::test_waiting_for_bridge_state -x` | Wave 0 |
| UNITY-04 | `UnityTool.write_script()` enforces path jail before sending | unit | `pytest engine/tests/test_unity_tool.py::test_write_script_path_jail -x` | Wave 0 |
| CODE-04 | `_assert_safe_code()` blocks all 8 dangerous patterns | unit | `pytest engine/tests/test_unity_tool.py::test_static_analysis_guard -x` | Wave 0 |
| CODE-05 | `_assert_in_jail()` rejects path traversal attempts | unit | `pytest engine/tests/test_unity_tool.py::test_path_jail_normalization -x` | Wave 0 |

**Note on UNITY-02:** The C# `ReflectionCommandDispatcher` and domain reload behavior require a live Unity Editor — these are integration tests not automatable in pytest. Verify manually as part of Wave 1 smoke test.

### Sampling Rate
- **Per task commit:** `python -m pytest engine/tests/test_unity_tool.py -x -q`
- **Per wave merge:** `python -m pytest engine/tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `engine/tests/test_unity_tool.py` — covers UNITY-01, UNITY-03, UNITY-04, CODE-04, CODE-05
- [ ] `engine/src/jarvis_engine/agent/tools/__init__.py` — package init for tools subpackage
- [ ] `engine/src/jarvis_engine/agent/tools/unity_tool.py` — the tool itself

*(C# test infrastructure is not automatable via pytest. Unity-side validation is manual smoke test.)*

---

## Sources

### Primary (HIGH confidence)
- websockets 16.0 official docs (websockets.readthedocs.io) — connect(), send(), recv(), auto-reconnect pattern
- Unity 6.3 scripting API: AssemblyReloadEvents.afterAssemblyReload (docs.unity3d.com/6000.3)
- Unity 6.3 scripting API: CompilationPipeline.assemblyCompilationFinished (docs.unity3d.com/6000.3)
- Unity 6.3 scripting API: CompilationPipeline.compilationFinished (docs.unity3d.com/6000.3)
- Unity Manual: Roslyn analyzers and source generators (docs.unity3d.com/6000.3/Documentation/Manual/roslyn-analyzers.html) — confirmed IDE-scoped only
- ARCHITECTURE.md (this project, 2026-03-16) — WebSocket JSON-RPC pattern, reflection cache, component responsibilities
- PITFALLS.md (this project, 2026-03-16) — domain reload deadlock, path jail, reflection IL2CPP, unsafe code execution
- STACK.md (this project, 2026-03-16) — websocket-sharp decision, Newtonsoft.Json, websockets Python library

### Secondary (MEDIUM confidence)
- mcp-unity by CoderGamester (github.com/CoderGamester/mcp-unity) — confirmed uses websocket-sharp for C# WS server in Unity Editor, port 8090 default
- websocket-sharp official (sta.github.io/websocket-sharp) — WebSocketServer API, AddWebSocketService, ReuseAddress flag
- NuGet: WebSocketSharp.Standard 1.0.3 — .NET Standard fork maintained for modern .NET targets
- Unity Manual: Avoid GC allocations from reflection (docs.unity3d.com/6) — confirmed cache-at-startup requirement
- WebSearch: HttpListener.IsWebSocketRequest always returns false in Unity Mono — confirms stdlib server mode does not work

### Tertiary (LOW confidence)
- WebSearch: websocket-sharp domain reload freeze issue (github.com/sta/websocket-sharp/issues/35) — old issue (2014) with Unity + websocket-sharp freeze; ReuseAddress mitigates; may need to retest with current version
- WebSearch: jsonrpc-websocket 3.2.0 (pypi.org) — optional Python helper; not needed unless raw JSON handling grows complex

---

## Metadata

**Confidence breakdown:**
- Standard stack (websocket-sharp + Newtonsoft.Json + websockets): HIGH — confirmed by mcp-unity reference implementation and official docs
- Architecture (domain reload handling, reflection cache): HIGH — based on Unity official APIs and PITFALLS.md verified patterns
- Python UnityTool patterns: HIGH — websockets 16.0 API verified from official docs
- Path jail implementation: HIGH — Path.GetFullPath() normalization is stdlib; pattern is standard directory traversal prevention
- Pre-compile static analysis (Python regex): HIGH — regex over string is reliable; documented Roslyn limitation confirmed from official Unity docs
- TypeCoercer completeness: MEDIUM — simple types are clear; Unity types (Vector3 etc.) need prototype testing
- websocket-sharp Unity 6.3 compatibility: MEDIUM — library exists and is used by mcp-unity, but exact version/fork compatibility with Unity 6.3 Mono runtime needs empirical verification

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (websocket-sharp and websockets are stable; Unity APIs are stable for Unity 6 LTS lifecycle)
