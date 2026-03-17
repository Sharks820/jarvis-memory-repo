---
phase: 20-infrastructure-foundations
plan: 02
subsystem: agent
tags: [agent, state-store, tool-registry, kg-seeder, unity63, sqlite, tdd]
dependency_graph:
  requires: []
  provides:
    - AgentStateStore (crash-safe SQLite checkpointing for agent tasks)
    - ToolRegistry (pluggable tool discovery with JSON Schema descriptors)
    - Unity 6.3 KG seed data (API, breaking changes, error patterns)
    - kg_seeder (idempotent seeder calling KnowledgeGraph.add_fact)
  affects:
    - Phase 22 (tool execution -- ToolRegistry is the discovery contract)
    - Phase 23 (code generation -- KG seed prevents Unity 6.3 API hallucination)
tech_stack:
  added: []
  patterns:
    - Dataclass with __post_init__ for constraint enforcement (is_destructive forces requires_approval)
    - INSERT OR REPLACE pattern for idempotent SQLite upserts
    - Protocol-based callable typing for async tool execute functions
    - importlib-free data loading via Path(__file__).parent.parent / "data"
key_files:
  created:
    - engine/src/jarvis_engine/agent/__init__.py
    - engine/src/jarvis_engine/agent/state_store.py
    - engine/src/jarvis_engine/agent/tool_registry.py
    - engine/src/jarvis_engine/agent/kg_seeder.py
    - engine/src/jarvis_engine/data/unity_kg_seed/unity63_api.json
    - engine/src/jarvis_engine/data/unity_kg_seed/unity63_breaking.json
    - engine/src/jarvis_engine/data/unity_kg_seed/unity63_errors.json
    - engine/tests/test_agent_state_store.py
    - engine/tests/test_tool_registry.py
    - engine/tests/test_kg_seeder.py
  modified: []
decisions:
  - AgentStateStore accepts existing sqlite3.Connection (never opens its own) to stay consistent with MemoryEngine shared-connection pattern
  - ToolSpec.validate and estimate_cost use module-level default functions (not lambdas) to avoid pickle issues and keep dataclass fields simple
  - is_unity_kg_seeded queries kg._db directly with LIKE match on sources column rather than a separate sentinel table
metrics:
  duration: ~20 minutes
  completed: 2026-03-17T05:48:53Z
  tasks_completed: 2
  tests_added: 35
  files_created: 10
  files_modified: 0
---

# Phase 20 Plan 02: Agent Infrastructure Foundations Summary

AgentStateStore with SQLite crash-safe checkpointing, ToolRegistry with JSON Schema tool discovery, and Unity 6.3 KG seed data (40 API + 12 breaking + 12 error entries) powering the agent's anti-hallucination knowledge base.

## Tasks Completed

### Task 1: AgentStateStore and ToolRegistry

**AgentStateStore** (`engine/src/jarvis_engine/agent/state_store.py`):
- `AgentTask` dataclass with all fields: task_id, goal, status, plan_json, step_index, checkpoint_json, token_budget, tokens_used, error_count, last_error, approval_needed
- `AgentStateStore(db: sqlite3.Connection)` -- accepts existing connection, never opens its own
- `_ensure_schema()` creates `agent_tasks` table and status index via `CREATE TABLE IF NOT EXISTS`
- `checkpoint(task)` -- INSERT OR REPLACE with all fields
- `load(task_id)` -- returns AgentTask or None
- `list_by_status(status)` -- filters by status column
- `delete(task_id)` -- returns True if row was deleted

**ToolRegistry** (`engine/src/jarvis_engine/agent/tool_registry.py`):
- `ToolCallable` Protocol for async tool execute functions
- `ToolSpec` dataclass with `__post_init__` enforcing `is_destructive=True => requires_approval=True`
- `register()`, `get()`, `list_tools()`, `schemas_for_prompt()`
- `schemas_for_prompt()` returns `[{name, description, parameters, requires_approval}]` for LLM injection
- Default `validate()` returns True, default `estimate_cost()` returns 0.0

**Tests:** 23 tests across test_agent_state_store.py and test_tool_registry.py -- all passing

### Task 2: Unity 6.3 KG Seed Data and Seeder

**Seed files** (`engine/src/jarvis_engine/data/unity_kg_seed/`):
- `unity63_api.json` -- 40 entries: GameObject, Transform, MonoBehaviour lifecycle (Awake/Start/Update/FixedUpdate/LateUpdate/OnDestroy/OnEnable/OnDisable), Rigidbody, Collider callbacks, Camera, Light, Material, Renderer, Input legacy + InputSystem, SceneManager, Resources, Coroutines, SerializeField, RequireComponent, AssetDatabase, EditorWindow, Physics.Raycast, Vector3.Lerp
- `unity63_breaking.json` -- 12 entries: Experimental.* removal, [field: SerializeField] syntax, URP compat API removal, Physics callback signatures, Input deprecation, WWW removal, EditorGUILayout changes, Mesh.vertices deprecation, AssetDatabase.Refresh, Physics2D, ShaderGraph
- `unity63_errors.json` -- 12 entries: CS0117/CS0619/CS0246/CS0103/CS0029/CS1061/CS0428/CS0234/CS0120/CS0535/CS0115/CS7036 mapped to Unity 6.3 fix patterns

**kg_seeder.py** (`engine/src/jarvis_engine/agent/kg_seeder.py`):
- `seed_unity_kg(kg)` -- loads all three JSON files, calls `kg.add_fact()` for each entry, returns count (64 total)
- `is_unity_kg_seeded(kg)` -- checks `kg._db` for `sources LIKE '%unity63_kg_seed_v1%'`
- Idempotent: re-seeding calls `add_fact()` again (KG's own INSERT OR REPLACE handles deduplication)

**Tests:** 12 tests in test_kg_seeder.py -- all passing

## Verification

```
35/35 new tests passing
6074 total tests passing (up from 5979 baseline + 95 from Phase 20 work)
ruff check clean on all new files
```

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check

### Files Verified
- engine/src/jarvis_engine/agent/__init__.py: FOUND
- engine/src/jarvis_engine/agent/state_store.py: FOUND
- engine/src/jarvis_engine/agent/tool_registry.py: FOUND
- engine/src/jarvis_engine/agent/kg_seeder.py: FOUND
- engine/src/jarvis_engine/data/unity_kg_seed/unity63_api.json: FOUND (40 entries)
- engine/src/jarvis_engine/data/unity_kg_seed/unity63_breaking.json: FOUND (12 entries)
- engine/src/jarvis_engine/data/unity_kg_seed/unity63_errors.json: FOUND (12 entries)
- engine/tests/test_agent_state_store.py: FOUND
- engine/tests/test_tool_registry.py: FOUND
- engine/tests/test_kg_seeder.py: FOUND

### Commits Verified
- 905584f8: feat(20-02): AgentStateStore and ToolRegistry with tests
- a35329b9: feat(20-02): Unity 6.3 KG seed data and idempotent seeder with tests

## Self-Check: PASSED
