# Cross-Module Dependency & Integration Analysis

**Date:** 2026-03-07
**Scope:** Tasks A, C, D, E, F, H — cross-module impacts on 35+ engine modules

---

## 1. Complete Module Inventory

### Top-level modules (engine/src/jarvis_engine/)

| Module | Description |
|--------|-------------|
| `__init__.py` | Package init |
| `_shared.py` | Shared utilities (now_iso, load_json, atomic_write, personal_vocab) |
| `_protocols.py` | Type protocols |
| `_fts_utils.py` | FTS5 query sanitization helpers |
| `_db_pragmas.py` | SQLite PRAGMA config + `connect_db` helper (WAL mode, busy_timeout) |
| `_constants.py` | Environment variable readers, model selection, privacy detection |
| `_compat.py` | Python compatibility shims (UTC, etc.) |
| `_cli_helpers.py` | CLI output formatting helpers |
| `_bus.py` | Lazy CommandBus singleton cache |
| `app.py` | **DI composition root** — creates CommandBus, wires all 70+ handlers |
| `command_bus.py` | CommandBus implementation with AppContext |
| `config.py` | `repo_root()` utility |
| `main.py` | CLI entrypoint (~3000 lines), all CLI commands |
| `daemon_loop.py` | Daemon cycle logic, mission auto-run, periodic subsystems |
| `activity_feed.py` | **SQLite-backed activity feed** — 18 categories, singleton |
| `conversation_state.py` | **[Task A]** Cross-LLM context continuity state machine, singleton |
| `voice_pipeline.py` | **[Task C touched]** Web-augmented LLM conversation, history, model tracking |
| `voice_telemetry.py` | **[Task C]** Full pipeline latency telemetry + SLO enforcement, singleton |
| `voice_intents.py` | Intent routing dispatch (large if/elif chain) |
| `voice_context.py` | Smart context building, system prompt assembly |
| `voice_extractors.py` | Phone/URL/weather extraction, text cleaning |
| `voice_auth.py` | Voice enrollment/verification |
| `voice.py` | Voice backend management |
| `stt.py` | Speech-to-text orchestration |
| `stt_vad.py` | Silero VAD detector, singleton |
| `stt_backends.py` | STT backend implementations |
| `stt_postprocess.py` | STT transcript post-processing |
| `wakeword.py` | Wake word detection |
| `brain_memory.py` | Context packet building for LLM prompts |
| `memory_store.py` | Memory store facade |
| `memory_snapshots.py` | Memory snapshot/restore |
| `ingest.py` | Ingest pipeline entry point |
| `auto_ingest.py` | Auto-ingestion for conversations |
| `temporal.py` | Temporal reasoning |
| `task_orchestrator.py` | Task orchestration |
| `router.py` | Intent routing logic |
| `resilience.py` | Self-heal report and execution |
| `runtime_control.py` | Runtime control (pause/resume engine) |
| `process_manager.py` | PID file management |
| `persona.py` | Persona management |
| `policy.py` | Policy enforcement |
| `owner_guard.py` | Owner authentication guard |
| `phone_guard.py` | Phone spam guard |
| `scam_hunter.py` | Scam detection |
| `desktop_widget.py` | Tkinter desktop widget + orb animation |
| `widget_tray.py` | System tray icon |
| `widget_orb.py` | Orb visual state machine |
| `widget_helpers.py` | Widget HTTP helpers |
| `widget_conversation.py` | Widget conversation panel |
| `web_research.py` | Web research orchestration |
| `web_fetch.py` | Web page fetching + search |
| `mobile_api.py` | HMAC-authenticated HTTP server on :8787 |
| `ops_sync.py` | Ops file sync |
| `ops_autopilot.py` | Autonomous ops execution |
| `intelligence_dashboard.py` | Intelligence metrics dashboard |
| `life_ops.py` | Life operations module |
| `learning_missions.py` | Mission creation, execution, cancellation |
| `harvest_discovery.py` | Knowledge harvest discovery |
| `growth_tracker.py` | Growth tracking metrics |
| `gaming_mode.py` | Gaming mode toggle |
| `connectors.py` | External connectors |
| `capability.py` | Capability inventory |
| `cli_ops.py` | CLI ops commands |
| `cli_knowledge.py` | CLI knowledge commands |
| `automation.py` | Automation framework |
| `api_contracts.py` | API contract types |
| `adapters.py` | Media adapters (Image/Video/3D) |

