# Task D Pre-Flight Report: Mission/Activity Transparency

**Date:** 2026-03-07
**Author:** Pre-flight analysis agent
**Scope:** MissionStep progress, expanded activity stream, "Now working on" panel, full lifecycle controls, learning dashboard enrichment

---

## 1. Current State Inventory

### 1.1 MissionRecord (TypedDict in `learning_missions.py`)

```python
class MissionRecord(TypedDict):
    mission_id: str          # "m-20260307142359123456"
    topic: str               # max 200 chars
    objective: str           # max 400 chars
    sources: list[str]       # e.g. ["google", "reddit", "official_docs"]
    status: str              # "pending" | "running" | "completed" | "cancelled" | "failed" | "exhausted"
    origin: str              # "desktop-manual" | "daemon" | "phone"
    created_utc: str         # ISO timestamp
    updated_utc: str         # ISO timestamp
    last_report_path: str    # path to .report.json
    verified_findings: int
    progress_pct: int        # 0-100, currently hardcoded at milestones (0→45→75→90→100)
    status_detail: str       # max 180 chars
    progress_bar: str        # "[████░░░░░░] 45%"
```

### 1.2 Current Progress Model (Hardcoded)

In `run_learning_mission()`:
- Start → 0% ("Queued")
- After `_start_mission()` → status "running" (no explicit pct update)
- After `_fetch_mission_content()` → 45% ("Scanning pages")
- After `_verify_candidates()` → 75% ("Verifying N candidate findings")
- Before finalize → 90% ("Finalizing mission report")
- After finalize → 100% ("Completed" or "Completed with no verified findings")

**Problem:** These are timer-driven, not truth-driven. The design wants real step weights.

### 1.3 Mission Lifecycle (Existing)

| Operation | Function | CQRS Command | API Endpoint |
|-----------|----------|-------------|-------------|
| Create | `create_learning_mission()` | `MissionCreateCommand` | `POST /missions/create` |
| Status | `load_missions()` | `MissionStatusCommand` | `GET /missions/status` |
| Cancel | `cancel_mission()` | `MissionCancelCommand` | (CLI only — no POST endpoint!) |
| Run | `run_learning_mission()` | `MissionRunCommand` | (CLI only) |
| Retry | `retry_failed_missions()` | (none) | (none) |
| Auto-gen | `auto_generate_missions()` | (none) | (none) |

**Key finding:** Cancel has a CQRS command but NO mobile API endpoint. The design says `/missions/stop` exists via cancel — it does NOT exist as an HTTP endpoint currently. Only the CLI path calls cancel via the bus.

### 1.4 Mission File Storage

- Missions stored in `.planning/missions.json` (flat JSON array)
- Reports stored in `.planning/missions/{safe_id}.report.json`
- All mutations use `_MISSIONS_LOCK` (threading.Lock)
- Persistence via `atomic_write_json` (rename pattern)

### 1.5 Activity Feed Events (Current)

The `_log_mission_activity()` function emits `MISSION_STATE_CHANGE` events with:
```python
{
    "mission_id": str,
    "provider": "web_research",
    "step": str,           # max 180 chars
    "progress_pct": int,
    "correlation_id": f"mission-{mission_id}",
    "status": str,
}
```

**Key finding:** `correlation_id` already exists in the mission activity payload! Task D just needs to expand what's in the event.

### 1.6 Widget Status Endpoint (Current)

`GET /widget-status` in `mobile_routes/health.py` returns:
```json
{
    "ok": true,
    "growth": { /* intelligence growth metrics */ },
    "alerts": [],
    "reliability": { /* command reliability panel */ },
    "recent_events": [ /* last 10 non-daemon-cycle events */ ]
}
```

**No `now_working_on` field exists currently.**

### 1.7 Intelligence Dashboard (Current)

`build_intelligence_dashboard()` in `intelligence_dashboard.py` returns:
- `methodology`, `jarvis`, `ranking`, `etas`
- `memory_regression`, `knowledge_graph`, `gateway_audit`
- `learning`, `knowledge_snapshot`, `achievements`

**Missing:** `missions_completed_7d`, `facts_learned_7d`, `top_topics`, `knowledge_graph_growth`, `success_rate_trend`

### 1.8 ActivityCategory Values (18 categories)

