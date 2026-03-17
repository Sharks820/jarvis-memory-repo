---
phase: 20
slug: infrastructure-foundations
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | engine/pyproject.toml |
| **Quick run command** | `python -m pytest engine/tests/ -x -q` |
| **Full suite command** | `python -m pytest engine/tests/ --tb=no` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest engine/tests/ -x -q`
- **After every plan completion:** Run full suite + `ruff check` on modified files
- **Phase gate:** Full suite must be green + `ruff check engine/src/` clean

---

## Phase Requirements → Test Map

| Requirement | Test File(s) | Test Strategy |
|-------------|-------------|---------------|
| UNITY-06 | `test_vram_coordinator.py` | Mock nvidia-smi, verify mutex acquire/release, test OOM prevention |
| KNOW-01 | `test_kg_seeder.py` | Verify seed data loaded, KG queryable for Unity 6.3 facts, idempotent re-seed |
| TOOL-01 | `test_tool_registry.py` | Register tools, verify discovery, validate JSON Schema, test approval flags |
| AGENT-04 | `test_agent_state_store.py` | Create/checkpoint/resume tasks, simulate crash recovery, verify SQLite persistence |

---

## Wave 0 Gaps

These test files don't exist yet and must be created during execution:

- [ ] `engine/tests/test_vram_coordinator.py`
- [ ] `engine/tests/test_unity_process_manager.py`
- [ ] `engine/tests/test_agent_state_store.py`
- [ ] `engine/tests/test_tool_registry.py`
- [ ] `engine/tests/test_kg_seeder.py`
- [ ] `engine/tests/test_agent_commands.py`

---

## Additional Checks

- `ruff check engine/src/jarvis_engine/agent/` — must be clean
- `pylint --errors-only engine/src/jarvis_engine/agent/` — must be clean
- `bandit -r engine/src/jarvis_engine/agent/ -ll -q` — no medium+ findings
