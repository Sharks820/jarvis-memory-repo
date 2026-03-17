# Pitfalls Research

**Domain:** Autonomous Unity game development agent (added to existing Jarvis Python/Ollama system)
**Researched:** 2026-03-16
**Confidence:** HIGH (critical pitfalls), MEDIUM (integration-specific), HIGH (VRAM)

---

## Critical Pitfalls

### Pitfall 1: Agent Hallucination Cascade — Inventing APIs That Don't Exist

**What goes wrong:**
The LLM generates C# code referencing a Unity API that either never existed, was removed, or changed signature in Unity 6.x. When the compiler reports an error, the agent doubles down — patching the hallucinated code rather than reconsidering the API call. After 5+ turns the agent has built a tower of fixes on top of a non-existent foundation. This is documented behavior: models fill truncated file reads with "best guesses" instead of flagging uncertainty, then treat invented class names (`BaseWriter`, `NetworkTransform.Update`) as ground truth for all downstream generations.

**Why it happens:**
qwen3.5 training data predates Unity 6.0 (released October 2023). The model has strong Unity 2020-2022 patterns, weak Unity 6.x patterns, and zero knowledge of Unity 6.3 breaking changes. When it generates `NetworkTransform.Update`, `[SerializeField]` on a property, or Compatibility Mode render graph calls — all of which broke in Unity 6.3 — the compiler error looks like a bug to fix rather than a wrong API.

**How to avoid:**
- Seed the Jarvis knowledge graph with Unity 6.3 official API reference before the agent writes any code. This is PROJECT.md's stated "deep Unity 6.3 knowledge seeding" requirement — treat it as a Phase 1 prerequisite, not a nice-to-have.
- Build a Unity API validation step into the ReflectionLoop: before compiling, check generated method names and type names against a locally-stored API dictionary (exported from Unity docs).
- When compiler returns `CS0117` (member does not exist) or `CS0619` (deprecated), the ReflectionLoop must query KG for the correct Unity 6.3 alternative, not attempt a blind fix.
- Cap the reflection loop at 5 retries as planned, but add a "same-error 3x = escalate to user" rule before exhausting attempts.

**Warning signs:**
- Compile error `CS0117: 'X' does not contain a definition for 'Y'` appearing on the same class across retries
- Agent output contains namespace `UnityEngine.Experimental.*` (most of this namespace was removed/graduated in Unity 6.0)
- Agent uses `RenderSettings.skybox` pipeline compatibility calls after initial scaffolding
- `using UnityEngine.Rendering.Universal;` with `ScriptableRendererFeature` Compatibility Mode calls

**Phase to address:** Phase 1 (Unity Knowledge Seeding + API Validation Layer). Must be complete before any code generation attempts.

---

### Pitfall 2: Domain Reload Deadlock Kills the Automation Pipeline

**What goes wrong:**
Every time the agent writes a new `.cs` file into the Unity project, Unity triggers a domain reload. Domain reload takes 2-15 seconds. If the agent sends the next JSON-RPC command to JarvisEditorBridge while reload is in progress, the command either silently drops, Unity's C# AppDomain is torn down mid-execution, or the Editor hangs on "Reloading Domain" indefinitely (confirmed user reports in Unity Discussions, 2025). The agent gets no response, treats it as a timeout, retries, queues more commands — potentially triggering additional reloads while one is already running.

**Why it happens:**
The Python agent side sees an HTTP/socket timeout and applies its normal retry logic. It has no awareness that the other end is in a mid-reload state where no C# code is running. JarvisEditorBridge will not exist as a running object during domain reload.

**How to avoid:**
- JarvisEditorBridge must implement a ready-state handshake. After every domain reload completes, the bridge re-registers itself via `[InitializeOnLoad]` and sends a `{"status": "ready"}` heartbeat to the Python side.
- Python agent must enter a `WAITING_FOR_BRIDGE` state after writing any `.cs` file, refusing to send further commands until the ready heartbeat is received (with 30-second max wait before escalating).
- Batch script writes: instead of writing one file and waiting, collect all files the agent wants to write for one task step, write them all at once, then wait a single domain reload.
- Set `EnterPlayModeOptions` to `DisableDomainReload` only during known-safe play-mode testing phases, not during code generation phases (different tradeoffs).

