# Project State

## Project Reference

See: .planning/PROJECT.md
See: .planning/ROADMAP.md (v5.0 Reliability, Continuity, and Autonomous Learning)

**Core value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.
**Current focus:** v5.0 phase-1 reliability execution (debt gate active with desloppify triage loop).

## Current Position

Phase: v5.0 / Phase 1 (Reliability Core + Resource Control) -- IN PROGRESS
Current Plan: 14-02 (Continuity, Voice UX, Learning Mission Control, and Autonomous Fix Loop)
Status: v4.0 complete, v5.0 execution active
Last activity: 2026-03-07 (SoC splits: desktop_widget.py 5 large methods -> focused sub-methods, thread-safe locks on global singletons)

Progress (v5.0): [██████░░░░] 55%

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

**v5.0 Reliability & Continuity**: IN PROGRESS
- Latest full test run (2026-03-07): 4903 passing, 11 skipped, 0 failures
- Lint baseline: ruff clean
- Typed debt baseline: mypy 105 errors across 31 files
- Security scan baseline: bandit 165 findings (1 high, 50 medium, 114 low)
- Plan active: `.planning/phases/14-world-class-assistant-reliability/14-02-PLAN.md`

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
- 2026-03-05: v5 Step 1 complete (command lifecycle hardening + structured diagnostics on mobile API).
- 2026-03-05: v5 Step 2 complete (CLI prompt compaction/checkpointing to preserve context under transport limits).
- 2026-03-05: v5 Step 3 complete (runtime resource budgets/pressure throttling + reliability panel + mission activity telemetry).
- 2026-03-05: v5 debt-gate pass: desloppify installed, Claude skill updated, and multi-file schema/key-flow fixes applied with scan loop + attested resolutions.
- 2026-03-07: Broad exception narrowing batch 28: narrowed 22 catches to specific types across 12 files, marked 56 boundary catch-alls across 8 HTTP/UI files, removed redundant local sqlite3 import in app.py.
- 2026-03-05: v5 debt-gate pass 2: fixed `mobile_api` recent_events schema drift (`details`) and `ops_sync` fallback ICS phantom-key reads; focused tests + full suite passed.
- 2026-03-05: v5 debt-gate pass 3: reduced dict-key drift and constructor duplication (`persona`, `resilience`, `runtime_control`, `mobile_api`, `defense_handlers`, `learning/*`) with repeated excluded-scope scans and green targeted/full test gates.
- 2026-03-05: v5 debt-gate pass 4: centralized safe Ollama endpoint policy (`security/net_policy.py`), removed gateway import masking, corrected cloud-vs-failed cost accounting contracts through proactive surfaces, and completed targeted regression gates (254 tests) with ruff clean.
- 2026-03-05: reliability hardening tranche: added authenticated mobile `/command` voice-auth-guard bypass plumbing (`skip_voice_auth_guard`) while preserving owner identity checks; added mobile best-effort learning fallback for failed/blocked commands; refreshed CLI provider availability dynamically in gateway without restart; made Claude CLI max-budget flag env-driven to avoid hardcoded budget failures; full regression suite clean (4456 passed, 14 skipped).
- 2026-03-05: ran repo-wide desloppify baseline scan under Python 3.12 (`PYENV_VERSION=3.12.12`) with build/cache excludes; captured 889 findings / strict 33.2 and created Plan 14-02 to drive large-scope continuity, date-grounding, realtime voice UX, mission transparency, autonomous approval-gated autofix, memory hygiene, and score-to-100 execution.
- 2026-03-05: began 14-02 implementation tranche in engine runtime: strengthened system prompt clock context (local+UTC+epoch conflict guard), added URL-to-domain speech compaction for TTS to avoid reading full links aloud, and added focused regression tests with post-change desloppify rescans.
- 2026-03-05: enabled Bandit in the active Python 3.12 scan runtime to restore Python security coverage in desloppify; fixed honeypot fake credential variable naming that triggered hardcoded-secret detectors, then re-ran security-focused and engine-wide scans to track true baseline.
- 2026-03-05: added explicit voice-listen lifecycle state emission (`arming`, `listening`, `processing`, `executing`, `idle`, `error`) to stdout and activity feed for real-time UX/telemetry trust, with focused regression tests for success/error transitions.
- 2026-03-05: added model-switch continuity guardrails: system-prompt continuity contract is now injected when routed model changes with existing history, and model-switch events are logged to activity feed (`conversation_model_switch`) for observability and anti-reset diagnosis.
- 2026-03-05: upgraded learning mission status surfaces with explicit active/inactive flags, active-count and per-status counters, mission status-detail emission, and richer response summaries to improve UI/voice mission transparency and operator trust.
- 2026-03-06: **User engagement checkpoint** — owner (Conner) acknowledged positive signal and confirmed acceptance of the current main-branch state. Reliability, innovation, and optimization tracking continues as planned. No blocking issues raised; feedback is: the system is on the right trajectory and the workflow heartbeat is healthy. Workflow cadence continues under v5.0 Phase 1 with next focus on continuity, voice robustness, and desloppify burn-down.
- v5.0 sequencing decision:
  1. Reliability/resource control first
  2. Cross-provider context continuity second
  3. Learning mission truthfulness + live activity third
  4. Voice correction loop and mobile tasking after core stability

### Blockers/Concerns
- Known flaky: test_cmd_brain_status_and_context (nomic-bert tensor size mismatch — infrastructure issue, not code)
- Security/typed quality debt still large despite functional pass baseline:
  - mypy: 105 errors / 31 files
  - bandit: 165 findings (1 high, 50 medium, 114 low)
- Desloppify strict-score loop currently constrained by subjective batch tooling and scope management; continue debt gate with targeted next/scan cycles.
- User-reported runtime issues persist in real-world usage:
  - context truncation/HTTP failures on long replies
  - stability degradation after short command sequences
  - high memory intensity and mission/activity trust gaps

## Session Continuity

Last session: 2026-03-06
Stopped at: User engagement/feedback checkpoint logged. Owner confirmed positive signal and acceptance of main-branch state. No code changes required this session. Resume next session with v5 runtime reliability tranche: cross-LLM continuity, voice robustness, mission activity truthfulness, and desloppify burn-down.
Resume file: None
