---
phase: 04-connectors-and-daily-intelligence
plan: 02
subsystem: daily-intelligence
tags: [imap, email-triage, narrative-briefing, ollama, model-gateway, daily-brief]

# Dependency graph
requires:
  - phase: 01-memory-revolution-and-architecture
    provides: "Command Bus, ops_sync.py, life_ops.py framework"
  - phase: 03-intelligence-routing
    provides: "ModelGateway with complete() interface, local Ollama routing"
  - phase: 04-connectors-and-daily-intelligence
    plan: 01
    provides: "ICS calendar parsing, task source abstraction"
provides:
  - "Multi-signal email triage using sender + subject keyword classification"
  - "IMAP email reader with From/Date headers, timeout=30, readonly=True"
  - "Two-stage narrative daily briefing: deterministic assembly + LLM synthesis via ModelGateway"
  - "Graceful fallback to deterministic build_daily_brief() when gateway unavailable"
  - "OpsBriefHandler with optional gateway parameter (backward compatible)"
affects: [daily-briefing, morning-routine, ops-autopilot]

# Tech tracking
tech-stack:
  added: []
  patterns: [two-stage-llm-pipeline, data-condensation-before-prompt, graceful-llm-fallback]

key-files:
  created:
    - engine/tests/test_email_briefing.py
  modified:
    - engine/src/jarvis_engine/ops_sync.py
    - engine/src/jarvis_engine/life_ops.py
    - engine/src/jarvis_engine/handlers/ops_handlers.py
    - engine/src/jarvis_engine/app.py

key-decisions:
  - "Multi-signal triage checks subject keywords first, then sender patterns -- any match returns high"
  - "Keep _email_importance() as backward-compatible wrapper delegating to _triage_email()"
  - "Data summary condensed to ~1500 tokens with truncation (10 events, 10 tasks, 10 emails, 8 meds, 8 bills)"
  - "LLM narrative via gateway.complete() with route_reason='daily_briefing_narrative' for cost tracking"
  - "OpsBriefHandler gateway parameter defaults to None for backward compatibility with existing wiring"

patterns-established:
  - "Two-stage LLM pipeline: assemble deterministic data summary, then pass to LLM for narrative synthesis"
  - "Graceful LLM fallback: try gateway.complete(), catch all exceptions, fall back to deterministic output"
  - "Data condensation: truncate lists, limit string lengths, format as labeled sections before prompt injection"

requirements-completed: [CONN-02, CONN-04]

# Metrics
duration: 6min
completed: 2026-02-23
---

# Phase 4 Plan 2: Email Triage & Narrative Daily Briefing Summary

**Multi-signal email triage with sender+subject classification and two-stage LLM-powered morning briefing via ModelGateway routed to local Ollama with deterministic fallback**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-23T06:00:00Z
- **Completed:** 2026-02-23T06:06:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Enhanced IMAP email reader with From/Date header extraction, timeout=30 safety, and readonly=True for inbox protection
- Built multi-signal _triage_email() that classifies importance using both sender patterns (noreply@, billing@, alert@, security@) and subject keywords (urgent, deadline, expiring, overdue, etc.)
- Created two-stage narrative daily briefing: _assemble_data_summary() condenses snapshot to ~1500 tokens, build_narrative_brief() passes it to local LLM for coherent morning narrative
- Graceful fallback chain: gateway available -> LLM narrative; gateway None or error -> deterministic build_daily_brief()
- Updated OpsBriefHandler and create_app() composition root to optionally wire gateway for narrative generation
- Added 27 comprehensive tests covering email triage, narrative generation, fallback behavior, data truncation, handler integration, and backward compatibility

## Task Commits

Each task was committed atomically:

1. **Task 1: Enhance IMAP email reader with sender/date extraction and multi-signal triage** - `1cab4a8` (feat)
2. **Task 2: Build two-stage narrative daily briefing and update OpsBriefHandler** - `fa14656` (feat)

## Files Created/Modified
- `engine/src/jarvis_engine/ops_sync.py` - Enhanced IMAP reader with From/Date headers, timeout, readonly, and _triage_email() multi-signal classification
- `engine/src/jarvis_engine/life_ops.py` - Added _assemble_data_summary() and build_narrative_brief() for two-stage LLM briefing
- `engine/src/jarvis_engine/handlers/ops_handlers.py` - Updated OpsBriefHandler to accept optional gateway, try narrative brief with fallback
- `engine/src/jarvis_engine/app.py` - Wire gateway to OpsBriefHandler in create_app() composition root
- `engine/tests/test_email_briefing.py` - 27 tests: email triage (10), narrative briefing (5), data summary (8), handler (3), backward compat (1)

## Decisions Made
- Multi-signal triage checks subject keywords first, then sender patterns -- either match triggers "high" importance
- Kept _email_importance() as a backward-compatible wrapper that delegates to _triage_email("", subject)
- Data summary uses truncation limits (10 events, 10 tasks, 10 emails, 8 medications, 8 bills) to stay under ~1500 tokens
- LLM prompt uses route_reason="daily_briefing_narrative" for cost tracking via CostTracker
- OpsBriefHandler gateway=None default ensures existing instantiations (without gateway) continue working
- Gateway wired in create_app() at the Ops section where the variable is already in scope

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required. LLM narrative generation activates automatically when Ollama is running locally.

## Next Phase Readiness
- Email triage and narrative briefing complete -- morning routine now produces actionable LLM-powered narratives
- All Phase 4 plans (01: Calendar & Tasks, 02: Email & Briefing) are complete
- 279 tests pass with zero regressions across the entire test suite
- Ready for Phase 5

## Self-Check: PASSED

- FOUND: engine/src/jarvis_engine/ops_sync.py
- FOUND: engine/src/jarvis_engine/life_ops.py
- FOUND: engine/src/jarvis_engine/handlers/ops_handlers.py
- FOUND: engine/src/jarvis_engine/app.py
- FOUND: engine/tests/test_email_briefing.py
- FOUND: .planning/phases/04-connectors-and-daily-intelligence/04-02-SUMMARY.md
- FOUND: commit 1cab4a8 (Task 1)
- FOUND: commit fa14656 (Task 2)

---
*Phase: 04-connectors-and-daily-intelligence*
*Completed: 2026-02-23*