```
LLM_ROUTING, FACT_EXTRACTED, CORRECTION_APPLIED, CONSOLIDATION,
REGRESSION_CHECK, DAEMON_CYCLE, PROACTIVE_TRIGGER, HARVEST,
WEB_RESEARCH, VOICE, ERROR, SECURITY, PREFERENCE_LEARNED,
MISSION_STATE_CHANGE, COMMAND_LIFECYCLE, RESOURCE_PRESSURE,
CONVERSATION_STATE, VOICE_PIPELINE
```

No new category needed — `MISSION_STATE_CHANGE` covers step events.

### 1.9 Daemon Loop Mission Integration

In `_run_periodic_subsystems()`:
- Missions run every cycle when `run_missions=True`
- Auto-generation every 50 cycles
- `_run_next_pending_mission()` picks first pending, runs via bus

---

## 2. New Data Structures

### 2.1 MissionStep (New dataclass)

```python
@dataclass
class MissionStep:
    name: str                # "search_web", "verify_candidates", "ingest_findings"
    description: str         # "Searching arXiv for quantum computing papers"
    weight: float = 1.0      # higher for expensive steps
    status: str = "pending"  # "pending" | "running" | "completed" | "failed" | "skipped"
    elapsed_ms: int = 0
    artifacts_produced: int = 0
    started_at: str = ""     # ISO timestamp
    completed_at: str = ""   # ISO timestamp
```

### 2.2 Expanded MissionRecord (Backward-compatible)

Add optional fields to the MissionRecord TypedDict:
```python
class MissionRecord(TypedDict, total=False):
    # Existing fields (total=True semantics via explicit listing)
    mission_id: str
    topic: str
    objective: str
    sources: list[str]
    status: str              # Add: "paused" | "scheduled" | "restarting"
    origin: str
    created_utc: str
    updated_utc: str
    last_report_path: str
    verified_findings: int
    progress_pct: int
    status_detail: str
    progress_bar: str

    # NEW Task D fields
    steps: list[dict[str, Any]]          # Serialized MissionStep list
    current_step_index: int              # Index into steps list
    elapsed_ms: int                      # Total mission elapsed time
    artifact_count: int                  # Total artifacts produced
    correlation_id: str                  # Links to activity stream
    pause_checkpoint: dict[str, Any]     # State at pause point
    schedule_cron: str                   # Cron expression for recurring
    schedule_next_utc: str               # Next scheduled run time
    prior_findings: list[dict[str, Any]] # Preserved from restart
    retries: int                         # Already exists partially
```

**Strategy:** Use `total=False` and provide defaults for all new fields. Old missions without `steps` key will have progress computed as before.

### 2.3 Expanded Activity Event Payload

```python
{
    "mission_id": str,
    "stage": str,              # "search" | "verify" | "ingest" | "complete" | "failed"
    "substep": str,            # "Verifying fact: Earth orbits the Sun"
    "elapsed_ms": int,
    "progress_pct": int,
    "artifact_count": int,
    "current_action": str,     # What the mission is doing RIGHT NOW
    "correlation_id": str,     # "mission-{mission_id}"
    "status": str,
    "step_name": str,          # MissionStep.name
    "step_index": int,         # Position in step list
    "total_steps": int,
}
```

---

## 3. File-by-File Change Map

### 3.1 Modified Files