**Warning signs:**
- Agent logs show repeated command timeouts followed by a successful command — reload was occurring
- Unity Editor console shows "Domain Reload" timestamp followed by a large gap in Jarvis bridge logs
- Multiple `[InitializeOnLoad]` log entries in rapid succession (multiple reloads triggered)

**Phase to address:** Phase 2 (JarvisEditorBridge implementation). The ready-state handshake protocol must be designed into the bridge from day one.

---

### Pitfall 3: Shared VRAM Exhaustion — Ollama + Unity GPU Competition

**What goes wrong:**
On the RTX 4060 Ti 8GB, qwen3.5 in Q4_K_M quantization consumes approximately 5.5-6.5GB VRAM at full load with the pre-loaded context. Unity's GPU usage varies dramatically: 200MB-500MB in Editor idle, 1-3GB during play-mode rendering of complex scenes, and spikes above that with post-processing or real-time lighting. When the agent triggers a play-mode test at the same moment Ollama is processing a reflection prompt, total VRAM demand can reach 8.5-9GB — causing one or both processes to crash, fall back to CPU (5-30x slower inference), or hard OOM with driver recovery.

**Why it happens:**
VRAM is a hard boundary, not a soft limit. Neither Ollama nor Unity yield voluntarily — they both assume they own the GPU. Ollama's `OLLAMA_KEEP_ALIVE` keeps the model loaded even during idle periods. Unity's VRAM usage during play mode is not predictable from the Editor side.

**How to avoid:**
VRAM budget allocation (8GB total):
- Ollama base reservation: 5.5GB (qwen3.5 Q4_K_M model weights, non-negotiable)
- KV cache at 4096 context: ~0.8GB
- Unity Editor baseline: ~0.5GB
- Unity play-mode rendering budget: ~1.0GB MAX (enforce via project quality settings)
- Safety headroom: 0.2GB
- Total: 8.0GB — no room for error.

Implementation rules:
1. Never trigger play-mode tests while Ollama is processing a multi-step generation. Build a `GPU_COORDINATOR` lock in the Python agent: `generation_active` and `unity_playmode_active` must be mutually exclusive.
2. Set `OLLAMA_NUM_CTX=4096` hard cap. Longer contexts for agent tasks mean KV cache grows from 0.8GB to 1.5GB+ and blow the budget.
3. Unity project quality settings: cap render scale, disable real-time shadows, disable post-processing during agent-driven play-mode tests. These are test runs, not final renders.
4. Monitor with `nvidia-smi` sampling in the Python daemon — if VRAM > 7.5GB, pause agent and log a `VRAM_PRESSURE` event before attempting next step.

**Warning signs:**
- `cuda: out of memory` in Ollama logs
- Unity Editor crashes or "Graphics device lost" during play mode
- Ollama inference drops from 40 tokens/s to 8 tokens/s (partial CPU fallback detected)
- `nvidia-smi` shows GPU utilization at 100% with VRAM at 7.8GB+

**Phase to address:** Phase 1 (Infrastructure setup). VRAM coordinator must exist before any combined Ollama+Unity workflow. Do not defer.

---

### Pitfall 4: Unsafe Code Execution Without Sandboxing

**What goes wrong:**
The agent generates C# `[MenuItem]` scripts that call `System.IO.File.Delete`, `Directory.Delete`, `AssetDatabase.DeleteAsset`, or `UnityEditor.FileUtil.DeleteFileOrDirectory` on paths it constructed from LLM-generated strings. A hallucinated path like `Application.dataPath + "/../"` combined with a recursive delete call can wipe the entire Unity project or parent directories. There is also CVE-2025-59489 (score 8.4 HIGH) which demonstrates that Unity's argument handling is itself exploitable for arbitrary code execution.

