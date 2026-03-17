# Phase 20: Infrastructure Foundations - Research

**Researched:** 2026-03-17
**Domain:** VRAM coordination, process lifecycle management, Unity 6.3 knowledge graph seeding, agent state persistence, pluggable tool registry
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| UNITY-06 | VRAM coordinator prevents OOM when Ollama and Unity share 8GB GPU | GPU mutex pattern, VRAM budget math, `nvidia-smi` monitoring approach all documented |
| KNOW-01 | Unity 6.3 API reference, patterns, and common errors seeded into knowledge graph | KG write API (graph.py), Unity 6.3 breaking changes catalog identified, seed script pattern established |
| TOOL-01 | Pluggable tool registry with standard interface (execute, validate, estimate_cost) | Dataclass-based registry pattern, JSON Schema descriptor approach, existing CQRS integration path all clear |
| AGENT-04 | Agent checkpoints state to SQLite before each tool call for crash recovery | MemoryEngine schema extension pattern, SQLite `agent_tasks` table design, three-layer checkpoint model documented |

</phase_requirements>

---

## Summary

Phase 20 builds the four load-bearing foundations for the entire v6.0 Unity Agent system before any Unity or bridge code is written. All four requirements are self-contained — none depends on the Unity Editor Bridge (Phase 21) or the Core Agent Loop (Phase 22). Each one blocks later work if skipped: VRAM OOM crashes are immediate and silent on first combined Ollama+Unity use; orphaned Unity processes hold project locks that can only be resolved with process tree kills; Unity API hallucinations cause compounding errors before Phase 23 code generation begins; and a missing checkpoint store means any crash loses the entire agent task state.

Every implementation in this phase builds directly on existing Jarvis infrastructure. The VRAM coordinator extends the existing gaming mode pattern. The Unity process manager replicates the `_ollama_started_by_widget` + `ops/process_manager.py` pattern already in use for Ollama. The KG seeding uses the existing `knowledge/graph.py` upsert path. The `AgentStateStore` adds a single new SQLite table using the existing schema versioning convention. The `ToolRegistry` is a pure Python dataclass registry with zero new pip dependencies. This phase adds no new pip packages.

**Primary recommendation:** Build in this order — (1) VRAMCoordinator, (2) UnityProcessManager, (3) KG seeder script, (4) AgentStateStore + SQLite schema, (5) ToolRegistry + ToolSpec protocol, (6) CQRS command stubs. Each item can be tested in isolation without Unity installed.

---

## Standard Stack

### Core — All Existing, No New Dependencies

| Library/Module | Version/Location | Purpose | Why Standard |
|----------------|-----------------|---------|--------------|
| `ops/process_manager.py` | Existing Jarvis module | PID file management, process kill | Already used for daemon/mobile_api/widget; replicate exact pattern for Unity |
| `knowledge/graph.py` | Existing Jarvis module | KG node/edge upsert, FTS5 + vector search | Source of truth for all Jarvis domain knowledge; Unity 6.3 API goes here |
| `memory/engine.py` | Existing Jarvis module | SQLite schema, `schema_version` table, `INSERT OR IGNORE` migration pattern | `agent_tasks` table follows identical schema extension pattern |
| `commands/base.py` | Existing Jarvis module | `ResultBase` dataclass with `return_code` + `message` | All new command result classes inherit from this |
| `app.py` CQRS bus | Existing Jarvis module | Command registration with lazy-import handler factories | Three new agent commands registered here following exact existing pattern |
| `subprocess` stdlib | Python stdlib | `taskkill /f /t /pid` for Windows process tree kill | Already used in widget.py for Ollama cleanup |
| `asyncio` stdlib | Python stdlib | `asyncio.create_subprocess_exec` for nvidia-smi polling | Already used in daemon for async work |
| `dataclasses` stdlib | Python stdlib | `ToolSpec` and `AgentTask` dataclasses | Matches all existing Jarvis command/result patterns |

### No New pip Dependencies

This phase adds **zero** new pip packages. All capabilities come from:
- Existing Jarvis modules
- Python standard library (`subprocess`, `asyncio`, `dataclasses`, `json`, `sqlite3`, `threading`)
- `nvidia-smi` CLI (already available on the RTX 4060 Ti system)

The two v6.0 pip additions (`websockets>=14.0`, `tripo3d==0.3.12`) are deferred to Phases 21 and 24 respectively.

---

## Architecture Patterns

### Recommended Project Structure

```
engine/src/jarvis_engine/
├── agent/                          # NEW subpackage (Phase 20 creates __init__.py + first modules)
│   ├── __init__.py                 # Empty init, marks subpackage
│   ├── state_store.py              # AGENT-04: AgentStateStore + SQLite agent_tasks table
│   ├── tool_registry.py            # TOOL-01: ToolRegistry + ToolSpec dataclass
│   └── vram_coordinator.py         # UNITY-06: VRAMCoordinator mutex + nvidia-smi monitor
├── ops/
│   └── unity_process_manager.py    # UNITY-06 (process side): PID lockfile + taskkill /f /t
├── data/
│   └── unity_kg_seed/              # KNOW-01: Unity 6.3 API seed data files
│       ├── unity63_api.json        # Type/method dictionary for validation
│       ├── unity63_breaking.json   # Breaking change catalog
│       └── unity63_errors.json     # Common compile error → fix patterns
└── commands/
    └── agent_commands.py           # NEW: AgentRunCommand, AgentStatusCommand, AgentApproveCommand
```