| File | Changes |
|------|---------|
| `learning_missions.py` | Add `MissionStep` dataclass; refactor `run_learning_mission()` to declare steps; add step-driven progress calc; add `pause_mission()`, `resume_mission()`, `restart_mission()`, `schedule_mission()`; expand `_log_mission_activity()` payload; add `get_mission_steps()`, `get_mission_artifacts()`, `get_active_missions()` |
| `activity_feed.py` | No structural changes needed (payload is freeform dict) |
| `commands/ops_commands.py` | Add `MissionPauseCommand`, `MissionResumeCommand`, `MissionRestartCommand`, `MissionScheduleCommand`, `MissionStepsCommand`, `MissionArtifactsCommand`, `MissionActiveCommand` + result dataclasses |
| `handlers/ops_handlers.py` | Add `MissionPauseHandler`, `MissionResumeHandler`, `MissionRestartHandler`, `MissionScheduleHandler`, `MissionStepsHandler`, `MissionArtifactsHandler`, `MissionActiveHandler` |
| `app.py` | Register 7 new mission command→handler pairs in `_register_ops_handlers()` |
| `mobile_api.py` | Add GET routes: `/missions/{id}/steps`, `/missions/{id}/artifacts`, `/missions/active`; Add POST routes: `/missions/stop`, `/missions/restart`, `/missions/pause`, `/missions/resume`, `/missions/schedule` |
| `mobile_routes/command.py` | Add handler methods for all new mission endpoints |
| `mobile_routes/health.py` | Expand `_handle_get_widget_status()` to include `now_working_on` panel |
| `mobile_routes/intelligence.py` | Expand `_gather_intelligence_growth()` to include `missions_completed_7d`, `facts_learned_7d`, `top_topics_learned`, `knowledge_graph_growth`, `mission_success_rate_trend` |
| `intelligence_dashboard.py` | Add mission/learning enrichment metrics to `build_intelligence_dashboard()` |
| `daemon_loop.py` | Add scheduled mission cron check in periodic subsystems; potentially add "now working on" tracking |

### 3.2 New Files

None required. All changes fit within existing modules.

---

## 4. New CQRS Commands

### 4.1 Commands to Add (in `commands/ops_commands.py`)

```python
@dataclass(frozen=True)
class MissionPauseCommand:
    mission_id: str

@dataclass
class MissionPauseResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MissionResumeCommand:
    mission_id: str

@dataclass
class MissionResumeResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MissionRestartCommand:
    mission_id: str
    preserve_findings: bool = True

@dataclass
class MissionRestartResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MissionScheduleCommand:
    mission_id: str          # Existing mission to make recurring
    cron_expression: str     # e.g. "0 8 * * 1" (every Monday at 8am)

@dataclass
class MissionScheduleResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MissionStepsCommand:
    mission_id: str

@dataclass
class MissionStepsResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    mission_id: str = ""
    message: str = ""

@dataclass(frozen=True)
class MissionArtifactsCommand:
    mission_id: str

@dataclass
class MissionArtifactsResult:
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    mission_id: str = ""
    message: str = ""

@dataclass(frozen=True)
class MissionActiveCommand:
    pass

@dataclass
class MissionActiveResult:
    missions: list[dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    message: str = ""
```

### 4.2 Registration Pattern (in `app.py`)

Add to `_register_ops_handlers()`:
```python
bus.register(MissionPauseCommand, MissionPauseHandler(root).handle)
bus.register(MissionResumeCommand, MissionResumeHandler(root).handle)
bus.register(MissionRestartCommand, MissionRestartHandler(root).handle)
bus.register(MissionScheduleCommand, MissionScheduleHandler(root).handle)
bus.register(MissionStepsCommand, MissionStepsHandler(root).handle)
bus.register(MissionArtifactsCommand, MissionArtifactsHandler(root).handle)
bus.register(MissionActiveCommand, MissionActiveHandler(root).handle)
```

---

## 5. New Mobile API Endpoints

### 5.1 Dynamic Path Routing

The current mobile_api.py uses static dispatch dicts (`_GET_DISPATCH`, `_POST_DISPATCH`). Dynamic paths like `/missions/{id}/steps` require a pattern-matching fallback.

**Approach:** Add a `_match_dynamic_route()` method that checks prefix patterns after the static dispatch miss. This is the cleanest way without adding a routing library:

```python
# In do_GET, after static dispatch miss:
# Check dynamic routes
dynamic_handler = self._match_dynamic_get(path)
if dynamic_handler:
    dynamic_handler()
    return
```

### 5.2 New GET Endpoints

| Endpoint | Handler Method | Auth |
|----------|---------------|------|
| `GET /missions/{id}/steps` | `_handle_get_mission_steps` | HMAC |
| `GET /missions/{id}/artifacts` | `_handle_get_mission_artifacts` | HMAC |
| `GET /missions/active` | `_handle_get_missions_active` | HMAC |

Add `/missions/active` to the static `_GET_DISPATCH` dict.

### 5.3 New POST Endpoints