### Subpackages

| Package | Modules | Description |
|---------|---------|-------------|
| `memory/` | engine, embeddings, classify, search, ingest, tiers, migration | SQLite+FTS5+sqlite-vec memory |
| `knowledge/` | graph, facts, locks, contradictions, llm_extractor, entity_resolver, regression, _base | Knowledge graph (NetworkX+SQLite) |
| `gateway/` | models, classifier, costs, pricing, audit, cli_providers | LLM routing (Ollama/Anthropic/Groq/Gemini) |
| `learning/` | engine, preferences, feedback, usage_patterns, temporal, relevance, metrics, cross_branch, correction_detector, consolidator, _tracker_base | Conversation learning |
| `harvesting/` | harvester, providers, budget, session_ingestors | Multi-provider knowledge harvesting |
| `proactive/` | triggers, self_test, notifications, kg_metrics, cost_tracking, alert_queue | Proactive intelligence |
| `security/` | 17 modules (orchestrator, threat_detector, forensic_logger, etc.) | Full security suite |
| `sync/` | engine, changelog, transport, auto_sync | Mobile-desktop sync |
| `handlers/` | 12 handler files | CQRS command handlers |
| `commands/` | 12 command files + base.py | CQRS command dataclasses |
| `mobile_routes/` | auth, command, data, health, intelligence, scam, security, sync, _helpers | Mobile API route handlers |
| `news/` | interests | News interest tracking |

**Total: ~160 Python files across 35+ active modules**

---

## 2. Dependency Graph — Task A (`conversation_state.py`)

### What conversation_state imports:
```
jarvis_engine._shared          → now_iso()
jarvis_engine.config           → repo_root()
jarvis_engine._db_pragmas      → connect_db() (lazy, in ConversationTimeline)
jarvis_engine.activity_feed    → ActivityCategory, log_activity (lazy, in telemetry methods)
```

### What imports conversation_state (5 consumers):
```
voice_pipeline.py              → get_conversation_state() — 5 call sites:
                                  mark_model_switch, update_turn (user+assistant),
                                  get_prompt_injection, atexit flush
gateway/cli_providers.py       → get_conversation_state() — checkpoint on compaction
daemon_loop.py                 → get_conversation_state() — startup init + shutdown save
mobile_routes/intelligence.py  → get_conversation_state() — GET /conversation/state endpoint
```

### Circular dependency risk: **NONE**
- conversation_state → activity_feed (lazy import inside methods)
- activity_feed does NOT import conversation_state
- voice_pipeline → conversation_state (lazy import inside methods)
- conversation_state does NOT import voice_pipeline
- All cross-references use lazy imports inside try/except blocks — safe pattern.

---

## 3. Dependency Graph — Task C (voice_telemetry.py + voice_pipeline.py changes)

### What voice_telemetry imports:
```
jarvis_engine.activity_feed    → ActivityCategory, log_activity (lazy, in emit methods)
```

### What imports voice_telemetry (2 consumers):
```
voice_pipeline.py              → get_voice_telemetry(), mark_stage() — 3 call sites
mobile_routes/intelligence.py  → get_voice_telemetry() — GET /voice/latency endpoint
```

### What voice_pipeline imports (top-level):
```
jarvis_engine._bus             → get_bus()
jarvis_engine._shared          → env_int()
jarvis_engine.auto_ingest      → auto_ingest_memory()
jarvis_engine.brain_memory     → build_context_packet
jarvis_engine.command_bus      → CommandBus
jarvis_engine.commands.learning_commands → LearnInteractionCommand
jarvis_engine.commands.task_commands     → QueryCommand, QueryResult
jarvis_engine.config           → repo_root()
jarvis_engine._constants       → env/model/privacy helpers
jarvis_engine.voice_extractors → re-exports (PHONE_NUMBER_RE, URL_RE, etc.)
jarvis_engine.voice_context    → re-exports (_build_smart_context, etc.)
jarvis_engine.voice_intents    → re-exports (cmd_voice_run_impl)
```