### Pattern 1: VRAMCoordinator Mutex (UNITY-06)

**What:** A module-level asyncio lock that makes `generation_active` and `unity_playmode_active` mutually exclusive. Backed by `nvidia-smi` polling for real VRAM pressure monitoring.

**When to use:** Acquired before any Ollama inference call; acquired before any Unity play-mode entry. Never held simultaneously.

**VRAM Budget (8GB RTX 4060 Ti):**
- Ollama qwen3.5 Q4_K_M weights: 5.5 GB (non-negotiable)
- KV cache at OLLAMA_NUM_CTX=4096: ~0.8 GB
- Unity Editor baseline (idle): ~0.5 GB
- Unity play-mode rendering budget (capped): ~1.0 GB
- Safety headroom: 0.2 GB
- Total: 8.0 GB — zero margin for simultaneous use

```python
# Source: SUMMARY.md + PITFALLS.md verified design
# engine/src/jarvis_engine/agent/vram_coordinator.py

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from typing import ClassVar

logger = logging.getLogger(__name__)

VRAM_PRESSURE_THRESHOLD_MB = 7500  # nvidia-smi threshold before blocking next step


@dataclass
class VRAMCoordinator:
    """Mutex preventing concurrent Ollama inference and Unity play-mode GPU use.

    generation_active and unity_playmode_active are mutually exclusive.
    Acquire generation_lock before any ModelGateway call.
    Acquire playmode_lock before any Unity play-mode entry command.
    Both share _gpu_mutex — only one may be held at a time.
    """

    _gpu_mutex: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _generation_active: bool = field(default=False, init=False)
    _playmode_active: bool = field(default=False, init=False)

    async def acquire_generation(self) -> None:
        await self._gpu_mutex.acquire()
        self._generation_active = True
        logger.debug("VRAMCoordinator: generation_active=True")

    def release_generation(self) -> None:
        self._generation_active = False
        self._gpu_mutex.release()
        logger.debug("VRAMCoordinator: generation_active=False")

    async def acquire_playmode(self) -> None:
        await self._gpu_mutex.acquire()
        self._playmode_active = True
        logger.debug("VRAMCoordinator: unity_playmode_active=True")

    def release_playmode(self) -> None:
        self._playmode_active = False
        self._gpu_mutex.release()
        logger.debug("VRAMCoordinator: unity_playmode_active=False")

    @property
    def status(self) -> dict:
        return {
            "generation_active": self._generation_active,
            "playmode_active": self._playmode_active,
            "locked": self._gpu_mutex.locked(),
        }


def read_vram_used_mb() -> int | None:
    """Query nvidia-smi for current VRAM usage in MB. Returns None on failure."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


# Module-level singleton — shared across daemon and agent components
_COORDINATOR: VRAMCoordinator | None = None


def get_coordinator() -> VRAMCoordinator:
    global _COORDINATOR
    if _COORDINATOR is None:
        _COORDINATOR = VRAMCoordinator()
    return _COORDINATOR
```

### Pattern 2: Unity Process Manager (UNITY-06 process side)

**What:** Extends `ops/process_manager.py` pattern for Unity Editor processes. Key difference: Unity spawns child processes (`UnityShaderCompiler.exe`, import workers) that must be killed with `taskkill /f /t` (tree kill), not just the parent.

**When to use:** Any time the agent launches Unity Editor in batch mode or interactive mode. PID stored on launch; atexit handler kills tree on Python exit.

```python
# Source: PITFALLS.md Pitfall 8, ops/process_manager.py existing pattern
# engine/src/jarvis_engine/ops/unity_process_manager.py

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

UNITY_SERVICE_NAME = "unity_editor"


def kill_unity_tree(pid: int) -> bool:
    """Kill Unity Editor and all child processes (shader compiler, import workers).

    Uses taskkill /f /t on Windows for full process tree kill.
    proc.terminate() / proc.kill() alone only kills the parent on Windows —
    UnityShaderCompiler.exe children become orphaned and hold the project lock.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/f", "/t", "/pid", str(pid)],
            capture_output=True, text=True,
        )
        success = result.returncode == 0
    else:
        # POSIX: kill process group
        import os, signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            success = True
        except ProcessLookupError:
            success = False
    if success:
        logger.info("Killed Unity process tree (pid=%d)", pid)
    else:
        logger.warning("Failed to kill Unity process tree (pid=%d)", pid)
    return success
```

**Startup check pattern** (replicate `_ollama_started_by_widget`):

