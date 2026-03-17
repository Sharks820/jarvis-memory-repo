# Project State

## Project Reference

See: .planning/PROJECT.md
See: .planning/ROADMAP.md (v6.0 Jarvis Unity Agent)

**Core value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.
**Current focus:** v6.0 Jarvis Unity Agent — Phase 20: Infrastructure Foundations

## Current Position

Phase: 20 of 25 (Infrastructure Foundations)
Plan: 0 of 2 in current phase
Status: Ready to plan
Last activity: 2026-03-17 — v6.0 roadmap created, phases 20–25 defined

Progress (v6.0): [░░░░░░░░░░] 0%

## Performance Metrics

**Prior milestones shipped:**
- v1.0 Desktop Engine: phases 1–9, 473 tests
- v2.0 Android App: phases 10–13, 3880 tests
- v3.0 Hardening: 4136 tests, 7-pillar security
- v4.0 Intelligence & Voice: 5 phases, 4345 tests
- v5.0 Reliability & Continuity: 6 phases, 5979 tests

**v6.0 baseline (2026-03-16):**
- pytest: 5979 passing, 6 skipped, 0 failures
- ruff: clean
- mypy: 77 errors / 22 files
- bandit: 0 high, 9 medium, 57 low

## Accumulated Context

### Decisions

- v6.0 stack: websockets>=14.0 (Python WS client) + tripo3d==0.3.12 — only 2 new pip packages
- Custom ReAct agent loop in agent/ subpackage — no LangGraph/CrewAI (would duplicate Jarvis systems)
- Unity Editor Bridge: custom C# WebSocket server on port 8091 (not Unity-MCP adopted wholesale)
- Blender invoked as subprocess with --background --python (bpy pip package avoided)
- VRAM budget: Ollama qwen3.5 uses 5.5–6.5GB; Unity play-mode 1–3GB; 8GB RTX 4060 Ti requires hard mutex
- Phase order rationale: infrastructure blockers (VRAM OOM, orphaned processes, API hallucination) must be resolved before bridge or code-gen work begins
- Phases 23 and 24 can be executed in parallel (no direct dependency between them)

### Blockers/Concerns

- WebSocket C# library choice unresolved: websocket-sharp (UPM) vs System.Net.WebSockets (stdlib) — decide before Phase 21 planning
- tripo.io credit cost model not researched — needed before Phase 24 approval gate thresholds
- qwen3.5 Unity 6.3 hallucination baseline unknown — Phase 23 should benchmark before/after KG seeding
- Blender 4.3 path needs discovery mechanism (registry lookup or config.json) — not hardcoded

### Pending Todos

None yet.

## Session Continuity

Last session: 2026-03-17
Stopped at: Roadmap created and approved. Ready to begin Phase 20 planning.
Resume file: None
