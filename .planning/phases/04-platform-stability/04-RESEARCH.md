# Phase 4: Platform Stability - Research

**Researched:** 2026-03-02
**Domain:** Python application initialization, error handling, CQRS patterns, proactive diagnostics
**Confidence:** HIGH

## Summary

Phase 4 addresses five stability issues in the Jarvis desktop engine: a db_path.exists() gate in `app.py` that silently disables the entire brain subsystem on first run, silent except blocks across four modules that swallow errors and make debugging impossible, the MemoryConsolidator being inaccessible through the CQRS command bus, proactive triggers that produce no diagnostic output when connector data is empty, and a test coverage gap (currently 4152 tests, target 4200+).

All findings have been verified by direct source code inspection. The fixes are well-scoped: remove the exists() gate (MemoryEngine._init_schema already creates all tables), add logging to ~7 silent blocks across 3 modules, add a ConsolidateCommand/Handler/Result trio following the existing learning_commands pattern, add diagnostic output to ProactiveCheckHandler when snapshot data is empty, and write ~50+ new tests.

**Primary recommendation:** Fix the db_path.exists() gate first (highest impact, simplest change), then wire MemoryConsolidator into CQRS, then add logging to silent blocks, then proactive diagnostics, then fill test gaps.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STAB-01 | Fix db_path.exists() gate -- create database on first run instead of silently disabling brain | Finding 1: Lines 215, 254, 507 in app.py. MemoryEngine._init_schema (line 82 of engine.py) creates all tables via CREATE IF NOT EXISTS. Remove the gate, always initialize. |
| STAB-02 | Add logging to all silent except blocks in desktop_widget.py (at least non-UI-lifecycle blocks) | Finding 2: 7 non-UI-lifecycle silent blocks identified across learning_missions.py (3 blocks), learning/engine.py (2 blocks), learning/metrics.py (1 block). Desktop widget has ~50+ try/except blocks but most are legitimate UI-lifecycle catches. |
| STAB-03 | MemoryConsolidator exposed through CQRS command bus for CLI and mobile API access | Finding 3: ConsolidateCommand/ConsolidateResult/ConsolidateHandler pattern documented. Currently only callable from daemon loop (main.py line 2625). |
| STAB-04 | Proactive triggers show diagnostic when no connector data available (not silent empty) | Finding 4: When snapshot data contains empty arrays (medications=[], bills=[], etc.), all trigger rules return empty lists silently. ProactiveCheckHandler returns "No alerts." with no diagnostic context. |
| STAB-05 | All functions verified end-to-end with passing tests (target: 4200+ tests) | Finding 5: Currently 4152 tests. Need 48+ new tests across the stability fixes and under-tested areas. |
</phase_requirements>

## Finding 1: db_path.exists() Gate (STAB-01)

**Confidence:** HIGH (verified by direct source inspection)

### Location
`engine/src/jarvis_engine/app.py` -- function `create_app()`

### The Problem

Three `db_path.exists()` checks gate critical subsystem initialization:

**Line 215** -- Main brain gate:
```python
if db_path.exists():
    try:
        from jarvis_engine.memory.classify import BranchClassifier
        from jarvis_engine.memory.embeddings import EmbeddingService
        from jarvis_engine.memory.engine import MemoryEngine
        from jarvis_engine.memory.ingest import EnrichedIngestPipeline
        from jarvis_engine.knowledge.graph import KnowledgeGraph

        embed_service = EmbeddingService()
        engine = MemoryEngine(db_path, embed_service=embed_service)
        # ... entire brain subsystem ...
```

When `db_path.exists()` is False (first run), ALL of these remain None:
- `engine` (MemoryEngine)
- `embed_service` (EmbeddingService)
- `pipeline` (EnrichedIngestPipeline)
- `kg` (KnowledgeGraph)

This cascades to disable:
- All memory handlers (BrainStatus, BrainContext, Ingest, etc.)
- Learning subsystem (line 400: `if engine is None: raise RuntimeError(...)`)
- Knowledge graph commands
- Cross-branch query
- Sync engine (line 453: `if engine is not None`)
- Intent classifier (line 264: `if embed_service is not None`)
- Cost tracker (line 254)
- Budget manager (line 501-507)

