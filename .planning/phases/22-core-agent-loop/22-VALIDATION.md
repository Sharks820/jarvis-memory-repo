---
phase: 22
slug: core-agent-loop
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 22 — Validation Strategy

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Quick run command** | `python -m pytest engine/tests/ -x -q` |
| **Full suite command** | `python -m pytest engine/tests/ --tb=no` |

## Phase Requirements → Test Map

| Requirement | Test File(s) | Strategy |
|-------------|-------------|----------|
| AGENT-01 | test_task_planner.py | LLM mock returns step DAG, verify structure |
| AGENT-02 | test_step_executor.py | Execute steps with mock tools, verify results |
| AGENT-03 | test_reflection_loop.py | Simulate failures, verify replan triggers |
| AGENT-05 | test_reflection_loop.py | 3 same-error → escalation, token budget enforcement |
| AGENT-06 | test_progress_bus.py, test_agent_e2e.py | SSE events emitted at step boundaries |
| TOOL-02 | test_approval_gate.py | Destructive=blocked, safe=auto, costly=estimate |
| TOOL-03 | test_file_tool.py | Read/write confined to project dir |
| TOOL-04 | test_shell_tool.py | Policy gate, timeout, subprocess execution |
| TOOL-05 | test_web_tool.py | Delegates to existing fetch pipeline |

## Wave 0 Gaps

- [ ] engine/tests/test_file_tool.py
- [ ] engine/tests/test_shell_tool.py
- [ ] engine/tests/test_web_tool.py
- [ ] engine/tests/test_approval_gate.py
- [ ] engine/tests/test_progress_bus.py
- [ ] engine/tests/test_task_planner.py
- [ ] engine/tests/test_step_executor.py
- [ ] engine/tests/test_reflection_loop.py
- [ ] engine/tests/test_agent_e2e.py