### What imports voice_pipeline (5 consumers):
```
voice_intents.py               → import jarvis_engine.voice_pipeline as _vp
voice_context.py               → import jarvis_engine.voice_pipeline as _vp (2 sites)
mobile_routes/command.py       → import jarvis_engine.voice_pipeline as _vp_mod
main.py                        → from voice_pipeline import (many symbols)
handlers/proactive_handlers.py → from voice_pipeline import cmd_voice_run_impl
cli_ops.py                     → from voice_pipeline import escape_response
cli_knowledge.py               → from voice_pipeline import escape_response
```

### Circular dependency risk: **MANAGED (via lazy imports)**
- voice_pipeline ↔ voice_intents: voice_pipeline re-exports from voice_intents; voice_intents imports voice_pipeline lazily inside functions
- voice_pipeline ↔ voice_context: same pattern
- voice_pipeline → main.py (lazy: `from jarvis_engine.main import cmd_voice_say`)
- This is safe because all cycles use lazy imports, but adding more top-level imports to voice_pipeline could break things.

---

## 4. Cross-Task Conflict Matrix

### Task A (Context Continuity) vs Task D (Mission Transparency)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **ConversationStateManager vs MissionStep state** | **LOW** | These are orthogonal state machines. ConversationStateManager tracks LLM conversation context; MissionStep tracks research mission progress. They don't share data structures. |
| **Activity feed contention** | **MEDIUM** | Both Task A (entity_extraction, continuity_reconstruction events) and Task D (mission_state_change, step progress events) write to the same ActivityFeed singleton. The feed uses `threading.Lock` for serialization — safe but could create contention under heavy load. |
| **`/widget-status` expansion** | **LOW** | Task D adds `now_working_on` to widget-status. Task A's conversation state is served via a separate `/conversation/state` endpoint. No collision. |
| **daemon_loop integration** | **MEDIUM** | Both need daemon_loop hooks. Task A already has startup/shutdown hooks. Task D needs mission step progress reporting in daemon_loop auto-run. Risk: interleaved code changes in the same daemon_loop functions. |

### Task A (Context Continuity) vs Task E (Self-Diagnosis)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Mission health checks** | **LOW** | Task E's DiagnosticEngine checks "stuck missions" which uses learning_missions.py state. This doesn't interact with conversation_state at all. |
| **Auto-fix: clear_stuck_missions** | **LOW** | Only touches learning_missions.py mission records (JSON file). No intersection with conversation_state. |
| **CQRS command registration** | **LOW** | Task E adds DiagnosticRunCommand, DiagnosticApproveCommand. These are new command types — no conflict with existing commands. |

### Task A (Context Continuity) vs Task F (Memory Hygiene)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **anchor_entities protection** | **HIGH** | Task F's anti-loss guardrail says "Records referenced in conversation_state anchor_entities are protected from cleanup." This requires Task F to **read** Task A's anchor_entities. This creates a **cross-module dependency**: memory hygiene must import conversation_state to check anchor references. |
| **Race condition: cleanup vs active state** | **HIGH** | If memory hygiene runs cleanup while a conversation is active, it must check anchor_entities atomically. The ConversationStateManager uses RLock — the hygiene module can safely call `get_conversation_state().snapshot.anchor_entities` to get a defensive copy, but timing matters. |
| **Memory DB concurrent access** | **MEDIUM** | Memory hygiene will soft-delete/archive records in the same SQLite DB that conversation_state's timeline queries. WAL mode allows concurrent reads, but write contention could occur if both modules write simultaneously. |

### Task C (Voice Telemetry) vs Task D (Mission Transparency)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Activity feed contention** | **LOW** | Voice telemetry emits events every 100 utterances. Mission step events are less frequent. Unlikely to contend. |
| **Widget display** | **LOW** | Voice telemetry drives orb state. Mission transparency drives a separate "now working on" panel. No visual conflict. |