**Line 254** -- CostTracker gate:
```python
if db_path.exists():
    cost_tracker = CostTracker(db_path)
```

**Line 507** -- BudgetManager gate:
```python
if db_path.exists():
    budget_manager = BudgetManager(db_path)
```

### Why the Gate is Unnecessary

`MemoryEngine.__init__()` (engine.py line 38-75) calls `self._init_schema()` (line 82) which uses `CREATE TABLE IF NOT EXISTS` and `CREATE VIRTUAL TABLE IF NOT EXISTS` for all tables. SQLite's `sqlite3.connect(str(db_path))` creates the file if it does not exist.

Therefore: removing the `db_path.exists()` check and always initializing MemoryEngine is safe -- it will create the DB file and all tables on first run.

### Proposed Fix

```python
# BEFORE (line 215):
if db_path.exists():
    try:
        # ... brain initialization ...

# AFTER:
try:
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline
    from jarvis_engine.knowledge.graph import KnowledgeGraph

    embed_service = EmbeddingService()
    engine = MemoryEngine(db_path, embed_service=embed_service)
    # ... rest stays the same ...
```

Same pattern for lines 254 and 507 -- remove the `if db_path.exists()` wrapper, always create CostTracker and BudgetManager (they also use sqlite3.connect which creates files).

### Test Strategy

- Test that `create_app()` with a non-existent db_path still produces a bus with non-None `_engine`, `_embed_service`, `_kg`
- Test that the db file is created on disk after `create_app()` returns
- Test that the created DB has all expected tables

## Finding 2: Silent Except Blocks (STAB-02)

**Confidence:** HIGH (verified by direct source inspection)

### Categorization

#### Non-UI-Lifecycle Silent Blocks (MUST ADD LOGGING)

**learning_missions.py line 440:**
```python
# Source 1: Recent user query summaries
try:
    rows = conn.execute("""SELECT summary FROM records ...""").fetchall()
    # ... topic extraction ...
except Exception:
    pass  # <-- SILENT: swallows DB errors during mission topic discovery
```

**learning_missions.py line 463:**
```python
# Source 2: KG nodes with low edge count
try:
    sparse = conn.execute("""SELECT n.label FROM kg_nodes n ...""").fetchall()
    # ... knowledge gap detection ...
except Exception:
    pass  # <-- SILENT: swallows DB errors during KG gap analysis
```

**learning_missions.py line 489:**
```python
# Source 3: Strong KG areas that could be deepened
try:
    strong = conn.execute("""SELECT n.label, COUNT(e.edge_id) ...""").fetchall()
    # ... topic deepening ...
except Exception:
    pass  # <-- SILENT: swallows DB errors during KG strength analysis
```

**learning/engine.py lines 98-101:**
```python
# After correction detection activity feed logging
except ImportError:
    pass  # <-- SEMI-LEGITIMATE: activity_feed module may not exist
```

**learning/engine.py lines 100-103:**
```python
# Outer correction detector import
except ImportError:
    pass  # <-- SEMI-LEGITIMATE: correction_detector module may not exist
```

These two in learning/engine.py are `ImportError`-only catches, which is a standard graceful-degradation pattern. However, they should at minimum use `logger.debug()` so the missing module can be diagnosed when troubleshooting.

**learning/metrics.py line 81:**
```python
# Temporal distribution from kg_nodes
except Exception:
    # Column may not exist if migration has not run yet
    pass  # <-- NEEDS LOGGING: swallows ALL exceptions, comment says it's for missing column
```

This should catch a more specific exception (e.g., `sqlite3.OperationalError`) and log at debug level for other exceptions.

#### UI-Lifecycle Catches in desktop_widget.py (LEGITIMATE -- do NOT change)

The desktop_widget.py has approximately 50+ try/except blocks. The vast majority fall into these legitimate categories:

1. **Tkinter TclError catches** (~20 blocks): Handling widget-destroyed race conditions during shutdown. These are standard Tkinter patterns -- widgets may be destroyed between the check and the use.
   - Example: lines 968, 983-984, 1004-1005, 1612, 1675-1676, 1876-1877, 1907-1908, 1921-1922, 2023-2024, 2043-2044, 2050-2051, 2475-2476, 2880-2881

2. **Widget-destroyed guards** (~10 blocks): `except Exception: pass` where comment says "Widget destroyed" or "Widget may be destroyed". These protect against cross-thread calls to a destroyed Tkinter root.
   - Example: lines 1144-1145, 1152-1153, 1160-1161, 1167-1168, 1884-1885, 2059-2060, 2270-2271, 2483-2484, 2651-2652, 2657-2658, 2670-2671, 2695-2696, 2708-2709, 2776-2777, 2890-2891, 2900-2901, 2910-2911, 2961-2962, 3065-3066

3. **Best-effort operations** (~5 blocks): Operations that are acceptable to silently fail.
   - Example: line 1600 "Best-effort clear", line 2738 "Parsing intelligence is best-effort"

4. **DPAPI encryption** (~4 blocks): DPAPI may not be available on all platforms.
   - Example: lines 162, 185, 255, 276, 293

5. **Already-has-logging blocks** (~10 blocks): Exception handlers that already call `logger.debug()` or `logger.warning()`.
   - Example: lines 605, 850, 905, 910, 919, 923, 989, 1010, 1020, 1175, 2528, 2751, 2772

**Non-UI blocks in desktop_widget.py that may need attention:**

- Line 220/226: `_auto_heal_stale_ip` -- catches generic Exception during URL probe. Already follows a reasonable pattern (try saved URL, try localhost, keep saved if neither works). Could add debug logging but low priority.
- Line 2528/2744: Health poll loop -- catches Exception during HTTP request. These are in a polling loop and already have appropriate fallback behavior.

**Recommendation:** Focus logging additions on the 3 blocks in `learning_missions.py` and the 1 block in `learning/metrics.py`. The `ImportError` catches in `learning/engine.py` are acceptable but should get `logger.debug()` calls.

### Proposed Fix Pattern

```python
# BEFORE (learning_missions.py line 440):
except Exception:
    pass

# AFTER:
except Exception as exc:
    logger.debug("Failed to extract topics from recent user queries: %s", exc)
```

## Finding 3: MemoryConsolidator CQRS Exposure (STAB-03)

**Confidence:** HIGH (verified by direct source inspection)

### Current State

MemoryConsolidator is currently only invoked from the daemon loop in `main.py` (lines 2622-2663):

```python
# --- Memory consolidation (every 50 cycles) ---
if cycles % 50 == 0:
    try:
        from jarvis_engine.learning.consolidator import MemoryConsolidator
        bus = _get_daemon_bus()
        engine = getattr(bus, "_engine", None)
        # ... builds MemoryConsolidator ad-hoc ...
        consolidator = MemoryConsolidator(engine, gateway=gateway, embed_service=embed_svc)
        result = consolidator.consolidate()
```

This means:
- CLI users cannot trigger consolidation manually
- Mobile API cannot trigger consolidation
- No consolidation if daemon is not running

### Proposed CQRS Structure

Following the existing pattern from `learning_commands.py` and `learning_handlers.py`:

**Command (add to `commands/learning_commands.py`):**
```python
@dataclass(frozen=True)
class ConsolidateMemoryCommand:
    """Trigger memory consolidation of episodic records into semantic facts."""
    branch: str = ""          # Restrict to specific branch (empty = all)
    max_groups: int = 20      # Max groups to process
    dry_run: bool = False     # Compute clusters but don't write


@dataclass
class ConsolidateMemoryResult:
    groups_found: int = 0
    records_consolidated: int = 0
    new_facts_created: int = 0
    errors: list = field(default_factory=list)
    message: str = ""
```