| Endpoint | Handler Method | Auth |
|----------|---------------|------|
| `POST /missions/stop` | `_handle_post_missions_stop` | HMAC |
| `POST /missions/restart` | `_handle_post_missions_restart` | HMAC |
| `POST /missions/pause` | `_handle_post_missions_pause` | HMAC |
| `POST /missions/resume` | `_handle_post_missions_resume` | HMAC |
| `POST /missions/schedule` | `_handle_post_missions_schedule` | HMAC |

Add all 5 to `_POST_DISPATCH` dict (static routes, no path params needed — mission_id in body).

### 5.4 Endpoint Response Shapes

**GET /missions/{id}/steps:**
```json
{
    "ok": true,
    "mission_id": "m-...",
    "steps": [
        {
            "name": "search_web",
            "description": "Searching web for quantum computing",
            "weight": 1.0,
            "status": "completed",
            "elapsed_ms": 12340,
            "artifacts_produced": 5
        }
    ],
    "progress_pct": 67
}
```

**GET /missions/{id}/artifacts:**
```json
{
    "ok": true,
    "mission_id": "m-...",
    "artifacts": [
        {
            "statement": "...",
            "source_urls": [...],
            "confidence": 0.85
        }
    ],
    "total": 5
}
```

**GET /missions/active:**
```json
{
    "ok": true,
    "missions": [
        {
            "mission_id": "...",
            "topic": "...",
            "status": "running",
            "progress_pct": 45,
            "current_step": "Verifying candidates",
            "elapsed_ms": 30000
        }
    ]
}
```

---

## 6. Backward Compatibility Strategy

### 6.1 MissionRecord Schema Evolution

**Problem:** Existing missions in `missions.json` lack `steps`, `correlation_id`, `pause_checkpoint`, etc.

**Strategy:** All new fields are optional with defaults:
```python
steps = mission.get("steps", [])
current_step_index = mission.get("current_step_index", -1)
elapsed_ms = mission.get("elapsed_ms", 0)
```

### 6.2 Progress Computation Fallback

```python
def compute_progress(mission: dict) -> int:
    steps = mission.get("steps", [])
    if not steps:
        # Legacy mission — return stored progress_pct
        return int(mission.get("progress_pct", 0))
    # Step-driven progress
    total_weight = sum(s.get("weight", 1.0) for s in steps)
    if total_weight <= 0:
        return 0
    completed_weight = sum(
        s.get("weight", 1.0) for s in steps
        if s.get("status") in ("completed", "skipped")
    )
    return min(100, int(completed_weight / total_weight * 100))
```

### 6.3 API Response Compatibility

All existing endpoints (`GET /missions/status`, `POST /missions/create`) continue to work unchanged. New fields are additive.

### 6.4 Status Field Expansion

Current statuses: `pending`, `running`, `completed`, `cancelled`, `failed`, `exhausted`
New statuses: `paused`, `scheduled`, `restarting`

**Impact on existing code:**
- `_run_next_pending_mission()` only picks `status == "pending"` → safe
- `cancel_mission()` checks `_NON_CANCELLABLE = ("completed", "cancelled", "exhausted")` → add `"scheduled"` is debatable; paused missions SHOULD be cancellable
- `retry_failed_missions()` only picks `status == "failed"` → safe
- `auto_generate_missions()` checks pending_count → safe

### 6.5 Widget-Status `now_working_on`

When no mission is running, return `"now_working_on": null`. Widget must handle null gracefully (already required by design doc).

---

## 7. Implementation Order

### Phase 1: Core Data Model (Build First)
1. **MissionStep dataclass** in `learning_missions.py`
2. **Step declaration in `run_learning_mission()`** — define steps upfront, track progress through them
3. **Step-driven progress computation** — replace hardcoded percentages
4. **Expanded `_log_mission_activity()`** — add stage, substep, elapsed_ms, etc.
5. **Backward-compat `compute_progress()` helper** for old missions

### Phase 2: Lifecycle Controls
6. **`pause_mission()`** — set status to "paused", save checkpoint (current step index, partial findings)
7. **`resume_mission()`** — restore from checkpoint, continue from paused step
8. **`restart_mission()`** — re-queue with prior findings preserved
9. **`schedule_mission()`** — add cron expression, daemon checks it
10. **Helper queries:** `get_mission_steps()`, `get_mission_artifacts()`, `get_active_missions()`

