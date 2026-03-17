# Roadmap: Jarvis v6.0 — Unity Agent

## Milestones

- ✅ **v1.0 Desktop Engine** — Phases 1–9 (shipped 2026-02-23)
- ✅ **v2.0 Android App** — Phases 10–13 (shipped 2026-02-25)
- ✅ **v3.0 Hardening** — 2 phases (shipped 2026-03-01)
- ✅ **v4.0 Intelligence & Voice** — 5 phases (shipped)
- ✅ **v5.0 Reliability & Continuity** — 6 phases (shipped 2026-03-16)
- 📋 **v6.0 Unity Agent** — Phases 20–25 (planned)

---

<details>
<summary>✅ v1.0–v5.0 (Phases 1–19) — SHIPPED</summary>

Phases 1–9: Desktop Engine
Phases 10–13: Android App
v3.0: Hardening (2 phases, unnumbered in earlier milestones)
v4.0: Intelligence & Voice (5 phases)
v5.0: Reliability & Continuity (6 phases ending at Phase 19)

See archived plans in `.planning/phases/`.

</details>

---

## 📋 v6.0 Unity Agent

**Milestone Goal:** Build an autonomous development agent that accepts natural language or voice instructions and produces complete, compiling Unity 6.3 projects — planning, coding, asset generation, testing, and debugging autonomously with user approval at destructive or costly milestones.

### Phases

- [x] **Phase 20: Infrastructure Foundations** - VRAM coordinator, process manager, KG seeding, AgentStateStore, ToolRegistry (completed 2026-03-17)
- [x] **Phase 21: Unity Editor Bridge** - C# WebSocket plugin, JSON-RPC dispatch, domain reload handling, path jail (completed 2026-03-17)
- [ ] **Phase 22: Core Agent Loop** - TaskPlanner, StepExecutor, ReflectionLoop, approval gate, SSE streaming
- [ ] **Phase 23: C# Code Generation** - Unity-domain prompting, compile-fix loop, NUnit tests, Editor panel UI
- [ ] **Phase 24: Asset Pipeline** - tripo.io 3D generation, Blender headless post-processing, Unity asset import
- [ ] **Phase 25: Polish and Integration** - Runtime tool registration, learn-as-you-go accumulation, voice intake, completion summaries

## Phase Details

### Phase 20: Infrastructure Foundations
**Goal**: The agent subsystem has safe, stable infrastructure in place before any Unity interaction begins — VRAM isolation, process lifecycle, Unity API knowledge in KG, and all state/tool contracts defined
**Depends on**: Phase 19 (v5.0 complete)
**Requirements**: UNITY-06, KNOW-01, TOOL-01, AGENT-04
**Success Criteria** (what must be TRUE):
  1. Ollama inference and Unity play-mode cannot run concurrently — VRAM coordinator mutex prevents OOM
  2. Unity process tree can be launched, tracked, and fully terminated without leaving orphaned processes
  3. Unity 6.3 API reference, breaking changes, and common error patterns are queryable in the knowledge graph
  4. AgentStateStore persists task plans and checkpoints to SQLite — task survives simulated crash and resumes from last checkpoint
  5. ToolRegistry accepts tool registrations with JSON Schema descriptors and approval flags; registered tools are discoverable
**Plans**: 2 plans

Plans:
- [ ] 20-01: VRAM coordinator, Unity process manager, CQRS agent command stubs
- [ ] 20-02: Unity 6.3 KG seeding, AgentStateStore schema, ToolRegistry interface

### Phase 21: Unity Editor Bridge
**Goal**: A stable C# WebSocket plugin in the Unity Editor communicates with Jarvis Python over JSON-RPC, handles domain reloads gracefully, enforces the path jail, and blocks dangerous generated code before compilation
**Depends on**: Phase 20
**Requirements**: UNITY-01, UNITY-02, UNITY-03, UNITY-04, CODE-04, CODE-05
**Success Criteria** (what must be TRUE):
  1. JarvisEditorBridge starts automatically in Unity Editor and accepts JSON-RPC commands on localhost:8091
  2. After a domain reload, the bridge sends a ready heartbeat and Jarvis enters WAITING_FOR_BRIDGE state until it arrives — no dropped commands
  3. UnityTool (Python) can create a Unity project, write a C# script, and trigger compilation via the bridge
  4. Any agent file write outside Assets/JarvisGenerated/ is rejected in Python before reaching the bridge
  5. Static analysis blocks generated C# containing Process.Start, File.Delete outside the path jail, or Assembly.LoadFrom before compilation
**Plans**: 2 plans

Plans:
- [ ] 21-01-PLAN.md — C# UPM package: JarvisEditorBridge, ReflectionCommandDispatcher, domain reload, TypeCoercer, StaticAnalysisGuard
- [ ] 21-02-PLAN.md — Python UnityTool WS client, path jail, static analysis guard, BridgeState machine