**Handler (add to `handlers/learning_handlers.py`):**
```python
class ConsolidateMemoryHandler:
    """Delegates ConsolidateMemoryCommand to MemoryConsolidator."""

    def __init__(
        self, root: Path, engine: Any = None,
        gateway: Any = None, embed_service: Any = None, kg: Any = None,
    ) -> None:
        self._root = root
        self._engine = engine
        self._gateway = gateway
        self._embed_service = embed_service
        self._kg = kg

    def handle(self, cmd: ConsolidateMemoryCommand) -> ConsolidateMemoryResult:
        if self._engine is None:
            return ConsolidateMemoryResult(
                message="MemoryEngine not available."
            )

        from jarvis_engine.learning.consolidator import MemoryConsolidator

        # Backup KG state before consolidation
        if self._kg is not None:
            try:
                from jarvis_engine.knowledge.regression import RegressionChecker
                rc_checker = RegressionChecker(self._kg)
                rc_checker.backup_graph(tag="pre-consolidation")
            except Exception as exc:
                logger.warning("KG backup before consolidation failed: %s", exc)

        consolidator = MemoryConsolidator(
            self._engine,
            gateway=self._gateway,
            embed_service=self._embed_service,
        )
        result = consolidator.consolidate(
            branch=cmd.branch or None,
            max_groups=cmd.max_groups,
            dry_run=cmd.dry_run,
        )

        # Log to activity feed
        try:
            from jarvis_engine.activity_feed import log_activity, ActivityCategory
            log_activity(
                ActivityCategory.CONSOLIDATION,
                f"Memory consolidation: {result.new_facts_created} facts from {result.groups_found} groups",
                {"groups_found": result.groups_found, "records_consolidated": result.records_consolidated},
            )
        except Exception:
            pass

        return ConsolidateMemoryResult(
            groups_found=result.groups_found,
            records_consolidated=result.records_consolidated,
            new_facts_created=result.new_facts_created,
            errors=result.errors,
            message=f"Consolidated {result.new_facts_created} facts from {result.groups_found} groups."
                    if not result.errors
                    else f"Consolidated {result.new_facts_created} facts with {len(result.errors)} error(s).",
        )
```

**Registration in `app.py`** -- add in the Learning section (after LearnInteractionCommand registration):
```python
bus.register(
    ConsolidateMemoryCommand,
    ConsolidateMemoryHandler(
        root, engine=engine, gateway=gateway,
        embed_service=embed_service, kg=kg,
    ).handle,
)
```

**CLI command in `main.py`:**
```python
def cmd_consolidate_memory(branch: str, max_groups: int, dry_run: bool) -> int:
    result = _get_bus().dispatch(ConsolidateMemoryCommand(
        branch=branch, max_groups=max_groups, dry_run=dry_run,
    ))
    print(f"consolidation_groups={result.groups_found}")
    print(f"consolidation_records={result.records_consolidated}")
    print(f"consolidation_new_facts={result.new_facts_created}")
    if result.errors:
        print(f"consolidation_errors={len(result.errors)}")
        for e in result.errors:
            print(f"  {e}")
    print(f"message={result.message}")
    return 0 if not result.errors else 2
```

**Daemon loop refactor:** Replace the inline consolidation code (lines 2622-2663) with:
```python
if cycles % 50 == 0:
    try:
        bus = _get_daemon_bus()
        result = bus.dispatch(ConsolidateMemoryCommand())
        print(f"consolidation_groups={result.groups_found}")
        print(f"consolidation_new_facts={result.new_facts_created}")
    except Exception as exc:
        print(f"consolidation_error={exc}")
```

## Finding 4: Proactive Trigger Diagnostics (STAB-04)

**Confidence:** HIGH (verified by direct source inspection)

### Current Behavior

The `ProactiveCheckHandler.handle()` method (proactive_handlers.py lines 33-80) loads snapshot data and evaluates trigger rules. When the snapshot file exists but contains empty data (as is the case in `ops_snapshot.live.json`):

```json
{
  "medications": [],
  "bills": [],
  "calendar_events": [],
  "tasks": []
}
```

Each trigger rule receives an empty list and returns `[]`. The handler returns:
```python
ProactiveCheckResult(
    alerts_fired=0,
    alerts="[]",
    message="No alerts."
)
```

This gives the user NO information about WHY there are no alerts -- they cannot tell whether:
1. Connectors are not configured (no data flowing in)
2. Connectors are configured but there are genuinely no alerts
3. The trigger rules failed to execute