### Phase 3: CQRS Wiring
11. **New commands** in `commands/ops_commands.py`
12. **New handlers** in `handlers/ops_handlers.py`
13. **Registration** in `app.py`

### Phase 4: API Endpoints
14. **New POST endpoints** in `mobile_routes/command.py`
15. **New GET endpoints** (static + dynamic path matching)
16. **Update dispatch dicts** in `mobile_api.py`

### Phase 5: Dashboard Integration
17. **`now_working_on` in widget-status** — query active missions, pick the first running one
18. **Learning dashboard enrichment** — missions_completed_7d, facts_learned_7d, etc.
19. **Intelligence dashboard additions** — top_topics, knowledge_graph_growth, success_rate_trend

### Phase 6: Daemon Integration
20. **Scheduled mission cron check** in `_run_periodic_subsystems()`
21. **Ensure daemon auto-run skips paused/scheduled missions**

### Phase 7: Tests + Verification
22. **Unit tests** for all new functions
23. **Integration tests** for API endpoints
24. **Backward compat tests** (old missions without steps)
25. **Thread safety tests** for concurrent pause/resume
26. **Full test suite run** + ruff lint

---

## 8. Gotcha List with Mitigations

### G1: MissionRecord Backward Compatibility (CRITICAL)
**Issue:** Old missions in `missions.json` lack `steps`, `current_step_index`, etc.
**Mitigation:** All new fields accessed via `.get()` with defaults. `compute_progress()` checks for `steps` key — falls back to stored `progress_pct`.

### G2: Activity Feed Write Contention During Rapid Step Updates (MEDIUM)
**Issue:** Step transitions fire activity events rapidly. The `ActivityFeed._lock` serializes all writes. The `_auto_prune()` runs on every insert.
**Mitigation:**
- Batch step events: only emit on status transitions (pending→running, running→completed), not on progress tick updates.
- Use `_update_mission_progress()` which already debounces via the mission lock.
- Consider a minimum interval between activity events (e.g., 2 seconds).

### G3: Widget-Status Must Handle null `now_working_on` (LOW)
**Issue:** When idle, `now_working_on` is null.
**Mitigation:** Return `"now_working_on": null` explicitly. Widget code and mobile app must check for null before rendering. This is a frontend contract.

### G4: Pause/Resume Must Checkpoint Mission State (HIGH)
**Issue:** Pausing mid-execution (e.g., during web fetches) is complex because `run_learning_mission()` uses `ThreadPoolExecutor`.
**Mitigation:**
- **Simple approach:** Pause only works on `pending` or between steps. A running step completes before pause takes effect.
- Store checkpoint: `{"step_index": N, "partial_findings": [...], "scanned_urls": [...]}` in the mission record.
- Resume picks up from the saved step_index.
- **Do NOT try to interrupt a ThreadPoolExecutor mid-fetch.** Instead, check cancellation/pause flag between steps.

### G5: Schedule Must Handle Cron Parsing (MEDIUM)
**Issue:** Cron expression parsing is non-trivial. Need a lightweight parser.
**Mitigation:**
- Use stdlib-compatible approach: simple daily/weekly/monthly presets + raw cron.
- Option A: Add `croniter` dependency (already popular, small).
- Option B: Implement minimal cron matching for common patterns (0 H * * D).
- **Recommendation:** Check if `croniter` is installed. If not, support only preset schedules ("daily", "weekly", "monthly") mapped to fixed cron expressions. Parse the next run time manually for these simple cases.

### G6: `/missions/restart` Must Preserve Prior Findings (HIGH)
**Issue:** Restart re-queues a completed/failed mission. Prior verified findings must survive.
**Mitigation:**
- On restart: copy `verified_findings` list to `prior_findings` field.
- Reset steps, progress_pct, status to "pending".
- On next run: merge prior + new findings, dedup by statement hash.
- The report file is preserved (new run creates a new report).

### G7: Thread Safety for New Mission States (MEDIUM)
**Issue:** New operations (pause, resume, restart, schedule) all write to `missions.json` under `_MISSIONS_LOCK`. Concurrent daemon auto-run + mobile API calls could contend.
**Mitigation:**
- All operations already use `_MISSIONS_LOCK` — safe by design.
- Pause must set a flag that `run_learning_mission()` checks between steps. Use a module-level `_PAUSE_REQUESTED: set[str]` protected by the same lock.
- The lock is held only during JSON read/modify/write (~milliseconds) — contention is minimal.

