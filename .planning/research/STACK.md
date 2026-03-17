# Stack Research

**Domain:** Autonomous Unity game development agent — Jarvis v6.0
**Researched:** 2026-03-16
**Confidence:** MEDIUM-HIGH (most claims verified via official docs or official GitHub; tripo3d version verified via PyPI)

---

## What Already Exists — Do NOT Re-Add

These are working in the Jarvis Python engine and must be integrated with, not replaced:

| Capability | Existing Module | Notes |
|-----------|----------------|-------|
| LLM calls | `gateway/` ModelGateway | Ollama + Anthropic + Groq + Kimi. Use for all agent reasoning. |
| Knowledge graph | `knowledge_graph/` | NetworkX + SQLite. Use to store Unity API patterns, error fixes. |
| Memory / vector search | `memory/` | SQLite + FTS5 + sqlite-vec. Use for retrieving past solutions. |
| Shell execution | already in engine | `subprocess` used throughout. Wrap for Unity CLI calls. |
| Web fetch | `web_fetch/` pipeline | curl_cffi + httpx + urllib. Use for tripo.io HTTP calls. |
| CQRS command bus | `app.py` | 70+ commands. All new agent actions become commands here. |
| Async daemon loop | `daemon/` | asyncio. Agent task loop runs here, not in a new process. |
| Missions system | `missions/` | Existing autonomous missions. Agent tasks are new mission types. |
| Logging / observability | `ops/` | Use existing structured logging. Do not add a separate logger. |

---

## New Stack Additions Required

### 1. Unity Editor Automation Layer

#### Unity CLI invocation (Python side)

No new Python library needed. Use `asyncio.create_subprocess_exec` (already available in standard library) to call the Unity Editor executable in batch mode.

**Pattern:**

```bash
"C:/Program Files/Unity/Hub/Editor/6000.3.x/Editor/Unity.exe" \
  -batchmode -nographics -quit \
  -projectPath "C:/path/to/project" \
  -executeMethod JarvisBridge.RunCommand \
  -jarvisPayload "{\"action\":\"compile\"}"
```

Arguments are read inside C# via `System.Environment.GetCommandLineArgs()`.

**Why this approach:** Unity 6.3 LTS (internal version 6000.3.0f1, released December 2025, supported until December 2027) has mature `-batchmode -executeMethod` support. No extra Python package needed — `asyncio.create_subprocess_exec` handles process management, stdout/stderr capture, and timeout.

**Confidence:** HIGH — verified at `docs.unity3d.com/6000.3/Documentation/Manual/EditorCommandLineArguments.html`

#### Unity Editor WebSocket bridge (live session mode)

For long-lived interactive sessions (vs. one-shot batch calls), the C# plugin opens a WebSocket server inside the Unity Editor. The Python agent connects as a client.

| Technology | Version | Purpose | Why |
|-----------|---------|---------|-----|
| `websockets` | 14.x (current stable) | Python WebSocket client connecting to C# server | Pure asyncio, no extra event loop, fits existing daemon. Version 14+ has improved Windows compatibility. |

```bash
pip install websockets>=14.0
```

**Why websockets over alternatives:** The `websockets` library is asyncio-native, zero-dependency (no Twisted, no Tornado), and version 14.x runs correctly on Windows asyncio event loops without the selector workaround needed by older versions. The Unity C# side uses `System.Net.WebSockets.HttpListenerWebSocketContext` (stdlib in .NET 4.x, available in Unity 6.3).

**Confidence:** MEDIUM — websockets library official; Unity stdlib WebSocket support inferred from Unity MCP reference implementation (github.com/IvanMurzak/Unity-MCP uses this transport).

---

### 2. Unity Editor C# Plugin (JarvisEditorBridge)

This is custom C# code written as part of v6.0, not a third-party package. The reference architecture is Unity-MCP by IvanMurzak (github.com/IvanMurzak/Unity-MCP), which proves the pattern works.