**Why it happens:**
The agent has destroy/delete approval rules planned, but the current design focuses on asset-level operations. File system operations generated inside C# scripts execute in the Editor process with full user permissions — they are not intercepted by any Jarvis approval layer unless explicitly coded.

**How to avoid:**
- All agent-generated C# scripts that write to Editor MenuItems or use AssetDatabase must be reviewed by a static analysis pass before compilation. Specifically block: `System.IO.File.Delete`, `Directory.Delete`, recursive path operations, `Process.Start`, `System.Reflection.Assembly.LoadFrom`.
- The Python agent's tool layer must classify "writes a file to disk that will be compiled and executed" as equivalent to "destroy operation" — requiring explicit approval regardless of whether the file operation is in Python or C#.
- Implement a path jail: all agent-generated file operations must stay within `Assets/JarvisGenerated/` directory. Validate this in the Python bridge before writing any file.
- Never use `ExecuteInEditMode` attribute in agent-generated scripts during prototype phases.

**Warning signs:**
- Generated C# contains `Application.dataPath` combined with `../` path traversal
- Generated code uses `System.IO` namespace with delete/move operations
- Script contains `Process.Start()` or `Assembly.LoadFrom()`
- Agent generates a `[MenuItem]` that is immediately invoked programmatically

**Phase to address:** Phase 2 (JarvisEditorBridge). Path jail and static pre-compilation analysis must be implemented alongside the bridge, not as a later security hardening phase.

---

### Pitfall 5: Reflection-Based Dispatch and IL2CPP Incompatibility

**What goes wrong:**
The `JarvisEditorBridge` design uses reflection-based JSON-RPC to cover the "full Unity API." In the Editor this works fine — Mono JIT is available. But if Jarvis ever generates code that uses the same reflection dispatch pattern inside a game build targeting IL2CPP (WebGL, console, mobile), the code either silently fails at runtime (method stripped by IL2CPP linker), throws `MissingMethodException`, or causes garbage collection heap inflation because Unity never GCs reflected `MethodInfo` objects.

**Why it happens:**
IL2CPP performs ahead-of-time compilation and strips types/methods that the static analyzer can't prove are reachable. Reflection calls are invisible to the static analyzer. The reflection cache issue is Unity-documented: "Mono and IL2CPP internally cache all C# reflection objects and by design, Unity does not garbage collect them."

**How to avoid:**
- Keep all reflection-based dispatch strictly inside `Editor/` assembly (files under `Assets/Editor/` or `Assets/JarvisGenerated/Editor/`). Editor code is never compiled for IL2CPP builds — no stripping risk.
- For any generated game-runtime code (not editor code), use interface dispatch or a pre-built command dictionary (`Dictionary<string, Action>`) instead of runtime `MethodInfo` lookup.
- Add `link.xml` to preserve any types the bridge needs to interact with dynamically, as a defense-in-depth measure.
- Performance rule: cache reflected `MethodInfo` objects on first call. Never use `Type.GetMethod()` on the hot path (each call is ~10x slower than a direct dispatch).

**Warning signs:**
- Bridge code placed outside `Editor/` folder (will be included in builds)
- Generated game-runtime code contains `GetMethod`, `Invoke`, `GetProperty` strings
- GC allocation graphs show sustained `System.Reflection.MethodInfo` allocation

**Phase to address:** Phase 2 (JarvisEditorBridge architecture). Enforce the `Editor/`-only constraint at project structure level, not as a code review concern.

---

### Pitfall 6: Agent Loop Escape — Cost and Time Explosion Without Hard Limits

