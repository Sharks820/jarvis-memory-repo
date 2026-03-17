# Requirements: Jarvis v6.0 — Unity Agent

**Defined:** 2026-03-17
**Core Value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.

## v6.0 Requirements

### Agent Core

- [ ] **AGENT-01**: User can give a high-level task via text/voice and Jarvis breaks it into executable steps
- [ ] **AGENT-02**: Agent executes steps sequentially using registered tools with error handling
- [ ] **AGENT-03**: Agent reflects on step results and replans when outcomes differ from expectations
- [ ] **AGENT-04**: Agent checkpoints state to SQLite before each tool call for crash recovery
- [ ] **AGENT-05**: Agent respects task-level token budget and escalates after 3 consecutive same-error failures
- [ ] **AGENT-06**: Agent streams real-time progress events to widget and Unity Editor panel via SSE

### Tool Layer

- [ ] **TOOL-01**: Pluggable tool registry with standard interface (execute, validate, estimate_cost)
- [ ] **TOOL-02**: Smart approval gate: safe=auto, destructive=approve, costly=approve+estimate
- [ ] **TOOL-03**: FileTool reads/writes project files confined to project directory
- [ ] **TOOL-04**: ShellTool executes commands via subprocess with policy gate and timeout
- [ ] **TOOL-05**: WebTool integrates with existing web fetch pipeline for research
- [ ] **TOOL-06**: User can register new tools at runtime ("use Mixamo for animations")

### Unity Integration

- [ ] **UNITY-01**: JarvisEditorBridge C# plugin communicates via WebSocket JSON-RPC on localhost:8091
- [ ] **UNITY-02**: Bridge uses reflection-based command dispatch covering full Unity Editor API
- [ ] **UNITY-03**: Bridge handles domain reload gracefully (heartbeat + reconnect + WAITING_FOR_BRIDGE state)
- [ ] **UNITY-04**: UnityTool creates projects, writes C# scripts, compiles, builds via bridge
- [ ] **UNITY-05**: Unity Editor panel shows agent progress, approval dialogs, console streaming
- [ ] **UNITY-06**: VRAM coordinator prevents OOM when Ollama and Unity share 8GB GPU

### Code Generation

- [ ] **CODE-01**: Agent generates valid Unity 6.3 C# scripts with correct API usage
- [ ] **CODE-02**: Agent compiles, runs tests, enters play mode, and fixes errors in a verify-fix loop
- [ ] **CODE-03**: Agent writes NUnit tests alongside game scripts
- [ ] **CODE-04**: Pre-compilation static analysis blocks dangerous APIs (Process.Start, File.Delete outside jail)
- [ ] **CODE-05**: Generated code confined to Assets/JarvisGenerated/ path jail

### Asset Pipeline

- [ ] **ASSET-01**: TripoTool generates 3D models via tripo.io API from text descriptions
- [ ] **ASSET-02**: BlenderTool runs headless Python scripts for modeling, rigging, UV, LOD, export
- [ ] **ASSET-03**: AssetTool imports models/textures/audio into Unity with correct import settings
- [ ] **ASSET-04**: Agent uses tripo.io for organic models and Blender for architecture/terrain

### Knowledge

- [ ] **KNOW-01**: Unity 6.3 API reference, patterns, and common errors seeded into knowledge graph
- [ ] **KNOW-02**: Successful code patterns and error fixes accumulated via learn-as-you-go
- [ ] **KNOW-03**: Agent queries KG during planning for Unity-specific guidance
- [ ] **KNOW-04**: Breaking changes from Unity 6.3 upgrade guide flagged during code generation

## Future Requirements

### Advanced Agent

- **ADV-01**: Agent can work on multiple Unity projects simultaneously
- **ADV-02**: Agent can create custom Unity Editor tools/windows for the game
- **ADV-03**: Agent can publish builds to platforms (Steam, itch.io)
- **ADV-04**: Agent can generate procedural content (levels, terrain, dungeons)

### Extended Tools

- **EXT-01**: MixamoTool for character animations
- **EXT-02**: SketchfabTool for downloading free 3D models
- **EXT-03**: Unity Asset Store integration for package discovery
- **EXT-04**: Music/SFX generation tool integration

## Out of Scope

| Feature | Reason |
|---------|--------|
| Cloud deployment of agent | Local-first is non-negotiable |
| Multi-user collaboration | Single-owner assistant |
| iOS/Mac Unity builds | Windows-only target for now |
| Custom LLM training | Use best available models via API + local inference |
| Real-time multiplayer game templates | Too complex for v6.0, defer to future |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| (populated during roadmap creation) | | |

**Coverage:**
- v6.0 requirements: 27 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 27

---
*Requirements defined: 2026-03-17*
*Last updated: 2026-03-17 after initial definition*
