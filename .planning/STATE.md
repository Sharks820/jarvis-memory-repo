# Project State

## Project Reference

See: .planning/PROJECT.md
See: .planning/ROADMAP.md (v4.0 Intelligence Activation & Voice Overhaul)

**Core value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.
**Current focus:** v4.0 — Close the learning feedback loop, overhaul voice-to-text, fix UI live-updates, prepare for mobile deployment.

## Current Position

Phase: 2 (Learning System Activation) -- COMPLETE
Current Plan: 3 of 3 (all plans complete)
Status: Phase 2 complete, ready for Phase 3
Last activity: 2026-03-02

Progress (v4.0): [██████░░░░] 50%

## Performance Metrics

**v1.0 Desktop Engine**: SHIPPED (phases 1-9, 18 plans, 473 tests at ship)
**v2.0 Android App**: SHIPPED (phases 10-13, 11 plans)
**v3.0 Hardening**: SHIPPED (2 phases, 4136 tests, 7-pillar security, 4-CLI scan gauntlet clean)
**v4.0 Intelligence & Voice**: IN PROGRESS
- Test count: 4243 passing, 3 skipped, 0 failures
- Source files: 60+ Python modules, 100+ Kotlin files
- Phase 1 (Voice-to-Text Overhaul): COMPLETE — 5 plans, 8 STT requirements verified
- Phase 2 (Learning System Activation): COMPLETE — 3 plans, 8 LEARN requirements verified

## Self-Analysis Findings (informing v4.0 priorities)

### CRITICAL (fixed in Phase 2)
1. ~~Learning trackers write-only~~ — FIXED: preferences injected into prompts, route quality into classifier, usage into daemon
2. ~~Missing route param~~ — FIXED: all 4 dispatch sites pass route/topic
3. ~~No learning feedback loop~~ — FIXED: preferences personalize responses, quality penalties adjust routing

### HIGH
4. db_path.exists() gate — silently disables entire brain on first run
5. MemoryConsolidator not in CQRS bus — can only run via daemon, not mobile API or CLI

### STT Research Findings (informing voice phase)
- Current faster-whisper small.en has ~15-20% WER (terrible for commands)
- NVIDIA Parakeet TDT 0.6B: 6.05% WER, 50x faster than Whisper (best local option)
- Deepgram Nova-3: best cloud option with keyterm prompting
- Silero VAD: replace energy-based VAD for better voice activity detection

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

### Blockers/Concerns
- None currently

## Session Continuity

Last session: 2026-03-02
Stopped at: Completed Phase 2 (Learning System Activation) -- all 3 plans complete, deep bug scan next
Resume file: None