### Task C (Voice Telemetry) vs Task E (Self-Diagnosis)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Voice pipeline health check** | **MEDIUM** | Task E's DiagnosticEngine checks "voice pipeline health: STT backend availability, VAD state, microphone accessibility." It will need to **read** from voice_telemetry's singleton. This is a new dependency E → C. |
| **No auto-fix conflict** | **LOW** | Task E doesn't auto-fix voice issues — it's read-only for voice pipeline status. |

### Task D (Mission Transparency) vs Task E (Self-Diagnosis)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Mission health states** | **MEDIUM** | Task E checks "stuck missions (running > 10min)." Task D introduces new mission states (paused, scheduled). Task E must understand these new states to avoid false-positive "stuck" detection. If Task D adds "paused" state, Task E must not flag paused missions as stuck. |
| **Auto-fix: clear_stuck_missions** | **MEDIUM** | Task E's auto-fix cancels missions running > 30 min. Task D's pause/resume means a paused mission could be "old" without being stuck. The auto-fix logic must check for paused state. |
| **Implementation order** | **HIGH** | Task D must land BEFORE Task E so DiagnosticEngine can reason about the full mission state machine. If E lands first, its mission health checks will need retroactive updates when D changes mission states. |

### Task D (Mission Transparency) vs Task F (Memory Hygiene)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Active mission context protection** | **MEDIUM** | Task F's anti-loss guardrail says "Active mission context (mission_id referenced)" is protected. Task D changes what "active" means (adds paused, scheduled states). The hygiene module must understand D's expanded state set. |

### Task E (Self-Diagnosis) vs Task F (Memory Hygiene)

| Conflict Area | Risk Level | Details |
|--------------|------------|---------|
| **Concurrent DB maintenance** | **HIGH** | Task E's auto-fix includes vacuum_db and rebuild_fts. Task F's cleanup pipeline soft-deletes and archives records. If both run concurrently: E vacuums the DB while F is writing deletes → potential corruption or lock timeout. **Must be serialized.** |
| **Memory pressure** | **LOW** | Both check and act on memory pressure, but from different angles. E monitors overall health; F cleans up content. They complement each other. |

---

## 5. Concurrency / Race Condition Risks

### 5.1 Module-level Singletons (shared mutable state)

| Singleton | Module | Lock Type | Risk |
|-----------|--------|-----------|------|
| `_conversation_state` | conversation_state.py | `threading.Lock` (creation) + `threading.RLock` (mutations) | **LOW** — properly double-checked locking, RLock for reentrant access |
| `_telemetry` | voice_telemetry.py | `threading.Lock` (creation) + `threading.Lock` (mutations) | **LOW** — per-thread `threading.local()` for in-flight data |
| `_feed` | activity_feed.py | `threading.Lock` (creation) + `threading.Lock` (DB ops) | **MEDIUM** — all DB ops serialized through single lock; high-frequency writers could contend |
| `_state` | voice_pipeline.py | `threading.RLock` (history) + `threading.Lock` (model) | **LOW** — proper lock separation |
| `_vad_instance` | stt_vad.py | `threading.Lock` | **LOW** |
| `_cached_bus` | _bus.py | `threading.Lock` | **LOW** |
| `_MISSIONS_LOCK` | learning_missions.py | `threading.Lock` | **MEDIUM** — file-level lock for missions.json; concurrent daemon auto-gen + mobile API creates |
| `_personal_vocab_lock` | _shared.py | `threading.Lock` | **LOW** |

### 5.2 Specific Race Conditions

1. **Activity Feed Write Contention** (MEDIUM)
   - conversation_state, voice_telemetry, daemon_loop, learning_missions, gateway/models all write to the same ActivityFeed singleton
   - All go through one `threading.Lock` — serialized but could become a bottleneck
   - **Mitigation:** Already using SQLite WAL mode, but the Python lock serializes all writes