### G8: Dynamic URL Routing for `/missions/{id}/steps` (MEDIUM)
**Issue:** Current mobile_api.py uses static dict dispatch. Path parameters aren't supported.
**Mitigation:**
- Add a fallback in `do_GET` after static dispatch miss: check if path starts with `/missions/` and parse the mission_id and sub-resource.
- Pattern: `re.match(r"^/missions/([^/]+)/(steps|artifacts)$", path)`
- This is minimal and doesn't require a routing framework.

### G9: Existing `MissionCancelCommand` Has No API Endpoint (LOW)
**Issue:** Cancel exists as CQRS command but has no `POST /missions/stop` or `POST /missions/cancel` endpoint.
**Mitigation:** The design calls for `POST /missions/stop`. Wire it to `MissionCancelCommand` (which calls `cancel_mission()`). This is straightforward.

### G10: `_gather_active_missions()` in intelligence.py Uses Wrong Path (LOW)
**Issue:** `_gather_active_missions()` reads from `_runtime_dir(root) / "learning_missions.json"` — but missions are actually stored at `root / ".planning" / "missions.json"` (via `_missions_path()`).
**Mitigation:** Fix the path to use the correct location, or better yet, use `load_missions(root)` directly.

### G11: MissionStep elapsed_ms Tracking (MEDIUM)
**Issue:** Need to track per-step elapsed time. `run_learning_mission()` currently doesn't timestamp each phase.
**Mitigation:** Wrap each step in a context manager or helper that records `time.monotonic()` start/end and computes elapsed_ms. Store in the step dict.

### G12: `retries` Field Already Exists Partially (LOW)
**Issue:** `retry_failed_missions()` already uses `mission.get("retries", 0)`. Not in the TypedDict.
**Mitigation:** Add `retries: int` to the expanded MissionRecord TypedDict. No functional change needed.

### G13: croniter Dependency (MEDIUM)
**Issue:** `croniter` is not currently installed in the project.
**Mitigation:** Check `pyproject.toml` / `requirements.txt` for existing scheduling libs. If none, implement basic day-of-week matching for "daily"/"weekly"/"monthly" presets without adding a dependency. A full cron parser can be added later if needed.

### G14: Intelligence Dashboard Enrichment Data Sources (MEDIUM)
**Issue:** Some enrichment metrics need data that isn't currently tracked:
- `missions_completed_7d`: Need to count missions with status "completed" and `updated_utc` in last 7 days.
- `facts_learned_7d`: Can derive from KG node count delta (already in `_gather_kg_metrics`).
- `top_topics_learned`: Need to scan completed missions' topics.
- `knowledge_graph_growth`: Node/edge delta (already in KG metrics trend).
- `success_rate_trend`: Need to compute from mission status history over 4 weeks.
**Mitigation:** All data is available from `missions.json` + KG metrics history. No new data collection needed — just computation at query time.

---

## 9. Test Plan

### 9.1 Unit Tests for `learning_missions.py` Changes

| Test | Description |
|------|-------------|
| `test_mission_step_creation` | Create MissionStep, verify fields |
| `test_step_driven_progress_computation` | Steps with weights → correct percentage |
| `test_backward_compat_progress_no_steps` | Old mission without steps → uses stored pct |
| `test_run_mission_with_steps` | Full run creates steps, tracks progress |
| `test_pause_mission` | Pause running mission → status "paused", checkpoint saved |
| `test_pause_non_running_mission` | Pause pending/completed → error |
| `test_resume_mission` | Resume paused mission → status "pending" |
| `test_resume_non_paused_mission` | Resume running → error |
| `test_restart_mission` | Restart failed → preserves findings, resets steps |
| `test_restart_running_mission` | Restart running → error (must cancel first) |
| `test_schedule_mission` | Set cron, verify next_utc |
| `test_get_mission_steps` | Query steps for mission |
| `test_get_mission_artifacts` | Query artifacts from report |
| `test_get_active_missions` | Filter running/paused missions |
| `test_expanded_activity_payload` | Verify all new fields in activity events |
| `test_concurrent_pause_resume` | Thread safety for pause/resume |
| `test_mission_cancel_paused` | Cancel a paused mission → success |