**What goes wrong:**
The agent enters a compile-fix-retry cycle where each fix introduces a new error. Without hard limits, a single "create a physics system" task can consume hundreds of Ollama inference calls over hours. With local inference this costs compute rather than money, but it still blocks the GPU (preventing Jarvis from answering voice queries), fills logs with garbage, and may make no forward progress. Production data from 2025 deployments shows 90% of autonomous agent failures in production stem from unbounded retry loops — the model conditions each attempt on the accumulated failure context, which itself contains hallucinations, making each attempt progressively worse.

**Why it happens:**
The ReflectionLoop design has a 5-retry cap, but the cap may apply per-error not per-task. If the task plan has 10 steps and each step retries 5 times, that's 50 inference calls on a task that should take 10. No progress detection, no accumulated-failure escalation, no cost accounting.

**How to avoid:**
- Implement a task-level token budget, not just a step-level retry cap. Default: 50,000 tokens per top-level task. If budget is exhausted before completion, escalate to user with a summary of progress and blocking issue.
- Implement loop detection: hash the (last_error, last_generated_code_diff) tuple. If the same hash appears twice in a task's history, the agent is looping — stop immediately and escalate.
- Track "no-progress" steps: if 3 consecutive steps produced compile errors without any successfully compiled file, escalate rather than continue.
- Log every inference call with token count to Jarvis's existing cost tracking infrastructure. This already exists for cloud models — extend it to local inference with token count.

**Warning signs:**
- Same compiler error message appearing in 3+ consecutive step logs
- Task runtime exceeds 15 minutes without any `STEP_COMPLETE` event
- Total token count for a task crosses 30,000 without user notification
- Agent log shows identical code blocks being generated and discarded repeatedly

**Phase to address:** Phase 3 (ReflectionLoop + StepExecutor). Hard limits must be baked into the loop architecture, not added as afterthoughts.

---

### Pitfall 7: Static Field Persistence Across Domain Reloads (Disabled Domain Reload Mode)

**What goes wrong:**
If the project uses `EnterPlayModeOptions.DisableDomainReload` to speed up iteration (common optimization), static variables in agent-generated scripts retain their values between play mode sessions. An agent-generated enemy spawner with `static int spawnCount = 0` will accumulate spawns across test runs, making each play-mode test non-deterministic. Event handlers registered with `+=` in static initializers accumulate, causing the same handler to fire multiple times. This makes the agent's "compile → test → fix" loop unreliable — test results vary based on how many times play mode was entered.

**Why it happens:**
Domain reload normally clears all static state. With it disabled, the state is an implicit global that persists. Agent-generated code is unlikely to include `[InitializeOnEnterPlayMode]` reset methods because LLMs don't generate defensive infrastructure by default.

**How to avoid:**
- Do not disable domain reload during agent development phases. The speed gain (2-5 seconds per cycle) is not worth the non-determinism in automated test loops.
- If domain reload is disabled for other performance reasons, the code generation template must include a `[RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]` method that resets all static fields to defaults.
- Add a "reset statics" check to the pre-play-mode validation step in JarvisEditorBridge.

**Warning signs:**
- Test results differ between first and second run of the same play-mode test
- Enemy/object count incrementing unexpectedly across sessions
- Event handlers firing N+1 times where N is the number of prior play-mode entries

**Phase to address:** Phase 2 (JarvisEditorBridge). Document the domain reload policy decision explicitly before any testing automation is built.

---

### Pitfall 8: Python-to-Unity Process Management — Orphaned Editor Instances

**What goes wrong:**
The Python agent launches Unity Editor in batch mode for compilation checks or headless tests. On Windows, `subprocess.terminate()` sends SIGTERM — which Windows ignores for most processes. The Unity Editor process tree (`Unity.exe` + child `UnityShaderCompiler.exe` + child asset import workers) remains running after the Python agent exits or is killed. On the next agent task start, it tries to open the same project — but Unity locks the project while it's open, and the orphaned instance holds the lock. The agent fails with "Cannot open project: another instance is running."