2. **Memory DB + Timeline DB Concurrent Access** (MEDIUM)
   - MemoryEngine uses `memory.db` with write_lock + db_lock
   - ConversationTimeline uses `conversation_timeline.db` (separate file)
   - ActivityFeed uses `activity_feed.db` (separate file)
   - **No cross-DB contention** — each has its own connection and locks

3. **Missions JSON File Lock** (MEDIUM)
   - learning_missions.py uses `_MISSIONS_LOCK` for missions.json
   - Daemon auto-run, mobile API /missions/create, and CLI all compete
   - Task D's new states (pause/resume/restart) add more write paths
   - **Mitigation:** Already serialized via file-level lock, but more operations means more contention

4. **Conversation State Save During Turn Update** (LOW)
   - `update_turn()` acquires RLock, mutates snapshot, then calls `save()` outside the lock
   - `save()` acquires its own lock for JSON serialization
   - Between releasing RLock and acquiring save lock, another thread could modify snapshot
   - **Actual risk is negligible** because save() re-acquires the lock and copies the snapshot atomically

5. **Task F + Task E Concurrent Maintenance** (HIGH)
   - If DiagnosticEngine's vacuum_db runs while MemoryHygiene is mid-cleanup:
     - VACUUM acquires exclusive lock on entire DB
     - Ongoing deletes will get SQLITE_BUSY
   - **Must serialize:** Task E's maintenance auto-fixes and Task F's cleanup must share a mutex or be scheduled non-overlapping

---

## 6. Resource Contention Points

### 6.1 SQLite Databases (6 separate files)

| Database | File | Used By | Write Lock |
|----------|------|---------|------------|
| Memory DB | `memory.db` | MemoryEngine, learning trackers, knowledge graph, sync, harvesting | `MemoryEngine._write_lock` (shared) |
| Security DB | `security.db` | SecurityOrchestrator, all defense handlers | Dedicated `threading.Lock` in app.py |
| Activity Feed DB | `activity_feed.db` | ActivityFeed singleton | `ActivityFeed._lock` |
| Conversation Timeline DB | `conversation_timeline.db` | ConversationTimeline | `ConversationTimeline._lock` |
| Gateway Costs DB | (in memory.db path) | CostTracker | Internal lock |
| Harvesting Budget DB | (in memory.db path) | BudgetManager | Internal lock |

**Key contention: Memory DB** — shared by MemoryEngine, 3 learning trackers (preferences, feedback, usage_patterns), knowledge graph, sync engine, and harvesting pipeline. All writers share `MemoryEngine._write_lock`. Task F's cleanup and Task E's vacuum_db will add more write pressure.

### 6.2 Embedding Model

- Loaded once in `EmbeddingService.__init__()`, warmed in background thread
- Used by MemoryEngine (ingest), KnowledgeGraph (search), IntentClassifier (routing), and potentially Task F (similarity classification)
- **Risk:** The model inference is CPU-bound. If Task F's "Pass 2 — Embedding-based similarity classifier" runs during normal operations, it could starve voice pipeline's intent classification.
- **Mitigation:** Task F should batch embedding calls during low-activity windows.

### 6.3 File-based State

| File | Used By | Lock |
|------|---------|------|
| `missions.json` | learning_missions.py | `_MISSIONS_LOCK` |
| `conversation_state.json` | conversation_state.py | `ConversationStateManager._lock` + atomic write |
| `conversation_history.json` | voice_pipeline.py | `ConversationState._conversation_history_lock` |
| Various `.jsonl` audit logs | security, diagnostics | Append-only (low contention) |

---

## 7. Recommendations for Safe Integration (Ranked by Impact)

### CRITICAL (must address before implementation)

1. **Serialize Task E auto-fixes with Task F cleanup** (Impact: prevents DB corruption)
   - Create a shared `maintenance_lock` in a common location (e.g., `_shared.py`)
   - Both DiagnosticEngine.vacuum_db and MemoryHygieneCommand must acquire this lock
   - Alternative: run both through the CQRS bus with a `MemoryMaintenanceCommand` that serializes internally