### 9.2 Unit Tests for CQRS Commands/Handlers

| Test | Description |
|------|-------------|
| `test_mission_pause_handler` | Dispatch pause → correct result |
| `test_mission_resume_handler` | Dispatch resume → correct result |
| `test_mission_restart_handler` | Dispatch restart → findings preserved |
| `test_mission_schedule_handler` | Dispatch schedule → cron stored |
| `test_mission_steps_handler` | Dispatch steps query → list returned |
| `test_mission_artifacts_handler` | Dispatch artifacts query → list returned |
| `test_mission_active_handler` | Dispatch active query → filtered list |

### 9.3 API Endpoint Tests

| Test | Description |
|------|-------------|
| `test_post_missions_stop` | HMAC auth + cancel → 200 |
| `test_post_missions_pause` | HMAC auth + pause → 200 |
| `test_post_missions_resume` | HMAC auth + resume → 200 |
| `test_post_missions_restart` | HMAC auth + restart → 200 |
| `test_post_missions_schedule` | HMAC auth + schedule → 200 |
| `test_get_missions_steps` | Dynamic path → steps returned |
| `test_get_missions_artifacts` | Dynamic path → artifacts returned |
| `test_get_missions_active` | Active missions → filtered list |
| `test_widget_status_now_working_on` | Running mission → now_working_on populated |
| `test_widget_status_idle` | No running mission → now_working_on null |

### 9.4 Dashboard Enrichment Tests

| Test | Description |
|------|-------------|
| `test_missions_completed_7d` | Count completed missions in window |
| `test_facts_learned_7d` | KG node delta in window |
| `test_top_topics_learned` | Topic extraction from completed missions |
| `test_knowledge_graph_growth` | Node/edge growth metrics |
| `test_success_rate_trend` | 4-week rolling average computation |

### 9.5 Integration / Edge Case Tests

| Test | Description |
|------|-------------|
| `test_old_mission_format_loads` | missions.json without new fields → no crash |
| `test_mixed_old_new_missions` | Some with steps, some without → both work |
| `test_pause_between_steps` | Pause flag checked between step transitions |
| `test_restart_preserves_findings` | Findings from prior run carried forward |
| `test_schedule_daemon_pickup` | Scheduled mission auto-runs at correct time |
| `test_concurrent_mission_operations` | Multi-thread pause+resume+cancel stress |

---

## 10. Dependency Check

### External Libraries Needed

| Library | Needed For | Status |
|---------|-----------|--------|
| `croniter` | Cron expression parsing | **NOT INSTALLED** — implement basic presets or add dependency |

### Internal Module Dependencies (New)

| Consumer | Dependency | Type |
|----------|-----------|------|
| `learning_missions.py` | `activity_feed.py` | Already exists (lazy import) |
| `mobile_routes/command.py` | `commands/ops_commands.py` | New imports for 7 commands |
| `mobile_routes/health.py` | `learning_missions.py` | New import for `now_working_on` |
| `mobile_routes/intelligence.py` | `learning_missions.py` | New import for dashboard enrichment |
| `handlers/ops_handlers.py` | `learning_missions.py` | New imports for 7 handlers |
| `daemon_loop.py` | `learning_missions.py` | Already exists |

No new circular dependency risks. All new imports follow the existing lazy-import pattern.

---

## 11. Summary

Task D is a **medium-complexity, high-surface-area** change touching ~11 files with ~7 new CQRS commands, ~8 new API endpoints, and significant refactoring of the mission execution pipeline. The most complex parts are:

1. **Step-driven progress** — requires refactoring `run_learning_mission()` internals
2. **Pause/Resume** — requires inter-step checkpoint mechanism
3. **Dynamic URL routing** — requires fallback pattern in mobile_api.py
4. **Dashboard enrichment** — requires aggregation queries across missions.json and KG metrics

The safest implementation order starts with the core data model (MissionStep, backward compat), then lifecycle controls, then API wiring, and finally dashboard integration. Each phase can be tested independently.

**Critical path:** MissionStep → step-driven progress → expanded activity payload → CQRS commands → API endpoints → widget panel → dashboard enrichment → daemon schedule check → tests.
