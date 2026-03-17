# Feature Research

**Domain:** Autonomous Unity Game Development Agent
**Researched:** 2026-03-16
**Confidence:** MEDIUM-HIGH (agent loop patterns HIGH; Unity-specific automation MEDIUM; tripo.io API MEDIUM)

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features a Unity agent must have or it does not qualify as an "agent" at all.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Natural language task intake | User describes "make a platformer" — agent must parse intent into structured steps | MEDIUM | Jarvis already has intent classification; needs Unity-domain prompting layer |
| Multi-step task planner | Agent must break a vague goal into ordered, executable steps autonomously | HIGH | ReAct + plan-and-execute hybrid is the production standard (2026); plan phase first, then step-by-step with deviation freedom |
| Unity Editor bridge (C# plugin) | Agent must be able to read and write to a live Unity project; no bridge = no automation | HIGH | MCP pattern is now the community standard; WebSocket or named-pipe JSON-RPC; EditorApplication.update dispatches to main thread. Reference: CoderGamester/mcp-unity, IvanMurzak/Unity-MCP |
| C# code generation and write-to-disk | Agent must produce runnable C# scripts and place them in the correct Assets/ paths | MEDIUM | Needs Unity-domain context seeded into the LLM; generic C# generation fails on MonoBehaviour patterns |
| Compile-trigger and error capture | After writing a script, agent must force AssetDatabase.Refresh(), detect compile errors from the console log, and feed them back into the fix loop | HIGH | Unity batch mode: `-batchmode -nographics -executeMethod`; AssetDatabase.Refresh() is the in-Editor trigger; console log polling via GetConsoleLog MCP tool |
| Error-fix retry loop with cap | Agent must attempt to fix compile/test errors automatically, stop after N attempts, and surface the error to user | HIGH | 5-retry cap is cited in PROJECT.md; pattern matches Devin 2.0 "self-reviewing PR" behavior; prevent infinite loops by tracking error fingerprints |
| Scene manipulation (create/modify GameObjects) | Agent must be able to create scenes, add GameObjects, assign components, and set transforms without user doing it | HIGH | Exposed via MCP: select_gameobject, update_gameobject, add_asset_to_scene, create_prefab, set_transform |
| Play mode entry and exit | Agent must be able to enter/exit Play mode to test runtime behavior | MEDIUM | EditorApplication.isPlaying; accessible via execute_menu_item or direct API; must sync with main thread |
| Progress reporting to user | User must see what the agent is doing in real-time (not a black box) | MEDIUM | Jarvis widget already has streaming display; agent emits step events to existing progress bus |
| Approval gate for destructive actions | Deleting assets, spending API credits, or replacing files requires explicit user approval | MEDIUM | PROJECT.md specifies: create=auto, destroy/spend=requires approval; ties to existing Jarvis HMAC-signed approval flow |

### Differentiators (Competitive Advantage)

Features that separate this agent from a generic "Copilot in Unity."

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Self-healing compile loop with error pattern learning | Agent learns which error types it has fixed before and applies fixes faster on repeat encounters; patterns stored in Jarvis KG | HIGH | Jarvis KG + memory engine already exist; add "error pattern" entity type. General tools (Cursor, Copilot) do not persist error-fix patterns across sessions |
| 3D asset generation via tripo.io API | Text or image prompt → production-ready GLB/FBX dropped into Unity Assets/; no manual modeling | HIGH | Tripo 2.5+ supports text-to-3D, multi-image input, auto-LOD, auto-rig, GLB/FBX export; Unity plugin exists. Cost gating required (credits) |
| Blender headless post-processing pipeline | After tripo.io generates a mesh, Blender Python runs headless to: apply modifiers, recalculate normals, generate LODs, bake textures, export game-ready FBX | HIGH | `blender.exe --background --python script.py`; Blenderless library simplifies headless rendering. Differentiator: tripo.io alone lacks fine-grained mesh optimization |
| Unity 6.3 knowledge graph seeding | Agent queries KG for Unity-specific patterns (MonoBehaviour lifecycle, Physics layer setup, Input System) before generating code — reduces hallucination rate | HIGH | Jarvis KG already exists; seed with Unity 6 API reference, common error→fix pairs, best-practice patterns. Direct fix for documented "AI knowledge gap" with Unity 6 docs |
| Learn-as-you-go pattern accumulation | Every successful code generation, error fix, and asset import gets stored in KG with context; future tasks of same type start faster and with higher first-pass success | HIGH | Leverages existing Jarvis autonomous mission + memory systems; no equivalent in commercial tools |
| Voice-driven task intake and live narration | User says "add a jump mechanic to the player" — agent responds with British butler voice narrating each step as it executes | MEDIUM | Jarvis STT + TTS already exist; wire Unity agent events into existing voice narration pipeline |
| Smart approval UX (notification-based) | Approval gates surface as mobile push notifications with Accept/Reject — user approves destructive actions from phone without switching to desktop | HIGH | Requires Android app + mobile API integration; Jarvis already has mobile API on port 8787 |
| Dynamic tool registry | New tools (e.g., Spine animation, ProBuilder, custom shader baker) can be registered at runtime without restarting the agent | MEDIUM | MCP extensibility pattern; base class registration; Tool RAG for large tool sets. Enables community/user-contributed tools |
| Real-time Editor panel (Unity side) | Custom EditorWindow in Unity shows agent task queue, current step, and a log of actions taken — developer sees exactly what the agent is doing inside Unity | MEDIUM | Unity EditorWindow + UIToolkit; subscribes to WebSocket events from Python agent; no equivalent in existing MCP tools |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Fully autonomous "ship to store" publishing | Sounds like the dream — agent builds and publishes a complete game | Build validation, store compliance, legal review, asset licensing, age ratings — all require human judgment. Automation here creates legal and financial liability | Agent builds; human reviews and triggers publish manually |
| Unlimited retry loops | "Just keep trying until it works" | Infinite loops on unfixable errors (missing SDK, wrong Unity version) burn API credits and block the system. Error fingerprinting shows some errors are never self-fixable | Hard cap at 5 retries; surface error to user with diagnosis; log error pattern to KG to prevent future recurrence |
| Full Unity project generation from scratch in one shot | One prompt → complete playable game | LLMs cannot hold full project context in one pass; output degrades badly past ~500 LOC in a single generation. Results in uncompilable projects | Phased generation: scaffold → systems one at a time → test each → integrate |
| Real-time Play mode AI takeover | Agent controls gameplay during Play mode to test it | Race condition between Editor main thread and Python agent; Unity's domain reload invalidates C# state unpredictably during agent control | Agent enters Play mode, observes console output and framerate, exits — does not send input events during testing |
| Automatic dependency resolution via Package Manager | Agent adds packages from Package Manager automatically as needed | Package version conflicts are common and hard to unwind automatically; wrong package versions cause cascade compile failures | Agent proposes packages with version; user approves via approval gate before install |
| Training a custom Unity-specialized LLM from scratch | "Fine-tune a model on all Unity code" | Requires millions of dollars of compute and labeled data. Out of scope per PROJECT.md | Seed existing LLMs with Unity 6 docs via KG + RAG; use Unity 6 Documentation MCP for live lookups |

---

## Feature Dependencies

```
[Natural Language Task Intake]
    └──requires──> [Multi-Step Task Planner]
                       └──requires──> [Unity Editor Bridge (C# Plugin)]
                                          └──requires──> [Scene Manipulation Tools]
                                          └──requires──> [Compile-Trigger + Error Capture]
                                                             └──requires──> [Error-Fix Retry Loop]

[C# Code Generation]
    └──requires──> [Unity 6.3 KG Seeding]  (improves correctness)
    └──requires──> [Unity Editor Bridge]   (to write files and trigger compile)

[3D Asset Generation (tripo.io)]
    └──requires──> [Approval Gate]         (API credit spend)
    └──enhances──> [Blender Post-Processing Pipeline]
                       └──requires──> [Blender headless install on host machine]

[Error-Fix Retry Loop]
    └──enhances──> [Learn-As-You-Go Pattern Accumulation]
                       └──requires──> [Jarvis Knowledge Graph] (already built)

[Voice Task Intake]
    └──requires──> [Jarvis STT Pipeline]   (already built)
    └──requires──> [Multi-Step Task Planner]

[Smart Approval UX (mobile)]
    └──requires──> [Jarvis Mobile API]     (already built on port 8787)
    └──requires──> [Approval Gate feature]

[Real-Time Editor Panel]
    └──requires──> [Unity Editor Bridge]
    └──enhances──> [Progress Reporting to User]

[Dynamic Tool Registry]
    └──enhances──> [Multi-Step Task Planner] (planner can discover new tools)
```

### Dependency Notes

- **Unity Editor Bridge is the load-bearing feature:** Everything that touches a live Unity project depends on it. It must be built in Phase 1 before any other Unity-facing feature can be validated.
- **KG Seeding amplifies code generation quality:** LLMs have a documented knowledge gap with Unity 6 API. Seeding the KG before writing code cuts hallucination-driven compile errors. This is a force multiplier, not a nice-to-have.
- **tripo.io requires approval gate:** Every call costs credits. The approval gate must gate credit-spending tool calls specifically; create=auto applies only to free operations (scene, GameObject, script).
- **Blender requires host-machine install:** Blender must be installed at a known path on the Windows 11 desktop. The agent invokes it as a subprocess. This is an external runtime dependency, not a Python package.
- **Learn-as-you-go reuses existing KG:** No new storage system needed. Error patterns and successful code snippets become KG entities using the existing fact extraction + NetworkX + SQLite infrastructure.

---

## MVP Definition

### Launch With (v1 — Core Agent Loop)

Minimum viable: agent takes a task, writes Unity C# code, compiles it, fixes errors, and reports success or failure.

- [ ] Multi-step task planner (ReAct + plan-and-execute hybrid) — without this, the agent is just a one-shot code generator
- [ ] Unity Editor Bridge (C# WebSocket plugin) — required to interact with any live Unity project
- [ ] C# code generation with Unity-domain prompting — the primary value delivery mechanism
- [ ] Compile-trigger + error capture via AssetDatabase.Refresh + console log polling — required to close the loop
- [ ] Error-fix retry loop (max 5 attempts with error fingerprinting) — makes the agent autonomous rather than requiring human re-prompting
- [ ] Scene manipulation tools (create scene, add GameObject, assign component) — needed for any real game task beyond scripts
- [ ] Approval gate (destroy/spend actions) — non-negotiable safety gate; project spec requirement
- [ ] Progress streaming to Jarvis widget — user must see what agent is doing

### Add After Validation (v1.x — Asset Pipeline)

Features to add once the core compile loop is reliable.

- [ ] Unity 6.3 KG seeding — trigger: first-pass code quality is poor due to Unity 6 API gaps; add after measuring hallucination rate
- [ ] tripo.io 3D asset generation — trigger: user requests asset creation, not just scripting
- [ ] Blender headless post-processing — trigger: tripo.io meshes need optimization before Unity import
- [ ] Learn-as-you-go pattern accumulation — trigger: after 10+ successful tasks generate enough signal to seed
- [ ] Real-time Unity Editor panel — trigger: agent is running tasks and user wants visibility inside Unity

### Future Consideration (v2+)

Features to defer until the core agent is proven reliable.

- [ ] Smart mobile approval UX — defer: requires Android app changes; worth doing after core agent is battle-tested
- [ ] Dynamic tool registry — defer: high value but complex; initial tool set is fixed; add extensibility when tool count exceeds ~15
- [ ] Voice task intake integration — defer: Jarvis STT exists, but wiring Unity agent events into voice narration is polish, not MVP
- [ ] Play mode automated behavioral testing — defer: complex main-thread synchronization; test via console log observation first

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Unity Editor Bridge (C# plugin) | HIGH | HIGH | P1 |
| Multi-step task planner | HIGH | HIGH | P1 |
| C# code generation (Unity-domain) | HIGH | MEDIUM | P1 |
| Compile + error capture loop | HIGH | HIGH | P1 |
| Error-fix retry (5-cap) | HIGH | MEDIUM | P1 |
| Scene manipulation tools | HIGH | MEDIUM | P1 |
| Approval gate (destroy/spend) | HIGH | LOW | P1 |
| Progress streaming to widget | MEDIUM | LOW | P1 |
| Unity 6.3 KG seeding | HIGH | MEDIUM | P2 |
| tripo.io asset generation | HIGH | MEDIUM | P2 |
| Blender headless post-processing | MEDIUM | HIGH | P2 |
| Learn-as-you-go pattern accumulation | HIGH | LOW | P2 |
| Real-time Unity Editor panel | MEDIUM | MEDIUM | P2 |
| Voice task intake integration | MEDIUM | LOW | P2 |
| Dynamic tool registry | MEDIUM | HIGH | P3 |
| Smart mobile approval UX | MEDIUM | HIGH | P3 |
| Play mode behavioral testing | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Must have for launch (v1)
- P2: Should have, add when core loop is green
- P3: Future consideration

---

## Competitor Feature Analysis

| Feature | GitHub Copilot Agent Mode | Devin 2.0 | Cursor Composer | Jarvis Unity Agent |
|---------|--------------------------|-----------|-----------------|-------------------|
| Multi-step autonomous planning | YES (analysis→plan→execute) | YES (Planner module, agent-native IDE) | YES (workflow engine, multi-file) | YES — target |
| Persistent error-fix learning | NO (session-only) | LIMITED (project-scoped) | NO | YES — KG stores patterns across sessions |
| Unity Editor direct control | NO (file edits only) | NO | NO | YES — C# bridge is core architecture |
| 3D asset generation | NO | NO | NO | YES — tripo.io + Blender pipeline |
| Voice-driven task intake | NO | NO | NO | YES — existing Jarvis STT |
| Mobile approval gates | NO | NO | NO | YES — existing mobile API |
| Domain-specific KG (Unity API) | NO | NO | NO | YES — seeded KG |
| Approval gates for destructive ops | LIMITED (file diffs shown) | YES (PR review) | LIMITED | YES — hard gate, not soft suggestion |
| Local-first / privacy-first | NO (cloud-dependent) | NO (cloud) | NO (cloud) | YES — local Ollama first |
| Learn-as-you-go accumulation | NO | LIMITED | NO | YES — KG + mission system |

**Key insight:** No existing tool combines Unity Editor direct control + persistent cross-session learning + 3D asset generation pipeline. The combination is the differentiator, not any single feature in isolation.

---

## Dependencies on Existing Jarvis Systems

These existing Jarvis systems are directly leveraged by the Unity agent — no rebuild required:

| Existing System | How Unity Agent Uses It |
|----------------|------------------------|
| CQRS command bus (70+ commands) | New agent commands (ExecuteAgentTask, ApproveAgentAction, CancelAgentTask) follow existing handler pattern |
| Knowledge graph (NetworkX + SQLite) | Stores Unity error patterns, successful code snippets, Unity API facts, tripo.io usage patterns |
| Intelligence gateway (Ollama + cloud fallback) | All code generation and planning LLM calls route through existing gateway |
| Memory engine (SQLite + FTS5 + sqlite-vec) | Agent task history, step logs, and asset metadata stored in existing memory store |
| Autonomous mission system | Agent "tasks" are structured as missions; reuses retry, reflection, and completion tracking |
| Desktop widget (progress streaming) | Unity agent emits step events that widget displays in existing activity feed |
| Mobile API (port 8787, HMAC-signed) | Approval gate pushes to mobile; Android app shows Accept/Reject notification |
| STT pipeline (Parakeet / Deepgram) | Voice task intake routes through existing STT before reaching task planner |
| TTS (Edge-TTS, Thomas Neural) | Agent narrates progress using existing voice output pipeline |

---

## Sources

- [The Era of Autonomous Coding Agents: Beyond Autocomplete](https://www.sitepoint.com/autonomous-coding-agents-guide-2026/)
- [Agentic Design Patterns: The 2026 Guide](https://www.sitepoint.com/the-definitive-guide-to-agentic-design-patterns-in-2026/)
- [ReAct vs Tree-of-Thought: Reasoning Frameworks](https://www.coforge.com/what-we-know/blog/react-tree-of-thought-and-beyond-the-reasoning-frameworks-behind-autonomous-ai-agents)
- [Navigating Modern LLM Agent Architectures](https://www.wollenlabs.com/blog-posts/navigating-modern-llm-agent-architectures-multi-agents-plan-and-execute-rewoo-tree-of-thoughts-and-react)
- [GitHub Copilot Workspace and the Agentic Era](https://www.javacodegeeks.com/2026/02/github-copilot-workspace-the-agentic-era.html)
- [GitHub Copilot Workspace vs Cursor vs Devin (2026)](https://agileleadershipdayindia.org/blogs/agentic-ai-sdlc-agile/github-vs-copilot-vs-cursor-vs-devin-comparison.html)
- [Cognition Devin 2.0](https://cognition.ai/blog/devin-2)
- [Agent-Native Development: Devin 2.0 Technical Design](https://medium.com/@takafumi.endo/agent-native-development-a-deep-dive-into-devin-2-0s-technical-design-3451587d23c0)
- [CoderGamester/mcp-unity: MCP Plugin for Unity Editor](https://github.com/CoderGamester/mcp-unity)
- [IvanMurzak/Unity-MCP: AI-powered Unity Editor bridge](https://github.com/IvanMurzak/Unity-MCP)
- [Bluepuff71/UnityMCP: 40+ built-in tools](https://github.com/Bluepuff71/UnityMCP)
- [MCP-Unity SIGGRAPH Asia 2025](https://dl.acm.org/doi/10.1145/3757376.3771417)
- [Unity Test Framework — Automated Testing](https://unity.com/how-to/automated-tests-unity-test-framework)
- [Unity Editor Scripting](https://learn.unity.com/tutorial/editor-scripting)
- [Unity 6.3 EditorWindow API](https://docs.unity3d.com/6000.3/Documentation/Manual/UIE-HowTo-CreateEditorWindow.html)
- [AssetDatabase.Refresh](https://docs.unity3d.com/6000.2/Documentation/ScriptReference/AssetDatabase.Refresh.html)
- [Tripo API](https://www.tripo3d.ai/api)
- [Tripo 2.5 Algorithm and Plugins](https://www.tripo3d.ai/blog/tripo-2.5-and-plugins)
- [Blender Python API](https://docs.blender.org/api/current/index.html)
- [Blenderless: headless rendering](https://github.com/oqton/blenderless)
- [Human-in-the-Loop for AI Agents](https://www.permit.io/blog/human-in-the-loop-for-ai-agents-best-practices-frameworks-use-cases-and-demo)
- [Tool RAG: Next Breakthrough in Scalable AI Agents](https://next.redhat.com/2025/11/26/tool-rag-the-next-breakthrough-in-scalable-ai-agents/)
- [Closing the Agentic Coding Loop with Self-Healing Software](https://logicstar.ai/blog/closing-the-agentic-coding-loop-with-self-healing-software)
- [8 AI Code Generation Mistakes (2026)](https://vocal.media/futurism/8-ai-code-generation-mistakes-devs-must-fix-to-win-2026)
- [Unity AI Beta 2026](https://discussions.unity.com/t/unity-ai-beta-2026-is-here/1703625)

---
*Feature research for: Autonomous Unity Game Development Agent (Jarvis v6.0)*
*Researched: 2026-03-16*