2. **Task F must read Task A's anchor_entities safely** (Impact: prevents data loss)
   - Use `get_conversation_state().snapshot.anchor_entities` (returns defensive copy)
   - Do NOT cache the entities set — always read fresh before each cleanup batch
   - Add a test: create anchor entities, run hygiene, verify they survive

3. **Implement Task D before Task E** (Impact: prevents rework)
   - Task E's mission health checks must understand D's expanded state machine (paused, scheduled, restarting)
   - If E lands first, it will flag paused missions as "stuck" — false positives

### HIGH (should address during implementation)

4. **Activity feed write batching for Task D** (Impact: reduces contention)
   - Mission step events could fire rapidly during mission execution
   - Consider batching: accumulate step events and flush every N seconds or N events
   - The ActivityFeed._auto_prune runs on every insert — could be slow with rapid writes

5. **Task E's DiagnosticEngine must read voice_telemetry read-only** (Impact: clean API)
   - Voice pipeline health check should use `get_voice_telemetry().get_health_summary()`
   - Do not access internal state — use only public query methods

6. **Task D: MissionStep class must NOT conflict with MissionRecord** (Impact: backward compatibility)
   - Current `MissionRecord` is a TypedDict with `progress_pct`, `status`, `progress_bar`
   - MissionStep is a new concept (substeps within a mission)
   - Store steps as a nested list within MissionRecord, don't replace existing fields
   - Backward compat: existing `/missions/status` must still work with old-format missions

### MEDIUM (good practice)

7. **New CQRS commands for Tasks D and E** (Impact: clean architecture)
   - Task D needs: `MissionPauseCommand`, `MissionResumeCommand`, `MissionRestartCommand`, `MissionScheduleCommand`
   - Task E needs: `DiagnosticRunCommand`, `DiagnosticApproveCommand`
   - Register in `app.py` following the existing pattern: commands/ + handlers/ + app.py registration

8. **Task F's memory hygiene should be a CQRS command** (Impact: consistency)
   - `MemoryHygieneCommand` dispatched via bus, not called directly
   - This ensures it goes through the same infrastructure as other maintenance operations

9. **Conversation state's SQLite timeline DB should use the same `_db_pragmas.connect_db`** (Impact: consistency)
   - Already does this ✓ (uses `connect_db` in ConversationTimeline.__init__)

10. **No new module-level singletons without double-checked locking** (Impact: thread safety)
    - Task E's DiagnosticEngine and Task F's HygieneEngine should follow the same singleton pattern as conversation_state and voice_telemetry

### LOW (nice to have)

11. **Add integration tests for cross-task scenarios**
    - Test: conversation active + hygiene runs → anchor entities preserved
    - Test: mission paused + diagnostic runs → not flagged as stuck
    - Test: vacuum_db + hygiene cleanup → no DB errors

12. **Consider separating ActivityFeed writes by priority**
    - High-priority: security events, errors
    - Low-priority: entity_extraction, mission step progress
    - Could use a queue with background flusher to reduce lock contention

---

## 8. Summary of Key Findings

| Finding | Severity | Affected Tasks |
|---------|----------|----------------|
| Task F must safely read Task A's anchor_entities for anti-loss guardrails | CRITICAL | A ↔ F |
| Task E vacuum_db and Task F cleanup can corrupt DB if concurrent | CRITICAL | E ↔ F |
| Task D must land before Task E to avoid false-positive mission health alerts | HIGH | D → E |
| Activity feed is a write contention bottleneck for all new tasks | MEDIUM | A, C, D, E |
| Memory DB is shared by 6+ subsystems; new writers from E/F add pressure | MEDIUM | E, F |
| Embedding model CPU contention if Task F runs similarity classification during peak | MEDIUM | F |
| No circular dependencies exist; all cross-references use lazy imports | OK | All |
| All singletons use proper double-checked locking | OK | A, C |
| Task A and C are already fully integrated and working | OK | A, C |
| MissionPause/Resume/Restart/Schedule commands don't exist yet — must be created for D | INFO | D |
| DiagnosticEngine/self_diagnosis module doesn't exist yet — new module for E | INFO | E |
| No signal_quality column or MemoryHygiene module exists yet — new for F | INFO | F |