**Architecture decision:** Build a custom plugin rather than adopting Unity-MCP wholesale. Reason: Unity-MCP is MCP-protocol-oriented (designed for Claude Desktop / Cursor), adding protocol overhead that Jarvis does not need. Jarvis needs raw JSON-RPC over WebSocket with direct method dispatch — simpler and faster for an autonomous loop.

**C# components to build:**

| Component | Responsibility | Transport |
|-----------|---------------|-----------|
| `JarvisEditorBridge.cs` | Entry point, registers `[InitializeOnLoad]`, starts WS server | N/A |
| `WebSocketServer.cs` | Listens on localhost:9876, accepts one connection | TCP/WebSocket |
| `CommandDispatcher.cs` | Deserializes JSON-RPC, routes to tool handlers | N/A |
| `ReflectionTool.cs` | `Assembly.GetTypes()` scan, `MethodInfo.Invoke()` for dynamic dispatch | N/A |
| `CompileTool.cs` | `CompilationPipeline.RequestScriptCompilation()` + error collection | N/A |
| `PlayModeTool.cs` | `EditorApplication.EnterPlaymode()` / `ExitPlaymode()` + log capture | N/A |
| `AssetTool.cs` | `AssetDatabase.ImportAsset()`, `AssetDatabase.Refresh()` | N/A |
| `SceneTool.cs` | `EditorSceneManager` scene open/save/query | N/A |

**Unity package dependencies (all built-in to Unity 6.3):**
- `UnityEditor` namespace — Editor scripting
- `UnityEditor.Compilation` — compile pipeline access
- `System.Reflection` — dynamic method dispatch
- `System.Net.WebSockets` — WebSocket server
- `Newtonsoft.Json` (com.unity.nuget.newtonsoft-json) — JSON serialization

**Confidence:** HIGH for pattern; MEDIUM for specific C# class names (those are implementation choices).

---

### 3. Blender Python Scripting

Blender is invoked headlessly from Python via subprocess. No Blender-specific Python package is needed on the Jarvis side — scripts are Python files passed to Blender's embedded interpreter.

**Blender version:** 4.3.x (current stable as of early 2026, Python 3.11 embedded). Blender 4.2 is the current LTS. **Use 4.3** for latest bpy API improvements (Grease Pencil rewrite, `bpy.app.python_args`).

**Invocation pattern from Jarvis Python:**

```python
import asyncio

async def run_blender_script(script_path: str, args: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        "--background",
        "--python", script_path,
        "--", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode()
```