**Why it happens:**
Windows does not propagate kill signals to process trees. The correct Windows pattern requires `taskkill /f /t /pid` to kill the entire tree. Python's `subprocess.Popen.terminate()` and `subprocess.Popen.kill()` on Windows only kill the parent process — children become orphaned. This is a confirmed issue in the psutil and asyncio documentation.

**How to avoid:**
- Never use `proc.terminate()` or `proc.kill()` alone for Unity processes on Windows. Always use `psutil.Process(pid).kill()` followed by killing all children: `for child in proc.children(recursive=True): child.kill()`.
- Alternatively: `subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)])` is the most reliable Windows tree kill.
- On Unity Editor launch, store the PID in a lockfile at `.planning/runtime/unity_editor.pid`. On agent shutdown (atexit handler), check this file and kill the process tree.
- Add a startup check: if `unity_editor.pid` exists and the PID is still running, kill it before attempting to open the project.
- Jarvis already has this pattern for Ollama tracking (`_ollama_started_by_widget`). Replicate the same pattern for Unity Editor.

**Warning signs:**
- `taskmgr` shows multiple `Unity.exe` processes after agent restarts
- Agent fails with "project already open" error on startup
- `UnityShaderCompiler.exe` visible in Task Manager after Unity should be closed

**Phase to address:** Phase 1 (Infrastructure setup). Process management must be solved before any automated Unity launching is implemented.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip Unity API validation pre-compilation | Faster first implementation | Agent spirals on wrong APIs; every error becomes a hallucination cascade | Never — validation layer is foundational |
| Use `async/await` in Unity Editor scripts without main-thread dispatch | Simpler code | Async continues after play-mode stop; race conditions with domain reload | Never in Editor automation code |
| One JSON-RPC call per file write | Simple protocol | Triggers domain reload per file; N files = N reloads = N×15s wasted | Never — batch file writes into single reload cycle |
| Disable domain reload globally for speed | 2-5s faster iteration | Non-deterministic test results; agent loop produces garbage data | Only during manual debugging sessions, not agent runs |
| No VRAM coordinator — let OS manage GPU contention | Nothing to build | Unpredictable OOM crashes; inference quality degrades silently | Never on 8GB VRAM budget |
| Reflection dispatch in generated game-runtime code | Flexible bridge API | Silent failures in IL2CPP builds; GC heap inflation | Only acceptable in Editor-only assemblies |
| Agent retry cap per-step only | Simple retry logic | 50+ inference calls on a 10-step task; GPU blocked for hours | Never — task-level budget required |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Ollama + Unity GPU sharing | Assume GPU memory is shared gracefully | Implement mutex: agent generation and play-mode tests must not overlap |
| Python agent → Unity Editor via HTTP | Fire-and-forget commands, no ready-state check | Wait for `{"status": "ready"}` heartbeat before each command batch |
| Unity Editor batch mode | Use `-quit` flag, assume clean exit | Use `EditorApplication.Exit(0)` explicitly; `-quit` skips EditorApplication.update |
| Domain reload completion | Poll with fixed sleep | Wait for `[InitializeOnLoad]` re-registration event from bridge |
| Unity 6.3 API generation | LLM uses Unity 2021-2022 API patterns | Seed KG with Unity 6.3 docs; validate generated API names before compilation |
| C# compilation errors → agent fix | Retry with same strategy on same error | Hash error+code; if same hash repeats, escalate rather than retry |
| Windows process kill | `proc.kill()` or `proc.terminate()` | `taskkill /f /t /pid <PID>` to kill entire process tree including children |
| Unity serialization of agent-generated fields | Add `[SerializeField]` to C# properties | `[SerializeField]` now only valid on fields in Unity 6.3; use `[field: SerializeField]` for auto-props |
| Jarvis CQRS bus + agent commands | Create new bus for agent commands | Add agent commands to existing 70+ command bus; follow existing handler pattern |
| qwen3.5 context window during reflection | Let context grow across all retries | Trim or summarize earlier retry history before next attempt; hard cap 4096 tokens |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| KV cache growth across agent loop steps | Ollama inference slows from 40 to 8 tokens/s mid-task | Cap `OLLAMA_NUM_CTX=4096`; truncate old retry history | After ~3 retries with full error context accumulated |
| Domain reload on every file write | Task that should take 30s takes 5 minutes | Batch all file writes, trigger single reload | From the very first file written one-at-a-time |
| Unity play-mode test while Ollama generating | GPU OOM crash or 5x inference slowdown | GPU coordinator mutex between inference and play-mode | Every time both happen within the same minute |
| Reflection `MethodInfo` lookup in hot-path dispatch | GC allocs accumulate; frame time spikes | Cache MethodInfo on first lookup | After ~100 commands dispatched without caching |
| Agent writing files → Unity auto-import pipeline | Import takes 10-60s for large textures/models | Defer imports or use `AssetDatabase.StartAssetEditing()` / `StopAssetEditing()` batching | Any task generating more than 5 assets simultaneously |
| Log flooding during agent reflection loop | Disk I/O competes with Unity, inference, file writes | Rate-limit agent logs; use structured logging to SQLite (already in Jarvis arch) | After 20+ retry steps generating verbose logs |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Agent writes C# with `System.IO.File.Delete` inside generated scripts | Agent-generated code wipes project files or parent directories | Static analysis pre-compilation pass; block delete/move file API calls in generated code |
| No path jail on agent file writes | Agent writes outside `Assets/JarvisGenerated/` | Enforce path prefix validation in Python before writing any file |
| JarvisEditorBridge accepting commands without auth | Any local process can inject arbitrary Unity Editor commands | Use shared secret or localhost-only binding; validate command source |
| Agent-generated scripts with `[MenuItem]` auto-invocation | Immediate execution of potentially destructive operations | Separate generation from invocation; require explicit approval for MenuItem execution |
| CVE-2025-59489 Unity argument injection | Arbitrary code execution via command-line argument handling | Keep Unity Editor updated to patched version; do not pass user-supplied strings as Editor launch arguments |
| Unrestricted `Process.Start()` in generated C# | Agent can execute arbitrary system commands | Block `System.Diagnostics.Process` namespace in generated code static analysis pass |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Agent runs silently for 10+ minutes with no progress updates | User assumes it's frozen, kills process, loses work | Stream real-time step events to Jarvis widget and Unity Editor panel every 5 seconds minimum |
| Agent asks for approval mid-generation in a modal dialog | Blocks autonomous flow; user has to switch focus repeatedly | Queue approvals and present as a batch summary; only interrupt for destroy/spend operations |
| No way to cancel a running agent task | User must kill process, corrupting in-progress file writes | Implement cancellation token; agent checks cancel flag between every step |
| Agent failure messages say "compilation failed" with no detail | User cannot debug or understand what went wrong | Include the first 3 compiler errors verbatim in the failure notification |
| Generated project structure is opaque | User doesn't know what files were created or where | Emit a file manifest to Jarvis memory after each task completion |
| Agent creates assets with generic names (`Script1.cs`, `GameObject_0`) | Project becomes disorganized after multiple tasks | Enforce naming convention in code generation template: `[TaskContext]_[ComponentType].cs` |