### Phase 22: Core Agent Loop
**Goal**: Users can give Jarvis a high-level Unity task and watch it execute autonomously — decomposing into steps, running tools, handling failures, requesting approval on destructive actions, and streaming progress to the widget
**Depends on**: Phase 21
**Requirements**: AGENT-01, AGENT-02, AGENT-03, AGENT-05, AGENT-06, TOOL-02, TOOL-03, TOOL-04, TOOL-05
**Success Criteria** (what must be TRUE):
  1. User gives a task like "create a rotating cube scene" and Jarvis produces a step-by-step plan visible in the widget before execution begins
  2. Agent executes the plan using tools, recovers from step failures by replanning, and completes the task without user re-prompting
  3. Destructive or costly tool calls are blocked until the user approves — safe operations run automatically
  4. Agent escalates to the user after 3 consecutive same-error failures rather than looping indefinitely
  5. Live progress events appear in the Jarvis widget as each step starts and completes
**Plans**: TBD

Plans:
- [ ] 22-01: TaskPlanner, StepExecutor, ReflectionLoop, ApprovalGate
- [ ] 22-02: ProgressEventBus, SSE /agent/stream endpoint, ShellTool, FileTool, WebTool, KGTool
- [ ] 22-03: End-to-end test — rotating cube task executes autonomously

### Phase 23: C# Code Generation
**Goal**: Agent-generated C# scripts compile correctly against Unity 6.3 APIs on the first or second attempt — domain-specific prompting, API validation, NUnit tests, and an in-Editor progress panel make the loop visible and reliable
**Depends on**: Phase 22
**Requirements**: CODE-01, CODE-02, CODE-03, UNITY-05, KNOW-03, KNOW-04
**Success Criteria** (what must be TRUE):
  1. Generated MonoBehaviour scripts use correct Unity 6.3 API signatures — CS0117/CS0619 compile errors trigger KG lookup for the right alternative, not blind retry
  2. Agent compiles, runs NUnit tests, enters play mode, and fixes errors autonomously up to the 5-retry cap
  3. Generated scripts include a paired NUnit test file under Assets/JarvisGenerated/Tests/
  4. Unity Editor panel shows the agent's current step, recent log entries, and approve/reject buttons inside Unity
  5. Unity 6.3 breaking change warnings (removed namespaces, deprecated APIs) surface during code generation before compilation
**Plans**: TBD

Plans:
- [ ] 23-01: Unity-domain system prompt, API validation pre-compilation pass, KG query integration
- [ ] 23-02: NUnit test generation, play mode entry/exit with GPU coordinator interlock
- [ ] 23-03: JarvisPanel.cs EditorWindow — progress UI, approve/reject dialogs

### Phase 24: Asset Pipeline
**Goal**: Agent can generate and import 3D assets — organic models via tripo.io and architecture/terrain via Blender headless — into the Unity project with correct import settings and user approval on API credit spend
**Depends on**: Phase 22
**Requirements**: ASSET-01, ASSET-02, ASSET-03, ASSET-04
**Success Criteria** (what must be TRUE):
  1. User requests "generate a wooden crate model" and a compiled, textured GLB/FBX asset appears in Assets/JarvisGenerated/ — tripo.io credit spend requires approval before the API call
  2. Blender headless pipeline optimizes raw tripo.io meshes (LOD, UV, normals) without manual intervention
  3. Imported assets appear in Unity with correct TextureImporter, ModelImporter, and AudioImporter settings applied automatically
  4. Agent selects tripo.io for organic/character models and Blender for geometry/terrain based on asset type
**Plans**: TBD

Plans:
- [ ] 24-01: TripoTool, BlenderTool, Blender bpy scripts library
- [ ] 24-02: AssetTool Unity import coordination, asset manifest output, agent routing logic

### Phase 25: Polish and Integration
**Goal**: The agent improves from its own history, accepts voice instructions, allows runtime tool additions, and gives clear completion summaries — making the full development loop observable and extensible
**Depends on**: Phase 24
**Requirements**: TOOL-06, KNOW-02
**Success Criteria** (what must be TRUE):
  1. After completing 10+ tasks, agent queries accumulated code patterns and error-fix pairs from the knowledge graph before its first LLM inference call on new tasks
  2. User says "use Mixamo for animations" and Jarvis registers that tool for the current session without restarting
  3. Voice command routes to AgentRunCommand — user can assign Unity tasks hands-free
  4. Completed task produces a summary: files created, steps taken, tokens used, errors encountered
**Plans**: TBD

Plans:
- [ ] 25-01: Learn-as-you-go KG accumulation, successful pattern and error-fix storage
- [ ] 25-02: Runtime tool registration (TOOL-06), voice intake wiring, task completion summaries, context trim strategy

## Progress

**Execution Order:** 20 → 21 → 22 → 23 → 24 → 25

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 20. Infrastructure Foundations | 2/2 | Complete    | 2026-03-17 | - |
| 21. Unity Editor Bridge | 2/2 | Complete    | 2026-03-17 | - |
| 22. Core Agent Loop | v6.0 | 0/3 | Not started | - |
| 23. C# Code Generation | v6.0 | 0/3 | Not started | - |
| 24. Asset Pipeline | v6.0 | 0/2 | Not started | - |
| 25. Polish and Integration | v6.0 | 0/2 | Not started | - |
