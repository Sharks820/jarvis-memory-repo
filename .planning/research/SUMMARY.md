# Project Research Summary

**Project:** Jarvis v6.0 — Autonomous Unity Game Development Agent
**Domain:** Agentic code generation and Unity Editor automation integrated into existing Jarvis AI assistant
**Researched:** 2026-03-16
**Confidence:** MEDIUM-HIGH

## Executive Summary

The Jarvis Unity Agent is an autonomous coding agent that takes natural language game development goals, decomposes them into ordered steps, writes Unity C# code, compiles it in a live Unity Editor, fixes errors automatically, and can generate 3D assets via tripo.io and Blender. The recommended architecture is a custom ReAct-style agent loop (plan → execute → reflect → replan) integrated into Jarvis's existing daemon, CQRS bus, ModelGateway, and knowledge graph — rather than adopting a framework like LangGraph or CrewAI, which would duplicate all of these systems and introduce dependency conflicts. The Unity-facing communication layer uses a custom C# WebSocket server in the Unity Editor and a Python WebSocket client, following the pattern proven by Unity-MCP and mcp-unity open-source implementations.

The recommended approach builds in strict phases: first establish the infrastructure (VRAM coordination, process management, Unity API knowledge seeding) before touching any code generation or bridge work, because the most common failure modes — hallucinating Unity 6.3 APIs and VRAM OOM crashes — strike immediately and silently on the first real use. The Unity Editor Bridge (C# WebSocket plugin) is the load-bearing feature and must be complete and stable before any higher-level features can be validated. Code generation quality depends critically on seeding the Jarvis knowledge graph with Unity 6.3 API reference before any agent code generation is attempted — this is a prerequisite, not a nice-to-have.

The key risks are well-understood and addressable. VRAM exhaustion from concurrent Ollama inference and Unity play-mode rendering on the RTX 4060 Ti 8GB requires a hard GPU coordinator mutex. Domain reload deadlocks require a ready-state handshake protocol built into the bridge from day one. Hallucination cascades on non-existent Unity 6.3 APIs require pre-compilation static analysis combined with KG-seeded API validation. Agent cost/time explosion requires task-level token budgets and loop detection, not just per-step retry caps. All three of these must be addressed in Phase 1 and Phase 2 — deferring any of them creates compounding failure modes.

---

## Key Findings

### Recommended Stack

The new capabilities require only two new pip packages added to the existing Jarvis engine: `websockets>=14.0` (asyncio-native WebSocket client for Python-to-Unity communication, Windows-compatible in v14.x) and `tripo3d==0.3.12` (official VAST-AI-Research SDK for text/image-to-3D generation, verified on PyPI March 4, 2026). Everything else reuses existing Jarvis infrastructure: `asyncio.create_subprocess_exec` for both Unity batch-mode and Blender headless invocation, ModelGateway for all LLM calls, the knowledge graph for Unity API patterns and error-fix storage, and the missions system as the agent task container.

The Unity Editor bridge is a custom C# plugin (not Unity-MCP adopted wholesale) built on Unity 6.3 LTS. Unity's official Python Scripting package is explicitly ruled out — it locks to Python 3.10.6 and runs inside the Unity process, making it unreachable from Jarvis. Blender 4.3 is invoked as a subprocess with Python scripts passed via `--background --python` flags; the `bpy` pip package is avoided because it must exactly match Blender's embedded Python version and breaks on Blender updates. No agent framework (LangGraph, CrewAI, OpenAI Agents SDK) is used — the agent loop is a custom 50-100 line ReAct implementation that builds on Jarvis's existing CQRS and missions systems.

**Core technologies:**
- `websockets>=14.0`: Python WebSocket client for persistent agent-to-Unity communication — asyncio-native, Windows-compatible, zero extra dependencies
- `tripo3d==0.3.12`: Official SDK for text-to-3D and image-to-3D generation — handles auth, async polling, and GLB/FBX download automatically
- Unity 6.3 LTS (6000.3.0f1): Target Unity version, LTS until December 2027 — mature `-batchmode -executeMethod` and WebSocket support
- `asyncio.create_subprocess_exec`: Unity batch-mode and Blender headless invocation — stdlib, no new dependency
- Custom C# WebSocket server (JarvisEditorBridge): Bridge between Python agent and live Unity Editor — JSON-RPC 2.0 over WebSocket on port 8091
- Custom ReAct agent loop in `agent/` subpackage: Plan→Execute→Reflect loop — reuses ModelGateway, missions FSM, and KG storage

### Expected Features

**Must have (table stakes — v1 core agent loop):**
- Multi-step task planner (ReAct + plan-and-execute hybrid) — without this it is a one-shot code generator, not an agent
- Unity Editor Bridge (C# WebSocket plugin) — required for any live Unity project interaction; every other Unity feature depends on it
- C# code generation with Unity-domain prompting — primary value delivery mechanism
- Compile-trigger and error capture via `AssetDatabase.Refresh()` and console log polling — closes the autonomous loop
- Error-fix retry loop with hard cap (5 retries, error fingerprinting) — makes agent autonomous rather than requiring human re-prompting
- Scene manipulation tools (create scene, add GameObject, assign component, set transform) — needed for any real game task beyond pure scripting
- Approval gate for destructive and credit-spending actions — non-negotiable safety gate per project spec
- Progress streaming to Jarvis widget — user must see what the agent is doing in real time

**Should have (v1.x — asset pipeline, after core loop is green):**
- Unity 6.3 KG seeding — reduces hallucination-driven compile errors; triggers when first-pass code quality is poor
- tripo.io 3D asset generation — text/image to GLB/FBX dropped into Unity Assets/; triggers when user requests asset creation
- Blender headless post-processing pipeline — mesh optimization after tripo.io generation; triggers when raw tripo.io meshes need cleanup
- Learn-as-you-go pattern accumulation — stores successful code patterns and error fixes in KG; triggers after 10+ successful tasks
- Real-time Unity Editor panel (EditorWindow) — shows agent task queue inside Unity; triggers when agent is running tasks regularly

**Defer (v2+):**
- Smart mobile approval UX — push notifications to Android for approve/reject; requires Android app changes, defer until agent is battle-tested
- Dynamic tool registry — runtime tool registration without restart; initial tool set is fixed; add when tool count exceeds ~15
- Voice task intake integration — Jarvis STT exists; wiring Unity agent events into voice narration is polish, not MVP
- Play mode automated behavioral testing — complex main-thread synchronization; start with console log observation

**Anti-features (do not build):**
- Fully autonomous "ship to store" publishing — legal, compliance, and licensing review require human judgment
- Full project generation from scratch in one shot — LLMs cannot hold full project context in one pass; use phased generation
- Automatic Package Manager dependency resolution — version conflicts are hard to unwind automatically; use approval gate
- Real-time Play mode AI input takeover — race conditions with Unity main thread make this unreliable

### Architecture Approach

The agent subsystem is a new `agent/` subpackage inside the existing Jarvis engine, structured to match existing conventions (voice/, stt/, gateway/, learning/). It consists of five Python components (TaskPlanner, StepExecutor, ReflectionLoop, ToolRegistry, AgentStateStore) connected by an SSE-based ProgressEventBus, and two transport layers: WebSocket JSON-RPC to Unity (port 8091) and subprocess CLI to Blender. All agent tasks are stored in a new `agent_tasks` SQLite table (via the existing migration system) and reuse the existing missions FSM for state transitions. The Unity Editor hosts a C# WebSocket server; Python is the client. Progress events flow over a separate SSE channel to both the Tkinter widget and the Unity EditorWindow panel.

**Major components:**
1. **TaskPlanner** (`agent/planner.py`) — Decomposes natural language goal into a DAG of ordered steps using ModelGateway; injects tool schemas into the LLM prompt; persists plan to AgentStateStore before execution begins
2. **StepExecutor + ReflectionLoop** (`agent/executor.py`, `agent/reflection.py`) — Executes one step at a time, evaluates success/failure, replans remaining steps on failure, caps retries, writes error patterns to KG; these two must be built last (depend on everything else)
3. **JarvisEditorBridge + ReflectionCommandDispatcher** (C#, `Assets/Editor/Jarvis/`) — WebSocket server in Unity Editor; builds reflection cache of all UnityEditor/UnityEngine public static methods once at startup; dispatches JSON-RPC calls to Unity API; sends ready-state heartbeat after every domain reload
4. **ToolRegistry** (`agent/tool_registry.py`) — Dataclass-based tool registry keyed by name; each tool has a JSON Schema descriptor used to populate the planner's LLM prompt; approval flag gates destructive tools before execution
5. **AgentStateStore** (`agent/state_store.py`) — SQLite `agent_tasks` table with full plan JSON, step index, checkpoint blob, approval flags; enables task resumption from last successful checkpoint after crash or bridge disconnection
6. **ProgressEventBus** (`agent/progress_bus.py`) — Bounded asyncio.Queue (256 items) feeding an SSE `/agent/stream` HTTP endpoint; fan-out to multiple consumers (widget + Unity panel); Unity commands travel on the separate WebSocket channel to avoid backpressure interference

### Critical Pitfalls

1. **Hallucination cascade on Unity 6.3 APIs** — qwen3.5 has strong Unity 2020-2022 patterns but weak Unity 6.x knowledge; agent doubles down on non-existent APIs across retries. Avoid by seeding KG with Unity 6.3 API reference before any code generation, adding a pre-compilation name validation step in ReflectionLoop, and treating `CS0117`/`CS0619` errors as "wrong API" signals rather than fix targets.

2. **Domain reload deadlock** — Agent sends JSON-RPC commands while Unity C# AppDomain is torn down mid-reload; commands silently drop or hang. Avoid by building a ready-state handshake into JarvisEditorBridge from day one: bridge sends `{"status":"ready"}` after every reload; Python agent enters `WAITING_FOR_BRIDGE` state after any `.cs` file write and refuses further commands until heartbeat received.

3. **VRAM exhaustion (Ollama + Unity GPU contention)** — qwen3.5 Q4_K_M uses 5.5-6.5GB VRAM on RTX 4060 Ti 8GB; Unity play-mode rendering uses 1-3GB; combined demand exceeds 8GB and causes OOM. Avoid with a `GPU_COORDINATOR` mutex that makes `generation_active` and `unity_playmode_active` mutually exclusive, hard cap `OLLAMA_NUM_CTX=4096`, and Unity quality settings capped during agent-driven play-mode tests.

4. **Unsafe code execution via agent-generated C#** — Agent-generated scripts with `System.IO.File.Delete`, recursive path operations, or `Process.Start()` execute with full user permissions inside the Editor process, bypassing Jarvis approval gates. Avoid with a static analysis pass on all generated C# before compilation, a path jail restricting writes to `Assets/JarvisGenerated/`, and classifying "writes compilable file to disk" as a destructive operation requiring approval.

5. **Agent loop cost/time explosion** — Per-step retry cap (5 retries) does not prevent 50+ inference calls on a 10-step task; loop detection not built in by default. Avoid with a task-level token budget (50,000 tokens default), loop detection via hashing (last_error, code_diff), and "3 consecutive failed steps = escalate" rule before retries are exhausted.

6. **Orphaned Unity process tree holding project lock** — `subprocess.terminate()` on Windows kills only the parent `Unity.exe`; child shader compiler and import worker processes remain and hold the project lock. Avoid by always using `taskkill /f /t /pid` for Unity process trees and storing the PID in a lockfile (replicating the existing Ollama tracking pattern).

---

## Implications for Roadmap

Based on combined research, the following phase structure is recommended. The build order is driven by two constraints: (1) infrastructure blockers that cause silent failures if deferred, and (2) the Unity Editor Bridge being the load-bearing dependency for all Unity-facing features.

### Phase 1: Infrastructure and Knowledge Foundations

**Rationale:** Three pitfalls (VRAM OOM, orphaned processes, hallucinated Unity 6.3 APIs) strike immediately and silently on first use. All three must be addressed before any code generation or bridge work begins. This phase has no external dependencies — it can be built and tested in isolation before Unity or Blender are involved.

**Delivers:**
- GPU coordinator mutex (`generation_active` / `unity_playmode_active` mutually exclusive)
- Unity process manager with PID lockfile and `taskkill /f /t` tree kill (replicates `_ollama_started_by_widget` pattern)
- Unity 6.3 API dictionary seeded into the Jarvis knowledge graph (breaking changes, removed namespaces, correct method signatures)
- AgentStateStore + SQLite `agent_tasks` table (zero external dependencies — foundation for all other components)
- ToolRegistry + ToolSpec protocol definition (interface contracts before implementations)
- CQRS command stubs: AgentRunCommand, AgentStatusCommand, AgentApproveCommand registered in app.py

**Features addressed:** Approval gate foundation, progress streaming scaffolding
**Pitfalls avoided:** VRAM exhaustion, orphaned Unity processes, hallucinated Unity 6.3 APIs
**Research flag:** Standard patterns — skip research phase

---

### Phase 2: Unity Editor Bridge

**Rationale:** This is the load-bearing feature. Every Unity-facing capability depends on a stable, tested bridge. The ready-state handshake (domain reload protocol), path jail, and pre-compilation static analysis must be designed in from the start — adding them afterward requires bridge rewrite.

**Delivers:**
- JarvisEditorBridge.cs — `[InitializeOnLoad]` WebSocket server on port 8091; ready-state heartbeat after every domain reload
- ReflectionCommandDispatcher.cs — reflection cache built once at startup; JSON-RPC 2.0 dispatch to Unity API
- JarvisAssetPostprocessor.cs — `OnPostprocessAllAssets` hook for async import completion signaling
- UnityTool (Python) — WebSocket JSON-RPC client; `WAITING_FOR_BRIDGE` state machine; timeout + exponential backoff
- Path jail enforcement — all agent file writes restricted to `Assets/JarvisGenerated/`; path traversal rejected in Python before write
- Pre-compilation static analysis — blocks `System.IO.File.Delete`, `Process.Start`, recursive path ops, `Assembly.LoadFrom` in generated C#
- Domain reload policy documented and enforced (domain reload enabled during all agent development phases)

**Features addressed:** Unity Editor Bridge (C# plugin), scene manipulation tools foundation, compile-trigger and error capture
**Stack elements used:** websockets>=14.0, Unity 6.3 LTS, `asyncio.create_subprocess_exec`
**Pitfalls avoided:** Domain reload deadlock, unsafe code execution via agent-generated C#, IL2CPP reflection incompatibility (Editor-only constraint enforced at structure level)
**Research flag:** Needs research-phase — WebSocket C# server setup on Unity 6.3 has moderate implementation complexity; domain reload handshake protocol needs precise sequencing

---

### Phase 3: Core Agent Loop

**Rationale:** With the bridge stable and the knowledge foundation in place, the full agent loop can be built. TaskPlanner and ReflectionLoop are the last components to build because they depend on everything else. Hard limits and loop detection must be designed into the loop architecture here, not added later.

**Delivers:**
- TaskPlanner — goal decomposition into DAG of AgentStep objects via ModelGateway; tool schema injection into LLM system prompt
- StepExecutor — single-step execution with ApprovalGate check before destructive tools; checkpoint write before execution; progress event emission
- ReflectionLoop — success/failure evaluation; targeted replan of remaining steps; 5-retry cap per step; task-level token budget (50,000 tokens); loop detection via error+code hash; "3 consecutive failed steps = escalate" rule; error pattern write to KG
- ApprovalGate — create=auto, destroy/spend=block; emits approval_needed SSE event and waits; integrates with widget notification
- ProgressEventBus + `/agent/stream` SSE endpoint — asyncio.Queue fan-out to widget and Unity panel
- Core tool set: ShellTool (sandboxed subprocess), FileTool (path-jailed), KGTool (wraps existing knowledge/ module)
- End-to-end test: "create a rotating cube" task completing autonomously

**Features addressed:** Multi-step task planner, error-fix retry loop, approval gate, progress streaming to widget
**Pitfalls avoided:** Agent loop explosion (task-level budget + loop detection), agent blocking daemon loop (ThreadPoolExecutor pattern)
**Research flag:** Standard patterns — ReAct loop and plan-and-execute hybrid are well-documented; build order from ARCHITECTURE.md is authoritative

---

### Phase 4: C# Code Generation Quality

**Rationale:** The core loop from Phase 3 can execute tools, but code generation quality depends on Unity-domain context that must be injected into the LLM. This phase adds the Unity-specific prompting layer, scene manipulation tools, and the full compile-test-fix cycle with KG-backed error pattern lookup.

**Delivers:**
- Unity-domain system prompt template with Unity 6.3 API constraints, MonoBehaviour lifecycle patterns, and known breaking changes
- API validation pre-compilation pass in ReflectionLoop (checks generated type/method names against KG before invoking AssetDatabase.Refresh)
- Scene manipulation tools: CreateGameObject, AddComponent, SetTransform, AddAssetToScene, CreatePrefab, SaveScene (via JarvisEditorBridge)
- Play mode entry/exit with GPU coordinator interlock (enter only when generation_active=false)
- Compile error → KG lookup flow: CS0117/CS0619 triggers KG query for Unity 6.3 alternative before blind retry
- JarvisPanel.cs EditorWindow — progress UI inside Unity showing agent step log and approve/reject buttons

**Features addressed:** C# code generation (Unity-domain), scene manipulation, play mode entry/exit, real-time Unity Editor panel
**Pitfalls avoided:** Hallucination cascade (API validation layer), static field persistence across play-mode (domain reload policy + template reset method)
**Research flag:** Needs research-phase — Unity 6.3 breaking change catalog needs structured extraction for KG seeding; scene manipulation API surface needs enumeration

---

### Phase 5: Asset Pipeline

**Rationale:** Once the core scripting loop is reliable, add the 3D asset generation and Blender post-processing pipeline. This phase is independent of the core loop — it adds new tools to ToolRegistry without modifying existing components.

**Delivers:**
- TripoTool — wraps tripo3d SDK; text-to-3D and image-to-3D; FBX output for Unity; approval gate on every API call (credit spend)
- BlenderTool — headless Blender 4.3 subprocess; mesh optimization, LOD generation, normal recalculation, FBX export for game-ready assets
- Blender bpy scripts library under `agent/tools/blender_scripts/` — parameterized scripts for common post-processing operations
- Asset import coordination — `AssetDatabase.StartAssetEditing()` / `StopAssetEditing()` batching to avoid per-asset reimport overhead
- Asset manifest output to Jarvis memory after each task completion (naming convention: `[TaskContext]_[ComponentType]`)

**Features addressed:** tripo.io 3D asset generation, Blender headless post-processing pipeline
**Stack elements used:** tripo3d==0.3.12, Blender 4.3, `asyncio.create_subprocess_exec`
**Pitfalls avoided:** Unsafe file deletion via agent-generated C# (pre-compilation pass already in place from Phase 2)
**Research flag:** Needs research-phase — Blender bpy API for game-ready mesh export needs task-specific enumeration; tripo.io API credit cost modeling needed

---

### Phase 6: Learn-as-You-Go and Polish

**Rationale:** After the agent has run 10+ tasks, there is sufficient signal to seed the learn-as-you-go accumulation. This phase also adds voice intake integration and addresses UX pitfalls identified in research.

**Delivers:**
- Learn-as-you-go pattern accumulation — successful code snippets, error-fix pairs, and plan structures stored in KG with context tags; future tasks query KG before first LLM call
- Cancellation token — agent checks cancel flag between every step; clean cancellation without corrupting in-progress file writes
- Voice task intake wired to agent planner — existing Jarvis STT pipeline routes to AgentRunCommand
- Failure message enrichment — first 3 compiler errors included verbatim in all failure notifications
- Task summary on completion — file manifest, step count, token usage reported to user
- KV cache trim during reflection loop — prior retry history summarized before next attempt; hard cap OLLAMA_NUM_CTX=4096 enforced per-task

**Features addressed:** Learn-as-you-go pattern accumulation, voice task intake, UX pitfalls (silent failures, opaque output, generic error messages)
**Pitfalls avoided:** KV cache growth causing inference slowdown (context trim strategy), log flooding (rate-limited structured logging)
**Research flag:** Standard patterns — KG write patterns and voice wiring follow existing Jarvis conventions

---

### Phase Ordering Rationale

- Infrastructure (Phase 1) must precede everything because three of the eight critical pitfalls are immediate and silent — VRAM OOM can crash the system on the first combined Ollama+Unity test run.
- The Unity Editor Bridge (Phase 2) is the single load-bearing dependency. Zero Unity-facing features can be validated before it exists and is stable. The ready-state handshake and path jail must be designed in, not retrofitted.
- The Core Agent Loop (Phase 3) is built last among the foundational components because it depends on TaskPlanner, ToolRegistry, StepExecutor, ReflectionLoop, and the bridge all existing first — per the dependency sequence in ARCHITECTURE.md.
- Code generation quality (Phase 4) is separated from the core loop because it requires Unity-domain knowledge that flows from Phase 1 KG seeding and the API validation pass. Building it before the loop is stable wastes effort.
- Asset pipeline (Phase 5) is intentionally isolated — it adds new tools without modifying core loop components. Deferring it until the scripting loop is reliable avoids debugging two complex subsystems simultaneously.
- Learn-as-you-go (Phase 6) requires a body of successful tasks to learn from, making it correctly last.

### Research Flags

Phases needing `/gsd:research-phase` during planning:
- **Phase 2 (Unity Editor Bridge):** WebSocket server setup in Unity 6.3 C# has nuance; domain reload handshake sequencing is critical and sparsely documented outside the Unity-MCP source code
- **Phase 4 (C# Code Generation Quality):** Unity 6.3 breaking changes catalog needs structured extraction; scene manipulation API enumeration needed for tool schema definitions
- **Phase 5 (Asset Pipeline):** Blender bpy mesh export API for game-ready FBX needs enumeration; tripo.io credit cost model and rate limits need documentation before implementing approval gate thresholds

Phases with standard patterns (skip research-phase):
- **Phase 1 (Infrastructure):** Process management, SQLite schema, CQRS command registration all follow existing Jarvis patterns
- **Phase 3 (Core Agent Loop):** ReAct + plan-and-execute is thoroughly documented; build order from ARCHITECTURE.md is authoritative
- **Phase 6 (Learn-as-You-Go and Polish):** KG write patterns and voice wiring are direct extensions of existing Jarvis conventions

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Two new pip packages verified on PyPI; Unity 6.3 LTS CLI flags verified in official docs; Blender 4.3 subprocess pattern stable across 4.x versions; custom agent loop pattern verified by multiple independent sources |
| Features | MEDIUM-HIGH | Agent loop patterns HIGH (2026 industry consensus); Unity-specific automation MEDIUM (inferred from mcp-unity/Unity-MCP source code, not official Unity documentation on autonomous agents); tripo.io API MEDIUM (verified but 3D asset import quality into Unity depends on mesh topology) |
| Architecture | HIGH | WebSocket JSON-RPC pattern verified by three open-source Unity MCP implementations converging on the same approach; agent state checkpointing pattern well-documented; SSE streaming pattern established; reflection dispatch anti-patterns verified against Unity official docs |
| Pitfalls | HIGH for critical pitfalls; MEDIUM for integration-specific | VRAM budget verified against RTX 4060 Ti 8GB specs and Ollama model documentation; domain reload deadlock verified against Unity Discussions reports; hallucination cascade on deprecated APIs verified against ICSE 2025 research; CVE-2025-59489 verified against Unity security advisory |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Unity 6.3 breaking changes catalog completeness:** The KG seeding requirement is clear but the full list of breaking changes (especially in render pipeline, physics, and input system) needs structured extraction during Phase 1. The PITFALLS.md identifies specific namespaces (`UnityEngine.Experimental.*`, compatibility mode render graph calls, `[SerializeField]` on properties) but a complete diff from Unity 2022 → 6.3 was not compiled in this research session.

- **WebSocket server library for Unity C#:** The recommended approach uses `websocket-sharp` (MIT license, available via UPM). STACK.md notes this as a dependency for the C# server but ARCHITECTURE.md's code sample uses `System.Net.WebSockets.HttpListenerWebSocketContext` (stdlib). These are two different approaches; the implementation team needs to choose one before Phase 2 begins and verify UPM package availability for Unity 6.3.

- **tripo.io credit cost model:** The approval gate for tripo.io requires knowing the cost per API call to set sensible default thresholds. This was not researched. Phase 5 planning must include credit cost estimation.

- **qwen3.5 Unity 6.3 knowledge baseline:** The model's actual hallucination rate on Unity 6.3 API calls is unknown. Phase 4 planning should include a benchmark: generate 20 simple Unity 6.3 scripts and measure first-pass compile success rate before and after KG seeding, to validate whether KG seeding is sufficient or whether prompt engineering changes are also needed.

- **Blender 4.3 path on user machine:** The Blender executable path is hardcoded in STACK.md's example (`C:\Program Files\Blender Foundation\Blender 4.3\blender.exe`). The agent needs a discovery mechanism (registry lookup or configurable path in Jarvis config.json) rather than a hardcoded path.

---

## Sources

### Primary (HIGH confidence)
- `docs.unity3d.com/6000.3/Documentation/Manual/EditorCommandLineArguments.html` — Unity 6.3 CLI flags, `-batchmode -executeMethod` pattern
- `unity.com/blog/unity-6-3-lts-is-now-available` — Unity 6000.3.0f1 confirmed as LTS, December 2025
- `pypi.org/project/tripo3d` — tripo3d v0.3.12 verified, March 4, 2026 release
- `github.com/VAST-AI-Research/tripo-python-sdk` — Official tripo3d SDK, async polling, output format support
- `docs.blender.org/api/current/` — Blender Python API, `--background --python` CLI flags, stable across 4.x
- `developer.blender.org/docs/release_notes/4.3/python_api/` — Blender 4.3 Python API changes
- `websockets.readthedocs.io/en/stable/` — websockets v14.x, asyncio-native, Windows-compatible
- `docs.unity3d.com/6/Documentation/Manual/performance-gc-avoid-reflection.html` — Unity official: cache reflection at startup, avoid per-frame calls
- `docs.unity3d.com/6000.1/Documentation/ScriptReference/AssetPostprocessor.html` — AssetPostprocessor, OnPostprocessAllAssets hook pattern
- `unity.com/security/sept-2025-01` — CVE-2025-59489 Unity argument injection (score 8.4 HIGH)
- `docs.unity3d.com/6000.3/Documentation/Manual/UpgradeGuideUnity63.html` — Unity 6.3 breaking changes

### Secondary (MEDIUM confidence)
- `github.com/CoderGamester/mcp-unity` — WebSocket JSON-RPC architecture, port 8090, Unity C# server pattern
- `github.com/IvanMurzak/Unity-MCP` — MCP over stdio/HTTP, 50+ tools, Roslyn C# execution, dynamic tool generation
- `github.com/Bluepuff71/UnityMCP` — 40+ built-in tools, additional reference implementation
- `fast.io/resources/ai-agent-state-checkpointing/` — Three-layer checkpoint model (mission state, tool context, system config)
- `akanuragkumar.medium.com` — SSE streaming with asyncio.Queue, bounded queue backpressure
- `toolregistry.readthedocs.io` — Schema-first tool registry design pattern
- `surgehq.ai/blog/when-coding-agents-spiral-into-693-lines-of-hallucinations` — Hallucination cascade in autonomous coding agents, documented failure pattern
- `medium.com/@sattyamjain96/the-loop-of-death` — 90% autonomous agent production failures from unbounded retry loops

### Tertiary (LOW confidence)
- `aimultiple.com/agentic-frameworks` — Agent framework landscape 2026 (WebSearch only; used to confirm alternatives, not for implementation decisions)

---
*Research completed: 2026-03-16*
*Ready for roadmap: yes*