---

## "Looks Done But Isn't" Checklist

- [ ] **VRAM coordinator:** Agent and Unity play-mode tests appear to work in isolation — verify they cannot run simultaneously under load (RTX 4060 Ti 8GB will OOM without the mutex).
- [ ] **Domain reload handshake:** Bridge appears to accept commands — verify it sends ready-state heartbeat after every reload, not just on initial startup.
- [ ] **Process tree cleanup:** Agent appears to stop Unity — verify `UnityShaderCompiler.exe` and import worker children are also killed using `taskkill /t`.
- [ ] **API version validation:** Agent generates C# that compiles — verify it was not using deprecated Unity 2022 APIs that compile with warnings but break at runtime (e.g., old Physics callbacks, `OnTriggerEnter` signature changes).
- [ ] **Loop escape:** Agent completes a task — verify it did not silently exhaust its retry budget and return a partial result without escalating.
- [ ] **Path jail enforcement:** Agent writes files only inside `Assets/JarvisGenerated/` — verify a path traversal attempt (`../../`) is rejected before the file is written.
- [ ] **Approval gate for destructive ops:** Approval workflow for destroy operations appears to work in tests — verify it also intercepts C# scripts that call `AssetDatabase.DeleteAsset` or file delete APIs.
- [ ] **Serialization reset on domain reload:** Agent-generated scripts appear stateless — verify static fields are reset between play-mode sessions when domain reload is disabled.
- [ ] **CQRS integration:** Agent commands appear to work standalone — verify they are registered in the existing command bus and follow the lazy-import handler pattern (not creating a second bus).
- [ ] **Unity 6.3 `[SerializeField]` on properties:** Generated code appears valid — verify no `[SerializeField]` attributes on C# properties (compile error in Unity 6.3 — must use `[field: SerializeField]`).

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Hallucination cascade (wrong APIs) | MEDIUM | Clear agent task history; re-seed KG with Unity 6.3 API reference; start task from scratch with explicit API constraints in system prompt |
| Domain reload deadlock | LOW | `taskkill /f /pid <unity_pid>`; restart bridge; re-run task from last successful step checkpoint |
| VRAM OOM crash | LOW | Restart Ollama (`ollama serve`); reduce `OLLAMA_NUM_CTX` to 2048; set Unity quality to lowest; retry task |
| Orphaned Unity process holding project lock | LOW | Run `taskkill /f /t /im Unity.exe`; delete `Assets/ProjectSettings/.*.lock` files; re-open project |
| Unsafe file deletion by agent-generated code | HIGH | Git restore (`git checkout -- .`) to recover files; audit generated scripts for file system calls; add static analysis gate |
| Agent loop cost explosion | LOW | Kill agent task; review loop detection logs; identify the recurring error hash; manually provide the correct API or pattern |
| IL2CPP build failure from reflection-based dispatch | MEDIUM | Move bridge code to `Editor/` folder; replace runtime `MethodInfo.Invoke` with interface dispatch; rebuild |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Hallucinated Unity APIs | Phase 1: Knowledge seeding + API validation layer | Compile 20 agent-generated scripts; verify zero `CS0117`/`CS0619` errors from wrong API usage |
| Domain reload deadlock | Phase 2: JarvisEditorBridge ready-state protocol | Simulate domain reload mid-command; verify agent enters `WAITING_FOR_BRIDGE` state and recovers |
| VRAM exhaustion (Ollama + Unity) | Phase 1: GPU coordinator infrastructure | Run concurrent generation + play-mode test; verify no OOM; `nvidia-smi` stays under 7.5GB |
| Unsafe code execution | Phase 2: Path jail + pre-compilation static analysis | Attempt path traversal via agent; verify rejection before file write |
| IL2CPP reflection incompatibility | Phase 2: Bridge architecture enforces Editor-only | Confirm bridge assembly is under `Assets/Editor/`; build a WebGL target; verify no missing method errors |
| Agent loop explosion | Phase 3: ReflectionLoop hard limits | Force a compile error that cannot be fixed; verify agent escalates at task token budget or same-error-3x rule |
| Static field persistence across play-mode | Phase 2: Domain reload policy + template includes reset method | Run play-mode test 3x; verify deterministic results each run |
| Orphaned Unity processes | Phase 1: Process manager with PID lockfile + tree kill | Kill Python agent while Unity is open; verify Unity and all child processes are killed on restart |
| Serialization breaking on Unity 6.3 | Phase 1: Knowledge seeding (Unity 6.3 breaking changes) | Generate a script with a serialized field; verify correct `[field: SerializeField]` usage on auto-props |
| Agent reflection loop with no progress | Phase 3: No-progress detection (3 consecutive errors = escalate) | Inject a permanently-failing compile error; verify escalation fires before 5-retry exhaustion |

