# Architecture Research

**Domain:** Autonomous Unity game development agent integrated into Jarvis AI assistant
**Researched:** 2026-03-16
**Confidence:** HIGH (communication protocol, Unity plugin structure, agent patterns) / MEDIUM (reflection dispatch performance bounds, streaming backpressure)

---

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        JARVIS DESKTOP ENGINE (Python)                │
├────────────────┬─────────────────────────────┬───────────────────────┤
│  Daemon Loop   │     CQRS Command Bus         │   Mobile HTTP API     │
│  (existing)    │     (70+ commands)           │   port 8787 (exist.)  │
├────────────────┴──────────┬──────────────────┴───────────────────────┤
│                           │                                           │
│     ┌─────────────────────▼────────────────────────────────────┐     │
│     │                  AGENT SUBSYSTEM (NEW)                    │     │
│     │  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐  │     │
│     │  │TaskPlanner │  │StepExecutor  │  │ ReflectionLoop   │  │     │
│     │  │            │  │              │  │                  │  │     │
│     │  │ - decompose│  │ - run 1 step │  │ - eval outcome   │  │     │
│     │  │ - sequence │  │ - call tools │  │ - replan on fail │  │     │
│     │  │ - DAG deps │  │ - emit prog  │  │ - learn pattern  │  │     │
│     │  └─────┬──────┘  └──────┬───────┘  └────────┬─────────┘  │     │
│     │        └────────────────┴──────────────────┘            │     │
│     │                         │                                │     │
│     │     ┌───────────────────▼──────────────────────────┐     │     │
│     │     │            TOOL REGISTRY (NEW)               │     │     │
│     │     │  UnityTool | BlenderTool | ShellTool          │     │     │
│     │     │  FileTool  | WebTool     | KGTool             │     │     │
│     │     └───────────────────┬──────────────────────────┘     │     │
│     │                         │                                │     │
│     │     ┌───────────────────▼──────────────────────────┐     │     │
│     │     │        AGENT STATE STORE (NEW — SQLite)       │     │     │
│     │     │  task_id | plan_json | checkpoint | status    │     │     │
│     │     └──────────────────────────────────────────────┘     │     │
│     └───────────────────────────────────────────────────────────┘     │
│                                                                       │
│     ┌─────────────────────────────────────────────────────────────┐   │
│     │       PROGRESS EVENT BUS  (asyncio.Queue + SSE emitter)     │   │
│     │       Feeds: Desktop Widget (Tkinter) + Unity Plugin        │   │
│     └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
│     Existing: MemoryEngine | KnowledgeGraph | ModelGateway            │
└───────────────────────────┬───────────────────────────────────────────┘
                            │
              WebSocket JSON-RPC  (port 8091, ws://localhost)
                            │
┌───────────────────────────▼───────────────────────────────────────────┐
│                   UNITY EDITOR PLUGIN (C#, new)                       │
├───────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
│  │ JarvisEditorBridge│  │ JarvisPanel    │  │ AssetPostprocessor     │ │
│  │ (WebSocket client)│  │ (EditorWindow) │  │ hooks                  │ │
│  │                  │  │                │  │                        │ │
│  │ - ws connect     │  │ - progress UI  │  │ - OnPostprocessAll     │ │
│  │ - dispatch cmds  │  │ - approve btn  │  │   Assets callback      │ │
│  │ - reflection map │  │ - log stream   │  │ - notify Jarvis of     │ │
│  └──────────────────┘  └────────────────┘  │   import completion    │ │
│                                            └────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │               ReflectionCommandDispatcher (C#, new)              │  │
│  │  - builds method cache from Assembly.GetTypes() at startup       │  │
│  │  - maps JSON "method" string -> MethodInfo                       │  │
│  │  - converts JSON args -> C# parameter types                      │  │
│  │  - returns serialized result or structured error                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Lives In |
|-----------|----------------|----------|
| TaskPlanner | Decomposes natural language goal into ordered DAG of steps, each with a tool call | Python — agent/planner.py |
| StepExecutor | Executes one step: calls the assigned tool, captures result, emits progress event | Python — agent/executor.py |
| ReflectionLoop | After each step evaluates success/failure, replans if needed, caps at 5 retries, records error patterns in KG | Python — agent/reflection.py |
| ToolRegistry | Maintains dict of registered tools keyed by name; each tool is a dataclass with schema + callable | Python — agent/tool_registry.py |
| UnityTool | Sends JSON-RPC call over WebSocket to JarvisEditorBridge, waits for result | Python — agent/tools/unity_tool.py |
| AgentStateStore | SQLite table `agent_tasks` with full plan JSON, step index, checkpoint blob, approval flags | Python — agent/state_store.py |
| ProgressEventBus | asyncio.Queue feeding a background thread that pushes SSE to `/agent/stream` endpoint | Python — mobile_routes/agent.py (new route module) |
| JarvisEditorBridge | C# WebSocket client inside Unity Editor; receives JSON-RPC, dispatches to ReflectionCommandDispatcher | C# — Editor/JarvisEditorBridge.cs |
| JarvisPanel | EditorWindow showing task progress, step log, approve/reject buttons | C# — Editor/JarvisPanel.cs |
| ReflectionCommandDispatcher | Builds and caches reflection map of all UnityEditor + UnityEngine methods at startup; handles JSON-RPC dispatch | C# — Editor/ReflectionCommandDispatcher.cs |
| AssetPostprocessorHook | Intercepts import completion events, notifies bridge so agent can continue after asset operations | C# — Editor/JarvisAssetPostprocessor.cs |

---

## Recommended Project Structure

```
engine/src/jarvis_engine/
├── agent/                        # NEW — entire agent subsystem
│   ├── __init__.py
│   ├── planner.py                # TaskPlanner: goal → DAG[AgentStep]
│   ├── executor.py               # StepExecutor: AgentStep → StepResult
│   ├── reflection.py             # ReflectionLoop: eval + replan + KG write
│   ├── state_store.py            # AgentStateStore: SQLite task persistence
│   ├── tool_registry.py          # ToolRegistry: register/lookup/schema
│   ├── approval_gate.py          # ApprovalGate: create=auto, destroy/spend=block
│   ├── progress_bus.py           # ProgressEventBus: asyncio.Queue → SSE
│   └── tools/
│       ├── __init__.py
│       ├── unity_tool.py         # WebSocket JSON-RPC to Unity
│       ├── blender_tool.py       # Blender CLI subprocess
│       ├── shell_tool.py         # Sandboxed subprocess
│       ├── file_tool.py          # Read/write project files
│       ├── web_tool.py           # Web research (wraps existing web/fetch.py)
│       └── kg_tool.py            # KG query/inject (wraps existing knowledge/)
│
├── mobile_routes/
│   ├── agent.py                  # NEW route module: /agent/* endpoints + SSE
│   └── ... (existing)
│
└── commands/
    └── agent_commands.py         # NEW: AgentRunCommand, AgentStatusCommand, etc.

UnityProject/
└── Assets/
    └── Editor/
        └── Jarvis/
            ├── JarvisEditorBridge.cs         # WebSocket client, entry point
            ├── JarvisPanel.cs                # EditorWindow UI
            ├── ReflectionCommandDispatcher.cs # Reflection cache + dispatch
            ├── JarvisAssetPostprocessor.cs   # AssetPostprocessor hooks
            ├── Models/
            │   ├── JsonRpcRequest.cs
            │   └── JsonRpcResponse.cs
            └── Util/
                └── TypeCoercer.cs            # JSON → C# param coercion
```

### Structure Rationale

- **agent/ subpackage:** Mirrors existing subpackage convention (voice/, stt/, learning/, gateway/). Keeps all agent logic isolated from existing engine code. Handlers in handlers/ remain thin — they delegate to agent/ classes.
- **agent/tools/ sub-subpackage:** Each tool is a separate file. New tools (tripo.io, etc.) added without touching core. ToolRegistry discovers them via explicit registration (not auto-discovery — explicit is safer given security requirements).
- **mobile_routes/agent.py:** Follows existing pattern of one route module per domain. Adds /agent/run, /agent/status, /agent/approve, /agent/stream (SSE) without modifying existing server.py.
- **Editor/Jarvis/ folder:** Unity convention is Editor/ for editor-only code. The Jarvis/ subfolder namespaces it. All files compile only in Editor builds (not shipped with game).

---

## Architectural Patterns

### Pattern 1: WebSocket JSON-RPC (Python → Unity)

**What:** Python agent sends JSON-RPC 2.0 messages over a persistent WebSocket. Unity Editor hosts the server. Python side is the client. Protocol follows JSON-RPC 2.0 spec: `{"jsonrpc":"2.0","id":1,"method":"CreateGameObject","params":{"name":"Player","parent":null}}`.

**When to use:** This is the recommended channel for agent→Unity communication. Real-world implementations (mcp-unity by CoderGamester, IvanMurzak/Unity-MCP) all converge on WebSocket+JSON as the practical solution. HTTP polling was considered and rejected: round-trip latency and connection overhead per call makes multi-step agents slow.

**Why not stdin/stdout:** stdin/stdout (used by Unity's official Python Scripting package, port 18861) requires Python 2.7 and is being deprecated as of Unity 6.1. Not viable.

**Why not HTTP REST:** HTTP works for single request-response but requires a new connection per call. WebSocket keeps the socket open across the hundreds of editor API calls an autonomous agent generates per task. Latency drops from ~5ms per call to ~0.5ms.

**Port:** 8091 (avoids collision with mobile API on 8787, Unity's built-in Python port 18861, mcp-unity's default 8090).

**Unity side server setup:**
```csharp
// JarvisEditorBridge.cs — starts on editor load
[InitializeOnLoad]
public static class JarvisEditorBridge
{
    private static WebSocketServer _server;

    static JarvisEditorBridge()
    {
        _server = new WebSocketServer("ws://localhost:8091");
        _server.AddWebSocketService<JarvisBridgeService>("/jarvis");
        _server.Start();
        EditorApplication.quitting += () => _server.Stop();
    }
}
```

**Trade-offs:**
- WebSocket requires websocket-server-sharp or websocket-sharp C# lib (MIT license, available via UPM)
- Python side uses `websockets` library (already available in project or trivially added)
- Persistent connection means Unity must be open — agent fails gracefully if Unity disconnects (tool returns error, ReflectionLoop triggers replan)

### Pattern 2: Reflection Command Dispatch (C# Unity side)

**What:** ReflectionCommandDispatcher builds a dictionary from method name string to MethodInfo at editor startup (once). Each incoming JSON-RPC call looks up the cached MethodInfo, coerces JSON args to C# parameter types, invokes, serializes result.

**When to use:** Required for full Unity API coverage without writing 300+ wrapper methods. Reflection is acceptable in editor-only code where GC pressure is not a concern (Unity's "Avoid reflection at runtime" warning applies to gameplay, not Editor tools).

**Cache-at-startup pattern prevents GC pressure:**
```csharp
// ReflectionCommandDispatcher.cs
public class ReflectionCommandDispatcher
{
    private readonly Dictionary<string, MethodInfo> _cache = new();

    public void BuildCache()
    {
        // Called once on bridge startup, not per-call
        var editorAssembly = typeof(UnityEditor.EditorApplication).Assembly;
        var engineAssembly = typeof(UnityEngine.GameObject).Assembly;
        foreach (var asm in new[] { editorAssembly, engineAssembly })
        {
            foreach (var type in asm.GetTypes())
            {
                foreach (var method in type.GetMethods(
                    BindingFlags.Public | BindingFlags.Static))
                {
                    var key = $"{type.Name}.{method.Name}";
                    _cache.TryAdd(key, method);  // first wins on overload
                }
            }
        }
    }

    public object Dispatch(string methodKey, JObject args)
    {
        if (!_cache.TryGetValue(methodKey, out var method))
            throw new KeyNotFoundException($"Unknown method: {methodKey}");
        var parameters = CoerceParameters(method, args);
        return method.Invoke(null, parameters);
    }
}
```

**Trade-offs:**
- Startup build takes ~200-400ms in editor (one-time, acceptable)
- Method overloads: first match wins; complex overloads need explicit disambiguation via param count hint in JSON
- Static methods only by default; instance methods require object reference (scoped to specific instance)
- Full coverage for the ~1200 public static methods in UnityEditor namespace

### Pattern 3: Agent State Checkpointing (SQLite)

**What:** Every step transition persists to `agent_tasks` SQLite table before execution begins. On failure or restart, agent resumes from last successful checkpoint. Structure follows the three-layer model: mission state (plan JSON + step index), tool context (last tool result cache), system config (model used, temp settings).

**When to use:** Any task expected to run >30 seconds or involving >3 tool calls. Unity game development tasks routinely take minutes (compilation, import pipelines). Checkpointing is non-negotiable.

**Schema:**
```sql
CREATE TABLE agent_tasks (
    task_id     TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,
    plan_json   TEXT NOT NULL,          -- full DAG as JSON
    step_index  INTEGER DEFAULT 0,     -- next step to execute
    checkpoint  TEXT,                  -- last tool result cache (JSON)
    status      TEXT DEFAULT 'pending',-- pending/running/blocked/done/failed
    approval_required TEXT,            -- JSON list of step IDs needing approval
    error_count INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

**Integration with existing MissionStateStore:** The agent_tasks table mirrors the existing missions FSM (VALID_TRANSITIONS in learning/missions.py). The agent uses the same state machine logic — reuse `_check_transition` to enforce valid state changes.

**Trade-offs:**
- SQLite WAL mode (already set in Jarvis) handles concurrent reads from daemon loop
- Checkpoint blob grows with task complexity; prune after completion
- Step index approach (vs event sourcing) is simpler to implement and sufficient for linear/DAG plans

### Pattern 4: Progress Streaming via SSE

**What:** A background asyncio task drains an `asyncio.Queue` and emits Server-Sent Events on a `/agent/stream` HTTP endpoint. Both the Tkinter widget and Unity plugin consume this stream. Widget uses an http.client polling loop (Tkinter is not async); Unity uses a coroutine with UnityWebRequest.

**When to use:** One-way push from agent to consumers. SSE chosen over WebSocket for this direction because: (1) it's unidirectional (agent→consumer only), (2) works over plain HTTP (reuses existing ThreadingHTTPServer), (3) Unity side consumes it via standard UnityWebRequest, (4) simpler than a second WebSocket server.

**Event format:**
```
data: {"event":"step_start","task_id":"t-123","step":3,"tool":"unity","desc":"Creating Player prefab"}\n\n
data: {"event":"step_done","task_id":"t-123","step":3,"result":"ok","elapsed_ms":412}\n\n
data: {"event":"approval_needed","task_id":"t-123","step":5,"desc":"Delete all assets in /Audio"}\n\n
```

**Python SSE route (added to mobile_routes/agent.py):**
```python
def handle_agent_stream(self):
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.end_headers()
    # Long-poll: drain queue, write events, flush
    queue = get_agent_progress_queue()
    while True:
        try:
            event = queue.get(timeout=30)
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
        except Empty:
            self.wfile.write(b":\n\n")  # keep-alive ping
            self.wfile.flush()
```

**Trade-offs:**
- ThreadingHTTPServer (existing transport) is not async-native; the queue.get(timeout) pattern is the standard workaround — it blocks the handler thread but each SSE client gets its own thread
- For >1 concurrent consumer (widget + Unity panel), the queue needs a publish/subscribe fan-out: maintain list of per-client queues, broadcast to all on event emit
- Backpressure: bound queue to 256 items; slow consumers are dropped not blocked

### Pattern 5: Tool Registry with Schema-First Design

**What:** Each tool is a dataclass implementing a `ToolSpec` protocol: `name`, `description`, `parameters_schema` (JSON Schema dict), `execute(params: dict) -> ToolResult`. The registry is a dict keyed by name. The TaskPlanner receives the full registry schema to include in its LLM prompt context so the planner knows what tools are available.

**When to use:** Any capability that agent code needs to call. New tools (tripo.io 3D generation, Blender CLI, etc.) added by implementing ToolSpec and calling `registry.register(tool)` — no changes to planner or executor.

**Pattern:**
```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict  # JSON Schema
    requires_approval: bool = False

    def execute(self, params: dict) -> ToolResult: ...

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered")
        return self._tools[name]

    def schema_for_prompt(self) -> list[dict]:
        """Returns OpenAI-compatible tool schema list for LLM prompt injection."""
        return [
            {"name": t.name, "description": t.description,
             "parameters": t.parameters_schema}
            for t in self._tools.values()
        ]
```

**Integration with ModelGateway:** The TaskPlanner calls ModelGateway with tool schemas injected into the system prompt. ModelGateway already handles Ollama (local) and cloud fallback. No changes to gateway needed — the planner formats the tool list as part of its system prompt, not as OpenAI-style function calling (which local Ollama models support inconsistently).

**Trade-offs:**
- Prompt-injection of tool schemas (vs structured function calling) works across all models in the existing gateway
- Tool schema size matters: 10 tools × ~200 token schema = 2000 tokens added to every planning call. Stay under 15 tools in base registry; add domain-specific tools per task type.
- Approval gating in ToolSpec.requires_approval is checked in StepExecutor before calling execute(); destructive tools (DeleteGameObject, SpendMoney, DestroyAsset) are marked true

### Pattern 6: AssetPostprocessor as Completion Signal

**What:** Unity's `AssetPostprocessor.OnPostprocessAllAssets` fires after every import batch completes. JarvisAssetPostprocessor hooks this callback and sends a notification to JarvisEditorBridge, which relays it as a JSON-RPC notification back to Python. This allows the agent's UnityTool to await asset import completion before proceeding to the next step that depends on the imported asset.

**Why this matters:** Asset imports (textures, models, audio) are async in Unity Editor. An agent that creates a material and immediately tries to assign a texture will fail if the texture hasn't finished importing. AssetPostprocessor notification solves this without polling.

```csharp
public class JarvisAssetPostprocessor : AssetPostprocessor
{
    static void OnPostprocessAllAssets(
        string[] imported, string[] deleted,
        string[] moved, string[] movedFrom)
    {
        JarvisEditorBridge.NotifyImportComplete(imported);
    }
}
```

---

## Data Flow

### Primary Flow: "Create a 3D platformer" Voice Command

```
[Voice/Text Input]
    ↓
[Existing VoiceRunCommand / RouteCommand]
    ↓
[New: AgentRunCommand dispatched to CommandBus]
    ↓
[AgentStateStore.create_task(goal, status=pending)]
    ↓
[TaskPlanner.plan(goal, tool_schemas)]
    → LLM call via ModelGateway (local Ollama first)
    → Returns: DAG of AgentStep objects
    ↓
[AgentStateStore.save_plan(task_id, plan)]
    ↓
[StepExecutor loop]
    ├─ For each step:
    │   ├─ Check ApprovalGate (create=auto, destroy=block)
    │   ├─ If blocked: emit approval_needed SSE event → wait
    │   ├─ tool = ToolRegistry.get(step.tool_name)
    │   ├─ result = tool.execute(step.params)
    │   ├─ AgentStateStore.checkpoint(task_id, step_index, result)
    │   ├─ emit step_done SSE event
    │   └─ pass result to ReflectionLoop.evaluate()
    │       ├─ If success: continue
    │       └─ If fail: replan remaining steps (max 5 retries)
    │           └─ If pattern match in KG: apply known fix first
    ↓
[Task complete: AgentStateStore.status = done]
[KG write: successful plan pattern saved for reuse]
```

### Unity Tool Call Flow (inside StepExecutor)

```
[UnityTool.execute(params)]
    ↓
[Build JSON-RPC 2.0 request: {jsonrpc, id, method, params}]
    ↓ WebSocket send (ws://localhost:8091/jarvis)
[JarvisEditorBridge.cs receives message]
    ↓
[ReflectionCommandDispatcher.Dispatch(method, args)]
    ├─ Cache lookup: method string → MethodInfo
    ├─ TypeCoercer: JSON values → C# parameter types
    ├─ method.Invoke(null, parameters)
    └─ Serialize result to JSON
    ↓ WebSocket send back
[UnityTool.execute receives response]
    ↓ Returns ToolResult(success, data, error)
```

### Progress Stream Flow

```
[Any StepExecutor event]
    ↓
[ProgressEventBus.emit(event_dict)]
    ↓ asyncio.Queue (bounded 256)
[SSE handler thread drains queue]
    ↓ HTTP SSE to all connected clients
    ├─ Desktop Widget (Tkinter polling loop reads /agent/stream)
    └─ JarvisPanel (Unity EditorWindow consuming /agent/stream via UnityWebRequest)
```

### Agent State Management Flow

```
[New task]        → INSERT agent_tasks row, status=pending
[Plan saved]      → UPDATE plan_json, status=planning
[Step N start]    → UPDATE step_index=N, status=running (BEFORE tool call)
[Step N done]     → UPDATE checkpoint=result_json, step_index=N+1
[Approval needed] → UPDATE status=blocked, approval_required=[step_id]
[Approval given]  → UPDATE status=running
[All steps done]  → UPDATE status=done
[Failure + retry] → UPDATE error_count++, step_index=last_good (rollback to checkpoint)
[Failure exhaust] → UPDATE status=failed (maps to existing missions.py exhausted state)
```

---

## Integration Points

### New vs Existing Components

| Category | Component | Status | Integration Notes |
|----------|-----------|--------|-------------------|
| CQRS | AgentRunCommand, AgentStatusCommand, AgentApproveCommand | NEW | Add to commands/agent_commands.py; register handlers in app.py |
| CQRS | Existing MissionRunCommand | MODIFIED | Agent tasks become a mission type — AgentRunCommand creates a mission, delegates to agent/ |
| Mobile API | /agent/run, /agent/status, /agent/approve, /agent/stream | NEW | New mobile_routes/agent.py module, registered in mobile_routes/__init__.py |
| Learning | KG write of successful agent patterns | MODIFIED | ReflectionLoop calls existing knowledge/ module — no changes to KG module itself |
| Daemon | Agent task polling in daemon_loop.py | MODIFIED | Add agent task state check to _run_periodic_subsystems; resume blocked tasks |
| Desktop Widget | Agent progress panel in Tkinter | NEW | Separate tab or panel; polls /agent/stream SSE |
| SQLite | agent_tasks table | NEW | Added via migration in existing _db_pragmas.py migration system |

### External Service Integration

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Unity Editor | WebSocket JSON-RPC ws://localhost:8091 | Unity plugin hosts server; Python agent is client |
| Blender | subprocess CLI (blender --background --python script.py) | BlenderTool wraps subprocess; no persistent connection |
| tripo.io | HTTP REST API | WebTool extended or dedicated TripoTool; async HTTP call |
| Shell | subprocess with timeout + allowlist | ShellTool uses existing subprocess patterns from ops/ |

### Internal Module Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| agent/ → gateway/ | Direct import — ModelGateway.complete() call | No changes to gateway; planner injects tool schemas into prompt text |
| agent/ → knowledge/ | Direct import — existing KG fact injection APIs | ReflectionLoop writes; TaskPlanner reads for known error patterns |
| agent/ → mobile_routes/ | ProgressEventBus singleton shared via import | Bus initialized at app startup, consumed in agent.py route |
| agent/ → learning/missions.py | Reuses VALID_TRANSITIONS and _check_transition | AgentStateStore validates transitions using existing FSM |
| UnityTool → JarvisEditorBridge.cs | WebSocket JSON-RPC — transport boundary | Full decoupling; Unity plugin can be updated independently |
| JarvisPanel → /agent/stream | HTTP SSE — transport boundary | Unity plugin polls Jarvis HTTP API for progress |

---

## Scaling Considerations

This is a single-user local system (Conner only). Scale concerns are performance and resource usage, not multi-tenancy.

| Concern | Current | Mitigation |
|---------|---------|------------|
| LLM call latency in planner | 2-10s per planning call | Ollama local first; cache plan if same goal repeated (hash in KG) |
| Unity WebSocket message queue | Grows if Unity is busy (compilation, import) | UnityTool implements timeout + retry with exponential backoff |
| SQLite write contention | Daemon loop + agent both write | WAL mode already set; agent uses dedicated table, not memory tables |
| Token budget for tool schemas | ~2000 tokens per planning call | Limit base registry to 10-12 tools; dynamically extend per task type |
| Tkinter SSE polling | Polling loop on background thread | 500ms poll interval is sufficient; widget thread is already backgrounded |
| Compilation loop | Agent may trigger recompile, block Unity | JarvisEditorBridge detects compilation state via EditorApplication.isCompiling; queues commands until complete |

---

## Anti-Patterns

### Anti-Pattern 1: HTTP REST for Unity Communication

**What people do:** Implement an HTTP server in Unity (using C# HttpListener) and send REST requests from Python.

**Why it's wrong:** Every tool call creates a new TCP connection with HTTP overhead. A 50-step plan generates 50 connection cycles. Latency compounds. More critically, Unity Editor's C# runs on the main thread — an HTTP server either blocks the main thread or requires complex async bridging. WebSocket maintains a single persistent connection and messages are dispatched asynchronously.

**Do this instead:** WebSocket server in Unity (websocket-sharp), persistent connection, JSON-RPC 2.0 message framing.

### Anti-Pattern 2: Calling Assembly.GetTypes() Per Request

**What people do:** Build the reflection method cache inside each JSON-RPC handler call.

**Why it's wrong:** Assembly.GetTypes() allocates substantial GC memory and takes ~200ms in UnityEditor assembly. Doing this per request makes every tool call slow and causes GC pressure in the Editor.

**Do this instead:** Build the cache once at bridge startup ([InitializeOnLoad] static constructor). Cache is a plain Dictionary<string, MethodInfo> — zero allocation per lookup.

### Anti-Pattern 3: Planning Inside Executor

**What people do:** Have the executor call the LLM to figure out the next step each time.

**Why it's wrong:** No checkpointing possible. Replanning after failure requires the full goal context. Executor loop becomes unbounded. Cost spirals if using cloud LLMs.

**Do this instead:** Planner produces full DAG upfront, persisted to SQLite. Executor is a dumb step runner. ReflectionLoop can trigger targeted replanning of remaining steps (not full replan) when a step fails.

### Anti-Pattern 4: Single Queue for Progress + Unity Commands

**What people do:** Use one asyncio.Queue for both agent-to-Unity commands and agent-to-widget progress events.

**Why it's wrong:** A slow widget consumer blocks Unity command delivery. Unity tool calls require fast round-trip; SSE progress events are fire-and-forget.

**Do this instead:** Two separate channels. Unity commands go over the dedicated WebSocket (synchronous request-response with coroutine await). Progress events go on the SSE queue (broadcast, non-blocking, lossy acceptable).

### Anti-Pattern 5: Blocking Daemon Loop with Agent Execution

**What people do:** Run the StepExecutor loop directly in `_run_periodic_subsystems` in daemon_loop.py.

**Why it's wrong:** Blocks the daemon's 60-second cycle. Daemon handles proactive intelligence, health checks, KG maintenance — none of that runs while agent is executing.

**Do this instead:** Agent execution runs in a dedicated ThreadPoolExecutor thread (same pattern as existing MissionRunCommand). Daemon loop only polls agent task status (lightweight check) and resumes blocked tasks.

---

## Build Order (Dependency Sequence)

The following order respects internal dependencies and minimizes integration risk:

1. **AgentStateStore + SQLite schema** — No dependencies. Can be built and tested in isolation. All other components depend on it.

2. **ToolRegistry + ToolSpec protocol** — No external dependencies. Define the interface contracts before implementations.

3. **ReflectionCommandDispatcher (C#)** — Unity-side only. Can be developed and unit-tested independently of Python. Builds the reflection cache, handles dispatch. Tests can call it directly with mock JSON-RPC requests.

4. **JarvisEditorBridge (C#) + WebSocket server** — Depends on ReflectionCommandDispatcher. Adds the WebSocket layer. At this point Unity side is complete.

5. **UnityTool (Python)** — Depends on JarvisEditorBridge being testable. Implements the WebSocket JSON-RPC client. Integration test: Python calls a simple Unity API (e.g., CreatePrimitive) and receives result.

6. **Core tool set** — ShellTool, FileTool, KGTool. No WebSocket dependency. Each wraps existing Jarvis subsystems.

7. **ProgressEventBus + mobile_routes/agent.py** — Depends on state store. Adds /agent/stream SSE endpoint. Can be tested with a mock event producer.

8. **ApprovalGate** — Depends on ProgressEventBus (emits approval_needed events) and state store. Simple logic, fast to build.

9. **TaskPlanner** — Depends on ToolRegistry (needs schemas), ModelGateway (existing), AgentStateStore (saves plan). Core LLM-calling component.

10. **StepExecutor + ReflectionLoop** — Depends on everything above. The orchestration layer. Build last to integrate all pieces.

11. **AgentRunCommand + app.py registration** — Wires agent subsystem into CQRS bus. One new command + handler.

12. **JarvisPanel (C#, EditorWindow)** — Depends on /agent/stream SSE endpoint. Progress UI. Can be built after step 7.

13. **JarvisAssetPostprocessor (C#)** — Depends on JarvisEditorBridge. Simple hook, built late as it's an enhancement.

14. **Widget integration** — Tkinter panel for agent progress. Build last as it depends on all backend components.

---

## Sources

- [mcp-unity by CoderGamester — WebSocket JSON-RPC architecture, port 8090, Unity C# WebSocket server pattern](https://github.com/CoderGamester/mcp-unity)
- [Unity-MCP by IvanMurzak — MCP over stdio/HTTP, 50+ tools, Roslyn C# execution, dynamic tool generation](https://github.com/IvanMurzak/Unity-MCP)
- [Unity MCP by CoplayDev — HTTP transport, localhost:8080 configuration](https://github.com/CoplayDev/unity-mcp)
- [Unity Official: Avoid C# reflection overhead — cache at startup, avoid per-frame calls](https://docs.unity3d.com/6/Documentation/Manual/performance-gc-avoid-reflection.html)
- [Unity Official: AssetPostprocessor — OnPostprocessAllAssets hook pattern](https://docs.unity3d.com/6000.1/Documentation/ScriptReference/AssetPostprocessor.html)
- [Unity Official: EditorWindow scripting API](https://docs.unity3d.com/ScriptReference/EditorWindow.html)
- [AI Agent State Checkpointing — three-layer checkpoint model (mission state, tool context, system config)](https://fast.io/resources/ai-agent-state-checkpointing/)
- [LangGraph state management — SqliteSaver checkpointing, per-step persistence pattern](https://markaicode.com/langgraph-production-agent/)
- [Designing Tool Architecture for AI Agents — base tools vs toolkits, registry pattern, allowlist safety](https://dev.to/kim_namhyun_e7535f3dc4c69/designing-a-tool-architecture-for-ai-agents-base-tools-toolkits-and-dynamic-routing-fdo)
- [ToolRegistry library — protocol-agnostic tool management, schema-first design](https://toolregistry.readthedocs.io/en/stable/)
- [SSE for agent progress streaming — FastAPI+asyncio.Queue pattern, backpressure via bounded queue](https://akanuragkumar.medium.com/streaming-ai-agents-responses-with-server-sent-events-sse-a-technical-case-study-f3ac855d0755)
- [Planner-Executor agentic framework — strict separation of strategic decomposition vs tactical execution](https://www.emergentmind.com/topics/planner-executor-agentic-framework)
- [Unity Python Scripting package — deprecated as of Unity 6.1, port 18861 requires Python 2.7](https://discussions.unity.com/t/why-is-the-python-scripting-package-support-ending/1546473)

---

*Architecture research for: Jarvis Unity Agent — autonomous game development agent*
*Researched: 2026-03-16*