Scripts use `bpy` (Blender's embedded module) and `sys.argv` for arguments. No pip install needed on the Jarvis side.

**Optional helper library for Jarvis side only:**

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `blenderless` | 0.3.x | Simplified headless render calls from Python | Only if batch rendering is needed; skip if agent writes raw bpy scripts |

**Confidence:** HIGH — Blender CLI flags (`-b`, `--python`, `--`) are stable across all 4.x versions. Official docs: `docs.blender.org/api/current/`.

---

### 4. tripo.io API — AI 3D Model Generation

**Official Python SDK:**

| Technology | Version | Purpose | Why |
|-----------|---------|---------|-----|
| `tripo3d` | 0.3.12 | Text-to-3D and image-to-3D model generation via Tripo API | Official SDK from VAST-AI-Research; handles auth, async polling, download automatically |

```bash
pip install tripo3d==0.3.12
```

**Key methods:**

```python
from tripo3d import TripoClient

async with TripoClient(api_key=os.environ["TRIPO_API_KEY"]) as client:
    task = await client.text_to_model("a low-poly medieval sword")
    result = await client.wait_for_task(task.task_id)
    await client.download_task_models(result, output_dir="./assets")
```

**Output formats:** GLB, GLTF, FBX, OBJ, STL, USD, 3MF. Use **FBX** for Unity import (best material/rig preservation) or **GLB** with Unity's GLTFast package.

**Integration point:** Wrap as a `TripoTool` in the agent's pluggable tool layer. The API key goes in Jarvis's existing `config.json` / environment, following the same pattern as Anthropic/Groq keys.

**Confidence:** HIGH — version 0.3.12 verified on PyPI (March 4, 2026 release). Official GitHub: `github.com/VAST-AI-Research/tripo-python-sdk`.

---

### 5. Agentic Task Planning / Execution Loop

**Decision: Build custom, do not adopt LangGraph or CrewAI.**

Rationale:
- Jarvis already has a missions system, CQRS command bus, ModelGateway, and a daemon loop. A framework like LangGraph would duplicate all of these with heavy new dependencies (langchain-core, pydantic v2 conflicts, etc.).
- The agent loop for game development is a deterministic state machine, not an open-ended chat agent. The loop is: Plan → Execute tool → Observe result → Decide next step → (retry or advance). This is 50-100 lines of Python, not a framework problem.
- LangGraph's graph-state model adds overhead with no benefit when the state is already managed by Jarvis's existing SQLite + mission system.

**Pattern: ReAct loop as a new mission type**

```python
# Pseudocode — implemented in engine/src/jarvis_engine/agent/
class AgentLoop:
    async def run(self, goal: str, max_steps: int = 50) -> AgentResult:
        plan = await self.planner.create_plan(goal)          # ModelGateway call
        for step in itertools.count():
            action = await self.planner.next_action(plan, history)
            if action.type == "complete": break
            result = await self.executor.execute(action)     # dispatch to tool
            history.append((action, result))
            plan = await self.planner.reflect(plan, result)  # reflection step
            if step >= max_steps: raise AgentTimeoutError()
```

**New Python modules needed (zero new pip packages):**

| Module | Purpose | Dependencies |
|--------|---------|-------------|
| `agent/planner.py` | Decomposes goal into steps using ModelGateway | Existing ModelGateway |
| `agent/executor.py` | Dispatches tool calls, captures results | asyncio subprocess, websockets |
| `agent/tool_registry.py` | Dynamic tool registration dict | stdlib only |
| `agent/reflection.py` | Analyzes step results, decides retry/advance | ModelGateway |
| `agent/state.py` | SQLite-backed task state, progress persistence | Existing SQLite layer |

**Approval gate:** The executor checks `action.requires_approval` before destructive actions (file delete, spend money, asset overwrite). Approval request goes to existing widget notification system.

**Confidence:** HIGH — custom loop pattern verified by multiple sources; avoids framework lock-in.

---

## Complete New pip Dependencies

```bash
# Add to requirements.txt or pyproject.toml
pip install "websockets>=14.0"
pip install "tripo3d==0.3.12"

# Optional, only if batch Blender rendering is needed
pip install "blenderless>=0.3"
```

That is the complete list. Everything else reuses existing Jarvis infrastructure.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Agent framework | Custom ReAct loop | LangGraph | Duplicates existing Jarvis missions/CQRS/ModelGateway; adds 15+ transitive deps; pydantic v2 conflicts likely |
| Agent framework | Custom ReAct loop | CrewAI | Multi-agent focus overkill for single-agent game dev loop; same dep bloat problem |
| Agent framework | Custom ReAct loop | OpenAI Agents SDK | Ties agent design to OpenAI tool-call format; Jarvis uses multiple LLMs |
| Unity bridge | Custom C# + WebSocket | Unity-MCP (IvanMurzak) | MCP protocol overhead unnecessary; designed for IDE integration not autonomous loop; we need tighter control |
| Unity bridge | Custom C# + WebSocket | Unity Python Scripting package (`com.unity.scripting.python`) | Editor-only, Python 3.10 locked, runs inside Unity process — can't call Jarvis code from it |
| Blender automation | subprocess + bpy scripts | `bpy` pip package | bpy pip package is Python-version-locked (must match Blender's embedded Python exactly); subprocess is simpler and decoupled |
| 3D generation | tripo3d SDK | Unofficial `tripo-python` | Official SDK has async support, maintained by VAST-AI-Research; unofficial is unmaintained |
| WebSocket | `websockets` | `aiohttp` | aiohttp adds HTTP server overhead not needed here; websockets is minimal and asyncio-native |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `com.unity.scripting.python` Unity package | Locks Python to 3.10.6, runs inside Unity process (can't reach Jarvis), Editor-only | subprocess + `-executeMethod` CLI for batch; WebSocket bridge for live session |
| LangGraph / LangChain | 15+ transitive dependencies, pydantic v2 conflicts with existing Jarvis code, duplicates existing systems | Custom ReAct loop in `agent/` |
| `bpy` pip package | Must exactly match Blender's embedded Python version (3.11 for Blender 4.3); breaks if Blender updates | Call Blender executable via subprocess, pass script files with `-P` flag |
| Unity-MCP as-is | MCP protocol overhead, designed for IDE use, not autonomous loop; .unitypackage install friction | Reference its architecture, write a simpler custom C# bridge |
| Polling sleep loops for tripo3d | Blocks daemon thread | `await client.wait_for_task()` — SDK handles async polling internally |
| New SQLite connection in agent | Jarvis already has a connection pool | Use existing `memory/` and `knowledge_graph/` modules |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|----------------|-------|
| `tripo3d==0.3.12` | Python 3.10+ | Uses `httpx` internally; compatible with Jarvis's existing httpx usage |
| `websockets>=14.0` | Python 3.11+, Windows asyncio | 14.x fixes Windows selector event loop issues present in 13.x |
| Unity 6.3 (6000.3.0f1) | .NET Standard 2.1, C# 9 | LTS until December 2027 |
| Blender 4.3 | Python 3.11 (embedded) | Scripts must not import non-stdlib packages unless installed into Blender's pip |

---

## Integration Map

```
Jarvis Daemon (asyncio)
  └── AgentLoop (new: agent/)
        ├── TaskPlanner → ModelGateway (existing)
        ├── ReflectionLoop → ModelGateway (existing)
        ├── KnowledgeGraph ← stores patterns/fixes (existing)
        └── ToolRegistry
              ├── UnityTool
              │     ├── BatchMode: asyncio.create_subprocess_exec → Unity.exe -batchmode
              │     └── LiveSession: websockets client → C# WebSocketServer in Editor
              ├── BlenderTool
              │     └── asyncio.create_subprocess_exec → blender.exe --background -P script.py
              ├── TripoTool
              │     └── tripo3d SDK → tripo3d.ai API
              ├── ShellTool (reuse existing subprocess pattern)
              ├── FileTool (reuse existing file ops)
              └── WebTool (reuse existing web_fetch)
```

---

## Sources

- Unity 6.3 CLI docs — `docs.unity3d.com/6000.3/Documentation/Manual/EditorCommandLineArguments.html` — HIGH confidence
- Unity 6.3 release announcement — `unity.com/blog/unity-6-3-lts-is-now-available` — HIGH confidence (version 6000.3.0f1, Dec 2025)
- tripo3d PyPI — `pypi.org/project/tripo3d` — HIGH confidence (v0.3.12, March 4, 2026)
- tripo3d official SDK — `github.com/VAST-AI-Research/tripo-python-sdk` — HIGH confidence
- Tripo output format guide — `tripo3d.ai/blog/choose-from-obj-fbx-glb-formats` — MEDIUM confidence
- Blender Python API — `docs.blender.org/api/current/` — HIGH confidence
- Blender 4.3 Python API changes — `developer.blender.org/docs/release_notes/4.3/python_api/` — HIGH confidence
- websockets library — `websockets.readthedocs.io/en/stable/` — HIGH confidence (v14.x current)
- Unity-MCP architecture reference — `github.com/IvanMurzak/Unity-MCP` — MEDIUM confidence (used as pattern reference only)
- LangGraph 2026 landscape — `aimultiple.com/agentic-frameworks` — LOW confidence (WebSearch only; used to confirm framework alternatives, not for implementation)

---

*Stack research for: Jarvis v6.0 Unity Agent — new capabilities only*
*Researched: 2026-03-16*