### Data Flow

1. **Snapshot population:** `ops_snapshot.live.json` is populated by the `ops-sync` command (main.py). It gathers data from configured connectors (calendar, email, tasks, etc.).
2. **Connector status:** The snapshot includes `connector_statuses` array showing which connectors are configured/ready.
3. **Trigger evaluation:** `ProactiveEngine.evaluate()` calls each rule's `check_fn(snapshot_data)`. When arrays are empty, rules return `[]` and the engine undoes its cooldown reservation.

### Proposed Fix

Add diagnostic context to the `ProactiveCheckResult` when data sources are empty:

```python
def handle(self, cmd: ProactiveCheckCommand) -> ProactiveCheckResult:
    # ... existing snapshot loading ...

    # Evaluate triggers
    alerts = self._engine.evaluate(snapshot_data)

    # Diagnostic: check which data sources are empty
    diagnostics: list[str] = []
    data_sources = {
        "medications": "medication_reminder",
        "bills": "bill_due_alert",
        "calendar_events": "calendar_prep",
        "tasks": "urgent_task_alert",
    }
    for source_key, rule_id in data_sources.items():
        items = snapshot_data.get(source_key, [])
        if not items:
            diagnostics.append(f"{rule_id}: no {source_key} data available")

    # Check connector statuses
    connectors = snapshot_data.get("connector_statuses", [])
    not_ready = [c["name"] for c in connectors if isinstance(c, dict) and not c.get("ready", False)]
    if not_ready:
        diagnostics.append(f"Connectors not ready: {', '.join(not_ready)}")

    # Build message
    if alerts:
        message = f"Fired {len(alerts)} alert(s)."
    elif diagnostics:
        message = "No alerts. Diagnostics:\n" + "\n".join(f"  - {d}" for d in diagnostics)
    else:
        message = "No alerts. All data sources populated."

    return ProactiveCheckResult(
        alerts_fired=len(alerts),
        alerts=json.dumps(alerts_dicts),
        message=message,
    )
```

## Finding 5: Test Coverage Gap Analysis (STAB-05)

**Confidence:** HIGH (verified by pytest --co)

### Current State

- **Total tests:** 4152 (collected)
- **Target:** 4200+ (need 48+ new tests)

### Test Distribution by Area

| Area | Test Count | Status |
|------|-----------|--------|
| Widget (desktop_widget.py) | 185 | Well covered |
| Proactive | 105 | Well covered |
| Test app (app.py) | 64 | Moderate |
| Learning missions | 42 | Moderate |
| Consolidator | 14 | UNDER-TESTED |
| create_app edge cases | 2 | UNDER-TESTED |

### Tests Needed for Phase 4 Changes

**1. db_path.exists() gate removal (~15 tests):**
- `test_create_app_fresh_db_creates_engine` -- verify engine is not None when DB file does not exist
- `test_create_app_fresh_db_creates_file` -- verify .db file is created on disk
- `test_create_app_fresh_db_has_tables` -- verify records, records_fts, schema_version tables exist
- `test_create_app_fresh_db_has_embed_service` -- verify embed_service is not None
- `test_create_app_fresh_db_has_kg` -- verify KnowledgeGraph is wired
- `test_create_app_fresh_db_has_cost_tracker` -- verify CostTracker is created
- `test_create_app_fresh_db_has_budget_manager` -- verify BudgetManager is created
- `test_create_app_fresh_db_learning_subsystem_wired` -- verify learning handlers get real engine
- `test_create_app_fresh_db_sync_subsystem_wired` -- verify sync engine is created
- `test_create_app_fresh_db_ingest_works` -- end-to-end: ingest a record on fresh DB
- `test_create_app_existing_db_still_works` -- regression: existing DB path continues to work
- `test_create_app_fresh_db_brain_status_returns_data` -- BrainStatus returns real data
- Additional edge cases for CostTracker and BudgetManager fresh DB creation