---

## Sources

- [When Coding Agents Spiral Into 693 Lines of Hallucinations — Surge HQ](https://surgehq.ai/blog/when-coding-agents-spiral-into-693-lines-of-hallucinations)
- [Stop AI Agent Loops in Autonomous Coding Tasks — Markaicode](https://markaicode.com/fix-ai-agent-looping-autonomous-coding/)
- [The "Loop of Death": Why 90% of Autonomous Agents Fail in Production — Medium](https://medium.com/@sattyamjain96/the-loop-of-death-why-90-of-autonomous-agents-fail-in-production-and-how-we-solved-it-at-e98451becf5f)
- [How to Prevent Infinite Loops and Spiraling Costs in Autonomous Agent Deployments — Codieshub](https://codieshub.com/for-ai/prevent-agent-loops-costs)
- [Unity Manual: Domain Reloading](http://docs.unity3d.com/Manual/domain-reloading.html)
- [Unity Discussions: Async/Await in Editor script](https://discussions.unity.com/t/async-await-in-editor-script/669701)
- [Unity Discussions: Editor often gets stuck at "Reloading Domain"](https://discussions.unity.com/t/editor-often-gets-stuck-at-reloading-domain/1702965)
- [Unity Manual: Upgrade to Unity 6.3 — Breaking Changes](https://docs.unity3d.com/6000.3/Documentation/Manual/UpgradeGuideUnity63.html)
- [Unity Discussions: Planned Breaking Changes in Unity 6.3](https://discussions.unity.com/t/planned-breaking-changes-in-unity-6-3/1646418)
- [Unity Manual: IL2CPP Limitations — Reflection](https://docs.unity3d.com/Manual/scripting-restrictions.html)
- [Unity Discussions: Issues with IL2CPP and Reflection](https://discussions.unity.com/t/issues-with-il2cpp-and-reflection/766318)
- [Unity Security Advisory CVE-2025-59489](https://unity.com/security/sept-2025-01)
- [Ollama VRAM Requirements: Complete 2026 Guide](https://localllm.in/blog/ollama-vram-requirements-for-local-llms)
- [Context Kills VRAM: How to Run LLMs on Consumer GPUs — Medium](https://medium.com/@lyx_62906/context-kills-vram-how-to-run-llms-on-consumer-gpus-a785e8035632)
- [Ollama: Fix VRAM Issues Loading Multiple Models](https://kisonik.una.io/blog/ollama-fix-vram-issues-loading)
- [Optimizing Local LLMs for Low-End Hardware: 8GB GPU Guide — SitePoint](https://www.sitepoint.com/optimizing-local-llms-low-end-hardware-8gb/)
- [psutil documentation — Process management](https://psutil.readthedocs.io/)
- [Python Issue: Cannot cleanly kill a subprocess using high-level asyncio APIs](https://github.com/python/cpython/issues/88050)
- [Agentic AI Pitfalls: Loops, Hallucinations, Ethical Failures — Medium](https://medium.com/@amitkharche14/agentic-ai-pitfalls-loops-hallucinations-ethical-failures-fixes-77bd97805f9f)
- [ICSE 2025: LLMs Meet Library Evolution — Deprecated API Usage in LLM-based Code Completion](https://conf.researchr.org/details/icse-2025/icse-2025-research-track/198/LLMs-Meet-Library-Evolution-Evaluating-Deprecated-API-Usage-in-LLM-based-Code-Comple)
- [LLM Tool-Calling in Production: Rate Limits, Retries, and the Infinite Loop Failure Mode — Medium](https://medium.com/@komalbaparmar007/llm-tool-calling-in-production-rate-limits-retries-and-the-infinite-loop-failure-mode-you-must-2a1e2a1e84c8)

---
*Pitfalls research for: Autonomous Unity game development agent (Jarvis v6.0)*
*Researched: 2026-03-16*
