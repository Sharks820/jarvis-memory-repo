# Project State

## Project Reference

See: .planning/PROJECT.md
See: .planning/ROADMAP.md (v5.0 Reliability, Continuity, and Autonomous Learning)

**Core value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.
**Current focus:** v5.0 planning + phase-1 reliability execution kickoff.

## Current Position

Phase: v5.0 / Phase 1 (Reliability Core + Resource Control) -- IN PROGRESS
Current Plan: 14-01 (Reliability Baseline and Execution Control Plane)
Status: v4.0 complete, v5.0 execution initialized
Last activity: 2026-03-05 (local merge + full scan baseline + v5 plan authored)

Progress (v5.0): [░░░░░░░░░░] 0%

## Performance Metrics

**v1.0 Desktop Engine**: SHIPPED (phases 1-9, 18 plans, 473 tests at ship)
**v2.0 Android App**: SHIPPED (phases 10-13, 11 plans)
**v3.0 Hardening**: SHIPPED (2 phases, 4136 tests, 7-pillar security, 4-CLI scan gauntlet clean)
**v4.0 Intelligence & Voice**: COMPLETE
- Test count: 4345 passing, 5 skipped, 0 failures
- Source files: 60+ Python modules, 100+ Kotlin files
- Phase 1 (Voice-to-Text Overhaul): COMPLETE — 5 plans, 8 STT requirements verified
- Phase 2 (Learning System Activation): COMPLETE — 3 plans, 8 LEARN requirements verified
- Phase 3 (Widget & UI Live Updates): COMPLETE — 2 plans, 5 UI requirements verified, bug scan clean
- Phase 4 (Platform Stability): COMPLETE — 1 plan, 5 STAB requirements verified, bug scan clean
- Phase 5 (Mobile App Readiness): COMPLETE — 1 plan, 5 MOB requirements verified, 2-round bug scan clean

**v5.0 Reliability & Continuity**: INITIALIZED
- Baseline test run (2026-03-05): 4433 passing, 9 skipped, 0 failures
- Lint baseline: ruff clean
- Typed debt baseline: mypy 116 errors across 35 files
- Security scan baseline: bandit 165 findings (1 high, 50 medium, 114 low)
- Plan active: `.planning/phases/14-world-class-assistant-reliability/14-01-PLAN.md`

## Self-Analysis Findings (all resolved)

### CRITICAL (fixed in Phase 2)
1. ~~Learning trackers write-only~~ — FIXED: preferences injected into prompts, route quality into classifier, usage into daemon
2. ~~Missing route param~~ — FIXED: all 4 dispatch sites pass route/topic
3. ~~No learning feedback loop~~ — FIXED: preferences personalize responses, quality penalties adjust routing

### HIGH (fixed in Phase 4)
4. ~~db_path.exists() gate~~ — FIXED: removed 3 gates, SQLite creates on connect, try/except for graceful degradation
5. ~~MemoryConsolidator not in CQRS bus~~ — FIXED: ConsolidateMemoryCommand exposed via bus, CLI, and daemon

## Accumulated Context

### Decisions
- v4.0 milestone covers: voice overhaul, learning activation, UI live-updates, platform stability, mobile readiness
- STT target: Parakeet TDT 0.6B (local) + Deepgram Nova-3 (cloud) + Silero VAD
- Learning activation: wire tracker read methods into QueryHandler, IntentClassifier, and dashboard
- Deepgram backend: used httpx REST API directly instead of deepgram-sdk
- Phase 1 complete: all 8 STT requirements verified
- Phase 2 complete: all 8 LEARN requirements verified
  - Plan 01: route/topic data quality in dispatch sites
  - Plan 02: preferences in prompts, quality penalty in classifier, usage prediction in daemon
  - Plan 03: frequency boost in search (0.9-1.1x), tier management in consolidator, dashboard learning metrics + knowledge snapshot
- Phase 3 complete: all 5 UI requirements verified
  - Plan 01: Mission cancel CQRS command, activity events (PREFERENCE_LEARNED, MISSION_STATE_CHANGE), recent_events in /widget-status, response= output
  - Plan 02: Widget frontend — live activity feed display, immediate dashboard refresh, expanded learned indicator
  - Bug scan: 2 MEDIUM + 3 LOW fixed (CLI subcommand, cancel guard, ordered dedup, correct learned intents)
- Phase 4 complete: all 5 STAB requirements verified
  - Plan 01: STAB-01 (db_path.exists() gate removal), STAB-02 (silent except logging), STAB-03 (ConsolidateMemoryCommand CQRS), STAB-04 (proactive diagnostics), STAB-05 (34 tests)
  - Bug scan: 1 MEDIUM + 1 LOW fixed (consolidator import guard, activity feed logging)
- Phase 5 complete: all 5 MOB requirements verified
  - Plan 01: MOB-01 (learning tables in sync changelog with composite PK), MOB-02 (GET /learning/summary), MOB-03 (POST /feedback with record_explicit_feedback), MOB-04/05 (45 tests)
  - Bug scan round 1: 3 CRITICAL + 4 MEDIUM fixed (DB leaks, composite PK, write lock, neutral quality)
  - Bug scan round 2: 1 CRITICAL fixed (SyncEngine._apply_single_change composite PK support)
- 2026-03-05: Start v5.0 reliability-first program with strict GSD execution and soak-test gating.
- v5.0 sequencing decision:
  1. Reliability/resource control first
  2. Cross-provider context continuity second
  3. Learning mission truthfulness + live activity third
  4. Voice correction loop and mobile tasking after core stability

### Blockers/Concerns
- Known flaky: test_cmd_brain_status_and_context (nomic-bert tensor size mismatch — infrastructure issue, not code)
- Security/typed quality debt still large despite functional pass baseline:
  - mypy: 116 errors / 35 files
  - bandit: 165 findings (1 high, 50 medium, 114 low)
- User-reported runtime issues persist in real-world usage:
  - context truncation/HTTP failures on long replies
  - stability degradation after short command sequences
  - high memory intensity and mission/activity trust gaps

## Session Continuity

Last session: 2026-03-05
Stopped at: v5.0 phase-1 plan authored and ready for implementation commits
Resume file: None