```python
# Before launching Unity, check for stale lockfile:
from jarvis_engine.ops.process_manager import read_pid_file, write_pid_file
from jarvis_engine.ops.unity_process_manager import kill_unity_tree

def ensure_unity_not_running(root: Path) -> None:
    """Kill stale Unity instance if lockfile exists and process is alive."""
    info = read_pid_file(UNITY_SERVICE_NAME, root)
    if info is not None:
        kill_unity_tree(info["pid"])
        from jarvis_engine.ops.process_manager import remove_pid_file
        remove_pid_file(UNITY_SERVICE_NAME, root)
```

### Pattern 3: KG Seeding Script (KNOW-01)

**What:** A one-time (idempotent) seeder that loads structured Unity 6.3 API data into the knowledge graph using the existing `graph.py` upsert path. Seeded at daemon startup if not already present.

**When to use:** Called once at agent subsystem initialization. Idempotent — re-running is safe. Queryable by Phase 23 code generation via existing `query_relevant_facts()`.

**KG node schema for Unity API entries:**

```python
# Source: knowledge/graph.py existing upsert pattern
# Nodes use label + relation "unity_api_method" / "unity_api_type" / "unity_breaking_change"

unity_api_node = {
    "label": "GameObject.AddComponent<T>()",     # The API name
    "value": "Returns T component added to the GameObject. Unity 6.3 generic form required.",
    "confidence": 0.95,
    "tags": ["unity63", "api_reference", "UnityEngine"],
    "relation": "unity_api_method",              # custom relation for KG queries
    "source": "unity63_kg_seed_v1",
}
```

**Seed data categories to include:**

1. **Removed/renamed namespaces**: `UnityEngine.Experimental.*` (most removed in Unity 6.0), `UnityEngine.Rendering.Universal` compatibility mode APIs
2. **Breaking serialization change**: `[SerializeField]` now only valid on fields, not properties; use `[field: SerializeField]` for auto-properties
3. **Render pipeline changes**: compatibility mode render graph calls removed in 6.3
4. **Common compile errors → fixes**: CS0117 / CS0619 patterns with Unity 6.3 alternatives
5. **Physics API**: `OnTriggerEnter` / `OnCollisionEnter` signature differences in 6.x
6. **Input System**: `Input.GetAxis` vs new InputSystem package patterns

**Seed trigger check:**

```python
def is_unity_kg_seeded(kg) -> bool:
    """Return True if Unity 6.3 KG seed has already been applied."""
    results = kg.query_relevant_facts("unity63_kg_seed_v1", k=1)
    return len(results) > 0
```

### Pattern 4: AgentStateStore + SQLite Schema (AGENT-04)

**What:** A new `agent_tasks` SQLite table added via the existing `schema_version` migration pattern. Stores full task plan JSON, current step index, checkpoint blob, and approval flags. Enables crash recovery — task resumes from last committed checkpoint, not from scratch.

**Table schema:**

```sql
-- Added as schema version migration in memory/engine.py _init_schema (or separate agent DB)
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id        TEXT PRIMARY KEY,
    goal           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    -- valid: pending, running, waiting_approval, completed, failed, cancelled
    plan_json      TEXT NOT NULL DEFAULT '[]',
    -- JSON array of AgentStep objects (full plan)
    step_index     INTEGER NOT NULL DEFAULT 0,
    -- Index of next step to execute (checkpoint = "I completed N-1 steps")
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    -- Arbitrary JSON blob: last successful step output, intermediate artifacts
    token_budget   INTEGER NOT NULL DEFAULT 50000,
    tokens_used    INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT NOT NULL DEFAULT '',
    approval_needed INTEGER NOT NULL DEFAULT 0,  -- bool: 1 if blocked on approval
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);
```

**Three-layer checkpoint model** (from research):
- Layer 1 (mission state): `step_index` — which step to resume from
- Layer 2 (tool context): `checkpoint_json` — last successful tool output (file paths written, compile result, etc.)
- Layer 3 (system config): `status` + `approval_needed` — agent FSM state

**AgentStateStore class:**

```python
# Source: fast.io/resources/ai-agent-state-checkpointing/ pattern + Jarvis conventions
# engine/src/jarvis_engine/agent/state_store.py

from __future__ import annotations

import json
import sqlite3
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AgentTask:
    task_id: str
    goal: str
    status: str = "pending"
    plan_json: str = "[]"
    step_index: int = 0
    checkpoint_json: str = "{}"
    token_budget: int = 50000
    tokens_used: int = 0
    error_count: int = 0
    last_error: str = ""
    approval_needed: bool = False


class AgentStateStore:
    """SQLite-backed store for agent task state with crash-safe checkpointing."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                task_id         TEXT PRIMARY KEY,
                goal            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                plan_json       TEXT NOT NULL DEFAULT '[]',
                step_index      INTEGER NOT NULL DEFAULT 0,
                checkpoint_json TEXT NOT NULL DEFAULT '{}',
                token_budget    INTEGER NOT NULL DEFAULT 50000,
                tokens_used     INTEGER NOT NULL DEFAULT 0,
                error_count     INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT NOT NULL DEFAULT '',
                approval_needed INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status)"
        )
        self._db.commit()

    def checkpoint(self, task: AgentTask) -> None:
        """Persist current task state before executing next tool call."""
        self._db.execute("""
            INSERT OR REPLACE INTO agent_tasks
                (task_id, goal, status, plan_json, step_index, checkpoint_json,
                 token_budget, tokens_used, error_count, last_error, approval_needed,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            task.task_id, task.goal, task.status, task.plan_json,
            task.step_index, task.checkpoint_json, task.token_budget,
            task.tokens_used, task.error_count, task.last_error,
            int(task.approval_needed),
        ))
        self._db.commit()

    def load(self, task_id: str) -> AgentTask | None:
        row = self._db.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._db.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
        ).description]
        # Re-execute to get description... use cursor pattern in real code
        return AgentTask(**dict(zip(cols, row)))
```

