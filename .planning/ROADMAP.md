# Roadmap: Jarvis v5.0 — Reliability, Continuity, and Autonomous Learning

## Overview

v1.0 through v4.0 milestones are shipped. v5.0 is a reliability-first rebuild pass focused on
eliminating breakage, reducing memory pressure, preserving context across model switches, and
making learning/activity visibly real in both desktop and mobile experiences.

This roadmap is designed for GSD execution: small plans, strict verification gates, and no
feature acceptance without production-like soak evidence.

## Baseline (2026-03-05)

- `pytest engine/tests -q`: 4433 passed, 9 skipped, 0 failed
- `ruff check engine/src engine/tests`: clean
- `mypy engine/src`: 116 errors in 35 files (typed quality debt + missing stubs)
- `bandit -r engine/src/jarvis_engine`: 165 findings (1 high, 50 medium, 114 low)
- Reported user symptoms:
  1. Memory/CPU intensity causes instability after 1-2 commands
  2. Long responses cause context loss and HTTP failures
  3. Learning missions/activity do not show trustworthy real-time progress
  4. Cross-provider switching can reset or fragment conversation context
  5. Voice recognition quality still misses basic utterances in real use

## v5 Phases

- [ ] **Phase 1: Reliability Core + Resource Control**
  - Goal: no hard breaks in normal workflows, bounded resource use, deterministic failure handling
  - Requirements: REL-01..REL-08, PERF-01..PERF-04
  - First plan: `phases/14-world-class-assistant-reliability/14-01-PLAN.md`

- [ ] **Phase 2: Context Continuity Across LLM/CLI Providers**
  - Goal: switch bot/provider without restarting from zero context
  - Requirements: CTX-01..CTX-06
  - Planned artifacts: 14-02 and 14-03

- [ ] **Phase 3: Learning Missions + Live Intelligence Telemetry**
  - Goal: real mission progress bars, activity stream, measurable intelligence growth
  - Requirements: LM-01..LM-08, OBS-01..OBS-04
  - Planned artifacts: 14-04 and 14-05

- [ ] **Phase 4: Voice Accuracy and Correction Loop**
  - Goal: robust STT for basic conversational commands in real environments
  - Requirements: STT-09..STT-14
  - Planned artifact: 14-06

- [ ] **Phase 5: Mobile Tasking + Async Completion Delivery**
  - Goal: assign task from phone, execute on desktop, return completion (including delivery channels)
  - Requirements: MOB-06..MOB-12
  - Planned artifacts: 14-07 and 14-08

- [ ] **Phase 6: Security Expansion + Release Gate**
  - Goal: strengthen already-solid security with deeper abuse containment and release evidence
  - Requirements: SEC-01..SEC-06
  - Planned artifact: 14-09

## Completion Gate (v5)

v5 only closes when all are true:

1. 8-hour soak run: zero crashes, zero deadlocks, no unbounded memory growth
2. Cross-provider context handoff passes all continuity tests
3. Mission UI shows truthful step/state/progress for all long-running jobs
4. Voice accuracy acceptance set passes in real-room recordings
5. Mobile-to-desktop task loop is demonstrably reliable end-to-end
6. Security, static checks, and bug scans meet enforced thresholds

## Prior Milestone Status

- v4.0 remains complete and archived: 31 requirements verified, all 5 phases shipped.