**2. Silent except block logging (~8 tests):**
- `test_auto_generate_missions_logs_db_error_source1` -- verify logger.debug called on Source 1 failure
- `test_auto_generate_missions_logs_db_error_source2` -- verify logger.debug called on Source 2 failure
- `test_auto_generate_missions_logs_db_error_source3` -- verify logger.debug called on Source 3 failure
- `test_metrics_logs_temporal_error` -- verify logger output on temporal query failure
- `test_learning_engine_logs_missing_correction_detector` -- verify logger.debug on ImportError
- `test_learning_engine_logs_missing_activity_feed` -- verify logger.debug on ImportError
- Additional tests for each logging addition

**3. ConsolidateMemoryCommand CQRS (~12 tests):**
- `test_consolidate_command_no_engine` -- returns "not available" message
- `test_consolidate_command_no_records` -- returns 0 groups
- `test_consolidate_command_creates_facts` -- end-to-end with mock records
- `test_consolidate_command_dry_run` -- no writes when dry_run=True
- `test_consolidate_command_branch_filter` -- only processes specified branch
- `test_consolidate_command_max_groups` -- respects max_groups limit
- `test_consolidate_command_kg_backup` -- KG backup called before consolidation
- `test_consolidate_command_activity_feed_logged` -- activity feed receives consolidation event
- `test_consolidate_cli_command` -- CLI arg parsing and dispatch
- `test_consolidate_registered_on_bus` -- ConsolidateMemoryCommand registered in create_app
- `test_consolidate_daemon_uses_bus` -- daemon loop dispatches via bus (not inline)
- `test_consolidate_mobile_api_accessible` -- command works via mobile API dispatch

**4. Proactive diagnostics (~10 tests):**
- `test_proactive_check_empty_medications_diagnostic` -- diagnostic message includes "no medications data"
- `test_proactive_check_empty_bills_diagnostic` -- diagnostic for empty bills
- `test_proactive_check_empty_calendar_diagnostic` -- diagnostic for empty calendar
- `test_proactive_check_empty_tasks_diagnostic` -- diagnostic for empty tasks
- `test_proactive_check_all_empty_diagnostic` -- all sources empty shows all diagnostics
- `test_proactive_check_connectors_not_ready` -- shows connector names
- `test_proactive_check_with_data_no_diagnostic` -- populated data shows "All data sources populated"
- `test_proactive_check_with_alerts_no_diagnostic` -- alerts present = no diagnostic noise
- `test_proactive_check_partial_data` -- some sources empty, some populated
- `test_proactive_check_missing_connector_statuses` -- graceful when connector_statuses key missing

**5. Additional coverage for robustness (~5 tests):**
- Edge cases in existing modules surfaced during development
- Integration tests confirming all STAB requirements pass end-to-end

**Total estimated new tests: ~50**

## Architecture Patterns

### Existing CQRS Command Pattern

All commands follow this structure (from learning_commands.py / learning_handlers.py):

```python
# commands/{domain}_commands.py
@dataclass(frozen=True)
class FooCommand:
    """Docstring describing the command."""
    param1: str = ""
    param2: int = 0

@dataclass
class FooResult:
    output_field: int = 0
    message: str = ""

# handlers/{domain}_handlers.py
class FooHandler:
    def __init__(self, root: Path, engine: Any = None, ...) -> None:
        self._root = root
        self._engine = engine

    def handle(self, cmd: FooCommand) -> FooResult:
        if self._engine is None:
            return FooResult(message="Engine not available.")
        # ... actual logic ...
        return FooResult(output_field=42, message="ok")

# app.py
bus.register(FooCommand, FooHandler(root, engine=engine).handle)

# main.py
def cmd_foo(param1: str, param2: int) -> int:
    result = _get_bus().dispatch(FooCommand(param1=param1, param2=param2))
    print(f"output_field={result.output_field}")
    print(f"message={result.message}")
    return 0
```

### Handler Initialization Pattern

Handlers receive their dependencies at construction time (not at dispatch time). Dependencies that may not be available are typed as `Any = None` and checked at handle() time:

```python
def handle(self, cmd: FooCommand) -> FooResult:
    if self._engine is None:
        return FooResult(message="MemoryEngine not available.")
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CQRS dispatch | Custom event system | Existing CommandBus pattern | Already handles 70+ commands, well-tested |
| DB table creation | Manual CREATE TABLE | MemoryEngine._init_schema() | Already handles all tables, FTS5, sqlite-vec |
| Test structure | New test framework | Existing pytest + mock patterns in engine/tests/ | 4152 tests already follow consistent patterns |

## Common Pitfalls

### Pitfall 1: Breaking Existing Tests When Removing db_path.exists()
**What goes wrong:** Many tests create a temporary directory with no DB file. If create_app() now always tries to initialize MemoryEngine, tests that mock at a higher level may fail.
**Why it happens:** Test fixtures may assume engine=None when no DB exists.
**How to avoid:** The try/except on lines 216-242 already handles initialization failures gracefully. If MemoryEngine or EmbeddingService imports fail (missing dependencies in test env), the fallback path sets everything to None. The key change is removing the `if db_path.exists()` gate, NOT removing the try/except.
**Warning signs:** Tests that explicitly check `bus._engine is None` may need updating.

### Pitfall 2: Circular Import in New Handler
**What goes wrong:** Importing MemoryConsolidator at module level in learning_handlers.py could cause circular imports.
**Why it happens:** MemoryConsolidator imports from memory.engine, which is also used by handlers.
**How to avoid:** Use lazy import inside handle() method, matching the pattern used in the daemon loop (line 2625: `from jarvis_engine.learning.consolidator import MemoryConsolidator`).

### Pitfall 3: Desktop Widget except Blocks Are Load-Bearing
**What goes wrong:** Adding logging to UI-lifecycle except blocks causes log spam every 33ms (animation frame rate) or blocks Tkinter's event loop.
**Why it happens:** Many of these catches run at animation speed (30fps) or during shutdown race conditions.
**How to avoid:** Only modify the learning_missions.py and learning/ module blocks. Leave desktop_widget.py blocks as-is (they are legitimate UI patterns).

### Pitfall 4: Proactive Diagnostic Output Breaking Parsers
**What goes wrong:** Changing the message format in ProactiveCheckResult could break the widget or mobile API parsers that parse the output.
**Why it happens:** The CLI cmd_proactive_check (main.py line 4085-4098) prints `message={result.message}`. Multiline messages could confuse line-oriented parsers.
**How to avoid:** Keep message as single-line summary, add diagnostics as a separate field in ProactiveCheckResult (e.g., `diagnostics: str = ""`).

### Pitfall 5: ConsolidateMemoryCommand vs Daemon Loop Duplication
**What goes wrong:** After wiring ConsolidateMemoryCommand into CQRS, the daemon loop still has the inline consolidation code. Both paths run, doubling consolidation work.
**Why it happens:** Forgetting to refactor the daemon loop to use the new command.
**How to avoid:** Replace the inline daemon consolidation (lines 2622-2663) with a simple `bus.dispatch(ConsolidateMemoryCommand())` call.

## Code Examples

### Example 1: Removing db_path.exists() Gate

```python
# app.py create_app() -- BEFORE line 215
if db_path.exists():
    try:
        # ... brain init ...

# AFTER -- remove the if, keep the try:
try:
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline
    from jarvis_engine.knowledge.graph import KnowledgeGraph

    embed_service = EmbeddingService()
    engine = MemoryEngine(db_path, embed_service=embed_service)
    classifier = BranchClassifier(embed_service)
    kg = KnowledgeGraph(engine, embed_service=embed_service)
    try:
        from jarvis_engine.learning.temporal import migrate_temporal_metadata
        migrate_temporal_metadata(engine._db, engine._write_lock)
    except Exception as exc_tm:
        logger.warning("Temporal metadata migration skipped: %s", exc_tm)
    pipeline = EnrichedIngestPipeline(
        engine, embed_service, classifier, knowledge_graph=kg,
    )
except Exception as exc:
    logger.warning("Failed to initialize MemoryEngine, falling back to adapter shims: %s", exc)
    engine = None
    embed_service = None
    pipeline = None
    kg = None