**IMPORTANT:** The `AgentStateStore` should accept the existing `sqlite3.Connection` from `MemoryEngine` rather than opening a new connection. Do NOT create a second SQLite connection in the agent — use the existing connection pool pattern.

### Pattern 5: ToolRegistry + ToolSpec Protocol (TOOL-01)

**What:** A dataclass-based registry keyed by tool name. Each `ToolSpec` carries a JSON Schema descriptor (used by the Phase 22 planner to populate LLM system prompts), an approval flag, and callable references for `execute`, `validate`, and `estimate_cost`.

**When to use:** All agent tool calls go through this registry. Phase 22 (TaskPlanner) reads tool schemas to inject into the LLM prompt. Phase 20 defines the contract; Phases 21-24 register their tools.

```python
# Source: toolregistry.readthedocs.io schema-first pattern + SUMMARY.md architecture
# engine/src/jarvis_engine/agent/tool_registry.py

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolCallable(Protocol):
    async def __call__(self, **kwargs: Any) -> Any: ...


@dataclass
class ToolSpec:
    """Specification for a registered agent tool.

    name:           Unique tool identifier (snake_case, e.g. "file_write")
    description:    Human-readable description injected into LLM system prompt
    parameters:     JSON Schema object for the tool's parameters
    execute:        Async callable — the actual tool implementation
    validate:       Sync callable — validates parameters before execution; raises ValueError on bad input
    estimate_cost:  Sync callable — returns estimated cost dict {"tokens": int, "api_credits": float}
    requires_approval: If True, StepExecutor blocks and emits approval_needed event before executing
    is_destructive: If True, implies requires_approval=True; additional logging
    """

    name: str
    description: str
    parameters: dict  # JSON Schema object
    execute: Callable
    validate: Callable = field(default=lambda **kw: None)
    estimate_cost: Callable = field(default=lambda **kw: {"tokens": 0, "api_credits": 0.0})
    requires_approval: bool = False
    is_destructive: bool = False

    def __post_init__(self) -> None:
        if self.is_destructive:
            self.requires_approval = True


class ToolRegistry:
    """Registry of available agent tools, keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            logger.warning("ToolRegistry: overwriting existing tool '%s'", spec.name)
        self._tools[spec.name] = spec
        logger.info("ToolRegistry: registered tool '%s' (approval=%s)", spec.name, spec.requires_approval)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas_for_prompt(self) -> list[dict]:
        """Return list of tool schemas formatted for LLM system prompt injection."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "requires_approval": spec.requires_approval,
            }
            for spec in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)
```

### Pattern 6: CQRS Command Stubs

**What:** Three new command dataclasses registered in `app.py` following the exact existing pattern. Stubs at this phase — no real handler logic yet, return `return_code=0` with a "not yet implemented" message. Phase 22 fills in the real handlers.

```python
# engine/src/jarvis_engine/commands/agent_commands.py

from __future__ import annotations

from dataclasses import dataclass, field
from jarvis_engine.commands.base import ResultBase


@dataclass(frozen=True)
class AgentRunCommand:
    """Start a new agent task from a natural language goal."""
    goal: str = ""
    task_id: str = ""          # Optional; generated if empty
    token_budget: int = 50000


@dataclass
class AgentRunResult(ResultBase):
    task_id: str = ""
    status: str = ""


@dataclass(frozen=True)
class AgentStatusCommand:
    """Query current status of a running or completed agent task."""
    task_id: str = ""


@dataclass
class AgentStatusResult(ResultBase):
    task_id: str = ""
    status: str = ""
    step_index: int = 0
    tokens_used: int = 0
    last_error: str = ""


@dataclass(frozen=True)
class AgentApproveCommand:
    """Approve or reject a pending destructive/costly agent action."""
    task_id: str = ""
    approved: bool = True
    reason: str = ""


@dataclass
class AgentApproveResult(ResultBase):
    task_id: str = ""
    action_taken: str = ""
```

**Registration in `app.py`** follows the lazy-import handler pattern:

```python
# In app.py — add to existing command registrations
from jarvis_engine.commands.agent_commands import (
    AgentApproveCommand,
    AgentRunCommand,
    AgentStatusCommand,
)

# In _register_agent_commands() function:
def _register_agent_commands(bus: CommandBus, root: Path) -> None:
    from jarvis_engine.handlers.agent_handlers import (
        AgentApproveHandler,
        AgentRunHandler,
        AgentStatusHandler,
    )
    bus.register(AgentRunCommand, AgentRunHandler(root).handle)
    bus.register(AgentStatusCommand, AgentStatusHandler(root).handle)
    bus.register(AgentApproveCommand, AgentApproveHandler(root).handle)
```

### Anti-Patterns to Avoid

- **New SQLite connection in agent:** Never open a second `sqlite3.connect()` in agent code. Use the existing `MemoryEngine` connection passed in at construction time. Opening a second connection risks WAL-mode write conflicts.
- **Per-step `nvidia-smi` polling in the hot path:** Call `read_vram_used_mb()` at checkpoint boundaries (before acquiring mutex), not inside every inference call. Each `nvidia-smi` call has ~20ms overhead.
- **Blocking asyncio event loop with `subprocess.run` for VRAM polling:** Use `asyncio.create_subprocess_exec` or run in `executor` if polling on the async path.
- **Hardcoded Unity.exe path in process manager:** Accept path as a config parameter, fall back to registry lookup on Windows. Never hardcode `C:\Program Files\Unity\...`.
- **ToolSpec with mutable defaults in dataclass:** Use `field(default=lambda...)` for callable defaults, not bare function references, to avoid shared state issues.
- **Second command bus for agent commands:** All three agent commands register on the existing 70+ command bus in `app.py`. Never create a second `CommandBus` instance.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Windows process tree kill | Custom recursive process kill via ctypes | `subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)])` | `taskkill /t` kills the entire process tree atomically; ctypes approach misses grandchildren |
| PID file management | New PID tracking dict/file format | `ops/process_manager.py` `write_pid_file` / `read_pid_file` / `remove_pid_file` | Already handles PID reuse detection, creation time validation, stale file cleanup |
| VRAM measurement | ctypes calls to NVML | `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` | nvidia-smi is always available on the target system; NVML ctypes requires driver version matching |
| JSON Schema validation | Custom parameter checker | Python `jsonschema` library (already in deps) or `isinstance` checks in `ToolSpec.validate` | JSON Schema is the industry standard for tool parameter schemas; LLM tool-call APIs expect this format |
| Agent task SQLite table | Separate SQLite file for agent state | Add `agent_tasks` table to the existing memory DB connection | Avoids second WAL journal, second connection, second lock; one DB = simpler backup/restore |
| KG fact deduplication | Custom hash-and-compare | `INSERT OR IGNORE` on `label` in `kg_nodes` + existing `upsert_fts_kg` | Already handled by `graph.py`'s upsert path — calling seeder twice is safe |

**Key insight:** Every infrastructure primitive needed by Phase 20 already exists in Jarvis. The work is wiring existing systems together with new data schemas and a coordination layer — not building net-new infrastructure.

---

## Common Pitfalls

### Pitfall 1: `proc.terminate()` Leaves Unity Child Processes Running

**What goes wrong:** Python's `subprocess.Popen.terminate()` on Windows sends `TerminateProcess()` only to the direct parent (`Unity.exe`). `UnityShaderCompiler.exe`, asset import workers, and cache server processes remain running and hold the project write lock. Next agent run fails with "project already open."

**Why it happens:** Windows does not propagate kills to process trees via `TerminateProcess`. This is documented in `psutil` and confirmed by Python CPython issue tracker.

**How to avoid:** Always use `taskkill /f /t /pid <PID>` for Unity process tree termination. Store PID on launch in `.planning/runtime/pids/unity_editor.pid`. Add a startup check that kills any stale Unity instance before opening the project.

**Warning signs:** Multiple `Unity.exe` entries in Task Manager; `UnityShaderCompiler.exe` visible after agent shutdown; "project already open" errors on second run.

### Pitfall 2: VRAMCoordinator asyncio.Lock Created Outside Event Loop

**What goes wrong:** `asyncio.Lock()` instantiated at module import time (before the event loop starts) raises `DeprecationWarning` in Python 3.10+ and breaks in 3.12+ when acquired from a different event loop than the one active at creation.

**Why it happens:** Module-level singleton `_COORDINATOR = VRAMCoordinator()` instantiated on first import, which may happen during test collection or CLI startup before `asyncio.run()`.

**How to avoid:** Use `field(default_factory=asyncio.Lock)` in the dataclass so the lock is created lazily, or use `get_coordinator()` which creates the instance on first call from within the event loop context. Tests must create the coordinator inside `asyncio.run()` or an `async` fixture.

**Warning signs:** `DeprecationWarning: There is no current event loop` during test collection; `RuntimeError: This event loop is already running` during coordinator acquire.

### Pitfall 3: KG Seed Running on Every Daemon Startup

**What goes wrong:** The seed script runs every time the daemon starts, inserting duplicate KG nodes. Even with `INSERT OR IGNORE`, it wastes 2-5 seconds of startup time and pollutes logs with "overwriting existing node" warnings.

**Why it happens:** No guard check before seeding; seeder is called unconditionally in daemon init.

**How to avoid:** Gate the seeder with `is_unity_kg_seeded()` that queries for a sentinel node (`label="unity63_kg_seed_v1"`). Seed only if sentinel absent. Write sentinel as the final step of the seeder so a partial seed is retried on next startup.

**Warning signs:** Daemon startup takes 5+ extra seconds; KG node count doubles on every restart.

### Pitfall 4: AgentStateStore Opening Its Own SQLite Connection

**What goes wrong:** `AgentStateStore(db_path)` opens a new `sqlite3.connect()` to the same DB file already open by `MemoryEngine`. In WAL mode, two writers from the same process can deadlock or produce `database is locked` errors on concurrent writes.

**Why it happens:** It is tempting to give `AgentStateStore` a path argument (like many other Jarvis modules). But `MemoryEngine` already holds the write lock.

**How to avoid:** `AgentStateStore.__init__` accepts a `sqlite3.Connection` (the same connection object already managed by `MemoryEngine._db`). This is the same pattern used by `knowledge/graph.py` which accepts `MemoryEngine` by reference.

**Warning signs:** `sqlite3.OperationalError: database is locked` during concurrent agent + memory operations; two separate journal files (`.db-wal`, `.db-wal2`) appearing.

### Pitfall 5: ToolSpec with `is_destructive=False` for File Write Operations

**What goes wrong:** A `file_write` tool registered without `is_destructive=True` skips the approval gate. The agent writes C# scripts that call `System.IO.File.Delete` or `Process.Start` directly to the Unity project without user review.

**Why it happens:** "Writing a file" feels non-destructive. But writing a `.cs` file that will be compiled and executed has the same risk surface as executing a shell command.

**How to avoid:** Treat "write a file that will be compiled and executed by Unity" as equivalent to a destructive operation. Set `is_destructive=True` on `UnityScriptWriteTool` and `UnityFileTool`. Only `read-only` file operations (read, stat, list) are non-destructive.

**Warning signs:** Agent silently writes C# files containing `System.IO.File.Delete`; no approval dialog appears; Unity project files modified without user notification.

---

## Code Examples

### Verified Patterns from Existing Jarvis Source

#### nvidia-smi query pattern (subprocess, existing usage in Jarvis)

```python
# Source: Pattern from ops/gaming_mode.py subprocess usage style
import subprocess

def read_vram_used_mb() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None
```

#### KG upsert pattern (from knowledge/graph.py existing API)

```python
# Source: knowledge/graph.py add_fact() / upsert_fts_kg() existing methods
# When seeding, call kg.add_fact() for each Unity API entry

def seed_unity_api_fact(kg, label: str, value: str, tags: list[str]) -> None:
    kg.add_fact(
        label=label,
        value=value,
        confidence=0.95,
        source="unity63_kg_seed_v1",
        tags=tags,
    )
```

#### PID file pattern (from ops/process_manager.py)

```python
# Source: ops/process_manager.py write_pid_file / read_pid_file (existing)
from jarvis_engine.ops.process_manager import (
    read_pid_file, write_pid_file, remove_pid_file
)

# On Unity launch:
write_pid_file("unity_editor", root)  # writes .planning/runtime/pids/unity_editor.pid

# On Unity shutdown:
info = read_pid_file("unity_editor", root)
if info:
    kill_unity_tree(info["pid"])
    remove_pid_file("unity_editor", root)
```

#### Dataclass command pattern (from commands/learning_commands.py)

```python
# Source: commands/learning_commands.py — frozen=True, ResultBase inheritance
from dataclasses import dataclass
from jarvis_engine.commands.base import ResultBase

@dataclass(frozen=True)
class AgentRunCommand:
    goal: str = ""
    task_id: str = ""
    token_budget: int = 50000

@dataclass
class AgentRunResult(ResultBase):
    task_id: str = ""
    status: str = ""
```

#### Handler registration pattern (from app.py existing _register_* functions)