```

### Example 2: Adding Logging to Silent Block

```python
# learning_missions.py line 440 -- BEFORE:
except Exception:
    pass

# AFTER:
except Exception as exc:
    logger.debug("Topic extraction from recent queries failed: %s", exc)
```

### Example 3: Test for Fresh DB Initialization

```python
def test_create_app_fresh_db_creates_engine(tmp_path):
    """create_app with no existing DB should still initialize MemoryEngine."""
    bus = create_app(tmp_path)
    assert bus._engine is not None, "MemoryEngine should be created even without pre-existing DB"
    assert bus._embed_service is not None
    assert bus._kg is not None
    # Verify DB file was created
    db_path = tmp_path / ".planning" / "brain" / "jarvis_memory.db"
    assert db_path.exists(), "DB file should be created by MemoryEngine"
```

## Open Questions

1. **Should the daemon loop consolidation be fully replaced by CQRS dispatch?**
   - What we know: The daemon loop currently builds MemoryConsolidator inline. The new CQRS handler will duplicate this logic.
   - What's unclear: Whether there are timing/threading concerns with dispatching via bus from daemon loop.
   - Recommendation: Yes, replace inline code with `bus.dispatch(ConsolidateMemoryCommand())`. The bus dispatch is synchronous and safe from the daemon loop context.

2. **Should ProactiveCheckResult gain a new `diagnostics` field?**
   - What we know: Adding multiline content to `message` could break parsers. A separate field is cleaner.
   - What's unclear: Whether the mobile API or widget parse ProactiveCheckResult fields.
   - Recommendation: Add `diagnostics: str = ""` field to ProactiveCheckResult. Keep `message` as single-line summary. Print diagnostics separately in CLI.

## Sources

### Primary (HIGH confidence)
- `engine/src/jarvis_engine/app.py` -- direct inspection of create_app(), lines 193-572
- `engine/src/jarvis_engine/memory/engine.py` -- direct inspection of __init__() and _init_schema(), lines 38-82
- `engine/src/jarvis_engine/learning_missions.py` -- direct inspection of auto_generate_missions(), lines 420-490
- `engine/src/jarvis_engine/learning/engine.py` -- direct inspection of learn_from_interaction(), lines 51-103
- `engine/src/jarvis_engine/learning/metrics.py` -- direct inspection of capture_knowledge_metrics(), lines 68-83
- `engine/src/jarvis_engine/learning/consolidator.py` -- direct inspection of MemoryConsolidator class, lines 52-362
- `engine/src/jarvis_engine/proactive/__init__.py` -- direct inspection of ProactiveEngine.evaluate(), lines 31-117
- `engine/src/jarvis_engine/proactive/triggers.py` -- direct inspection of trigger rules, lines 1-147
- `engine/src/jarvis_engine/handlers/proactive_handlers.py` -- direct inspection of ProactiveCheckHandler
- `engine/src/jarvis_engine/commands/learning_commands.py` -- CQRS command pattern reference
- `engine/src/jarvis_engine/handlers/learning_handlers.py` -- CQRS handler pattern reference
- `engine/src/jarvis_engine/main.py` -- daemon loop (lines 2400-2663), CLI commands (lines 4085-4098)
- `engine/src/jarvis_engine/desktop_widget.py` -- all except blocks catalogued via grep
- `ops_snapshot.live.json` -- actual snapshot data structure showing empty arrays

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` -- STAB-01 through STAB-05 requirement definitions
- `.planning/ROADMAP.md` -- Phase 4 success criteria

## Metadata

**Confidence breakdown:**
- db_path.exists() gate: HIGH -- verified by reading MemoryEngine._init_schema, confirmed CREATE IF NOT EXISTS
- Silent except blocks: HIGH -- all blocks verified by line-number inspection
- CQRS pattern: HIGH -- follows exact existing pattern from 70+ registered commands
- Proactive diagnostics: HIGH -- ProactiveCheckHandler and trigger rules fully inspected
- Test coverage: HIGH -- pytest --co verified current count of 4152

**Research date:** 2026-03-02
**Valid until:** 2026-04-02 (stable codebase, internal patterns unlikely to change)