```python
# Source: app.py _register_with_fallback and subsystem registration pattern
def _register_agent_commands(bus: CommandBus, root: Path) -> None:
    """Lazy-import handler registration — agent subpackage loaded on first use."""
    from jarvis_engine.handlers.agent_handlers import (
        AgentApproveHandler, AgentRunHandler, AgentStatusHandler,
    )
    bus.register(AgentRunCommand, AgentRunHandler(root).handle)
    bus.register(AgentStatusCommand, AgentStatusHandler(root).handle)
    bus.register(AgentApproveCommand, AgentApproveHandler(root).handle)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| One SQLite file per subsystem | Shared MemoryEngine connection + table-per-subsystem | Jarvis v3.0 refactor | AgentStateStore must use existing connection, not open new file |
| `subprocess.kill()` for process cleanup | `taskkill /f /t /pid` + PID lockfile | Jarvis v5.0 widget lifecycle work | Copy this pattern exactly for Unity process management |
| Global mutable state for service tracking | `_ollama_started_by_widget` flag + PID file | v5.0 session | VRAMCoordinator follows same module-level singleton + factory function pattern |
| Hardcoded tool list in agent prompts | ToolRegistry with JSON Schema descriptors | Industry 2025-2026 (per tool-calling APIs) | LLMs expect structured tool schemas; build this now, not later |

**Deprecated/outdated:**
- Direct `proc.terminate()` for Unity on Windows: replaced by `taskkill /f /t` — the v5.0 Ollama cleanup already uses `taskkill /F /IM ollama.exe`; Unity needs the tree-kill variant `/T`
- `INSERT INTO ... WHERE NOT EXISTS` for KG deduplication: the existing `upsert_fts_kg` handles this correctly; no need to reinvent

---

## Open Questions

1. **Which SQLite DB file does AgentStateStore target?**
   - What we know: Jarvis has one primary `memory.db` managed by `MemoryEngine`. The agent's `agent_tasks` table should share this DB.
   - What's unclear: Whether `_init_schema` in `memory/engine.py` should be extended (tight coupling) or whether `AgentStateStore._ensure_schema()` should be called separately after `MemoryEngine` is initialized (looser coupling, but requires connection handoff).
   - Recommendation: Pass the `sqlite3.Connection` from `MemoryEngine` to `AgentStateStore` at construction. Add `agent_tasks` table creation to `AgentStateStore._ensure_schema()` — this keeps the agent schema self-contained without modifying `memory/engine.py`.

2. **Unity 6.3 breaking changes catalog completeness**
   - What we know: PITFALLS.md and SUMMARY.md identify specific namespaces (`UnityEngine.Experimental.*`, `[SerializeField]` on properties, compatibility mode render graph calls). The official upgrade guide at `docs.unity3d.com/6000.3/Documentation/Manual/UpgradeGuideUnity63.html` is the authoritative source.
   - What's unclear: Full diff from Unity 2022 → 6.3 for physics, input system, and render pipeline APIs was not compiled in prior research.
   - Recommendation: Phase 20 Plan Wave 0 should include a task to fetch and parse the official Unity 6.3 upgrade guide, extract breaking change entries into `data/unity_kg_seed/unity63_breaking.json`, and seed from that structured file. Start with the known high-impact items (listed in PITFALLS.md) and expand.

3. **VRAMCoordinator asyncio compatibility with threading-based daemon**
   - What we know: `daemon_loop.py` uses `threading` (not `asyncio`). `asyncio.Lock()` is not safe to acquire from a `threading.Thread`.
   - What's unclear: Whether the agent loop (Phase 22) runs in the daemon's async context or in a thread pool.
   - Recommendation: For Phase 20, implement `VRAMCoordinator` with both a `threading.Lock` and an `asyncio.Lock` — the threading lock for use from daemon threads, the asyncio lock for use from the async agent loop. Alternatively, implement as a `threading.Lock`-only coordinator in Phase 20 and convert to asyncio-aware in Phase 22 when the event loop architecture is decided.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (current, 5979 tests passing) |
| Config file | `engine/` directory (no pytest.ini, uses `pyproject.toml` or inline) |
| Quick run command | `python -m pytest engine/tests/ -k "agent or vram or unity_process or tool_registry" -x -q` |
| Full suite command | `python -m pytest engine/tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UNITY-06 | VRAMCoordinator mutex: acquire_generation blocks acquire_playmode | unit | `pytest engine/tests/test_vram_coordinator.py -x -q` | ❌ Wave 0 |
| UNITY-06 | read_vram_used_mb returns int or None without crashing | unit | `pytest engine/tests/test_vram_coordinator.py::test_read_vram -x -q` | ❌ Wave 0 |
| UNITY-06 | UnityProcessManager.kill_unity_tree calls taskkill /f /t on Windows | unit (mock) | `pytest engine/tests/test_unity_process_manager.py -x -q` | ❌ Wave 0 |
| UNITY-06 | Startup check kills stale Unity instance before write_pid_file | unit (mock) | `pytest engine/tests/test_unity_process_manager.py::test_startup_check -x -q` | ❌ Wave 0 |
| KNOW-01 | KG seed inserts Unity 6.3 API nodes with correct tags | unit | `pytest engine/tests/test_unity_kg_seed.py -x -q` | ❌ Wave 0 |
| KNOW-01 | is_unity_kg_seeded returns False before seed, True after | unit | `pytest engine/tests/test_unity_kg_seed.py::test_idempotent -x -q` | ❌ Wave 0 |
| KNOW-01 | Re-running seeder is idempotent (no duplicate nodes) | unit | `pytest engine/tests/test_unity_kg_seed.py::test_no_duplicates -x -q` | ❌ Wave 0 |
| TOOL-01 | ToolRegistry.register stores ToolSpec; get retrieves by name | unit | `pytest engine/tests/test_tool_registry.py -x -q` | ❌ Wave 0 |
| TOOL-01 | ToolSpec with is_destructive=True sets requires_approval=True | unit | `pytest engine/tests/test_tool_registry.py::test_destructive_implies_approval -x -q` | ❌ Wave 0 |
| TOOL-01 | schemas_for_prompt returns list of dicts with name/description/parameters | unit | `pytest engine/tests/test_tool_registry.py::test_schemas_for_prompt -x -q` | ❌ Wave 0 |
| AGENT-04 | AgentStateStore.checkpoint persists task to SQLite | unit | `pytest engine/tests/test_agent_state_store.py -x -q` | ❌ Wave 0 |
| AGENT-04 | AgentStateStore.load returns None for unknown task_id | unit | `pytest engine/tests/test_agent_state_store.py::test_load_missing -x -q` | ❌ Wave 0 |
| AGENT-04 | Task survives simulated crash: load after checkpoint returns same step_index | unit | `pytest engine/tests/test_agent_state_store.py::test_crash_recovery -x -q` | ❌ Wave 0 |
| AGENT-04 | AgentRunCommand, AgentStatusCommand, AgentApproveCommand registered in bus | integration | `pytest engine/tests/test_app.py -k "agent" -x -q` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python -m pytest engine/tests/ -k "agent or vram or unity_process or tool_registry" -x -q`
- **Per wave merge:** `python -m pytest engine/tests/ -x -q` (full suite, ~5979+ tests)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `engine/tests/test_vram_coordinator.py` — covers UNITY-06 mutex and VRAM monitoring
- [ ] `engine/tests/test_unity_process_manager.py` — covers UNITY-06 process lifecycle
- [ ] `engine/tests/test_unity_kg_seed.py` — covers KNOW-01 seeding and idempotency
- [ ] `engine/tests/test_tool_registry.py` — covers TOOL-01 registry contract
- [ ] `engine/tests/test_agent_state_store.py` — covers AGENT-04 checkpoint and recovery
- [ ] `engine/src/jarvis_engine/agent/__init__.py` — new subpackage init (empty file)
- [ ] `engine/src/jarvis_engine/commands/agent_commands.py` — command dataclasses
- [ ] `engine/src/jarvis_engine/handlers/agent_handlers.py` — stub handlers
- [ ] `engine/src/jarvis_engine/data/unity_kg_seed/unity63_api.json` — seed data file

---

## Sources

### Primary (HIGH confidence)

- `.planning/research/SUMMARY.md` — Phase 1 (Infrastructure) complete architecture design; VRAMCoordinator, AgentStateStore, ToolRegistry specifications
- `.planning/research/PITFALLS.md` — Pitfalls 3 (VRAM), 8 (orphaned processes); exact VRAM budget math; `taskkill /f /t` requirement verified
- `.planning/research/STACK.md` — confirmed zero new pip deps for Phase 20; `ops/process_manager.py` integration path; KG pattern
- `engine/src/jarvis_engine/ops/process_manager.py` (read directly) — exact PID file API: `write_pid_file`, `read_pid_file`, `remove_pid_file`, `kill_service`
- `engine/src/jarvis_engine/knowledge/graph.py` (read directly) — confirmed `add_fact()` and `upsert_fts_kg()` upsert path for KG seeding
- `engine/src/jarvis_engine/memory/engine.py` (read directly) — confirmed `schema_version` table pattern; `INSERT OR IGNORE INTO schema_version(version)` migration approach
- `engine/src/jarvis_engine/commands/base.py` (read directly) — `ResultBase` dataclass pattern all new commands follow
- `engine/src/jarvis_engine/app.py` (read directly) — lazy-import handler factory pattern; existing command registration structure
- `engine/src/jarvis_engine/desktop/widget.py` (inspected) — `_ollama_started_by_widget` + `taskkill /F /IM ollama.exe` pattern to replicate for Unity

### Secondary (MEDIUM confidence)

- `fast.io/resources/ai-agent-state-checkpointing/` — Three-layer checkpoint model (mission state, tool context, system config) — verified consistent with SUMMARY.md architecture
- `toolregistry.readthedocs.io` — Schema-first tool registry design; JSON Schema descriptor pattern for LLM tool-call injection

### Tertiary (LOW confidence)

- None for Phase 20 — all critical claims backed by existing Jarvis source code or project research documents

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all modules read directly from source; zero new dependencies confirmed
- Architecture: HIGH — VRAMCoordinator, AgentStateStore, ToolRegistry designs all verified against existing Jarvis patterns
- Pitfalls: HIGH — process tree kill and VRAM exhaustion verified against Jarvis source code (`widget.py`, `process_manager.py`) and prior research docs
- KG seeding: HIGH for mechanism; MEDIUM for data completeness (Unity 6.3 breaking changes catalog needs extraction work in Wave 0)

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (stable patterns; Unity 6.3 API catalog may need update if Unity 6.3.x point release changes APIs)
