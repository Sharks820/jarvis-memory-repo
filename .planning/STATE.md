# Project State

## Project Reference

See: .planning/PROJECT.md
See: .planning/ROADMAP.md (v4.0 Intelligence Activation & Voice Overhaul)

**Core value:** Jarvis learns from everything, never forgets, never regresses, and becomes more useful every single day.
**Current focus:** v4.0 — Close the learning feedback loop, overhaul voice-to-text, fix UI live-updates, prepare for mobile deployment.

## Current Position

Phase: 1 (Voice-to-Text Overhaul) -- COMPLETE
Current Plan: 5 of 5 (all plans complete)
Status: Phase 1 complete, ready for Phase 2
Last activity: 2026-03-03

Progress (v4.0): [████░░░░░░] 30%

## Performance Metrics

**v1.0 Desktop Engine**: SHIPPED (phases 1-9, 18 plans, 473 tests at ship)
**v2.0 Android App**: SHIPPED (phases 10-13, 11 plans)
**v3.0 Hardening**: SHIPPED (2 phases, 4136 tests, 7-pillar security, 4-CLI scan gauntlet clean)
**v4.0 Intelligence & Voice**: IN PROGRESS
- Test count: 4212 passing (220 STT+VAD+wakeword+postprocess tests), 5 skipped, 0 failures
- Source files: 60+ Python modules, 100+ Kotlin files
- Self-analysis: 32 findings (4 CRITICAL, 3 HIGH, 4 MEDIUM, 12 LOW)

## Self-Analysis Findings (informing v4.0 priorities)

### CRITICAL (must fix in v4.0)
1. Learning trackers write-only — PreferenceTracker, ResponseFeedbackTracker, UsagePatternTracker collect data but NOTHING reads it
2. Missing route param — record_feedback() and record_interaction() called without route/topic, all data stored with empty strings
3. No learning feedback loop — learning system collects preferences but never applies them to personalize responses

### HIGH
4. db_path.exists() gate — silently disables entire brain on first run
5. MemoryConsolidator not in CQRS bus — can only run via daemon, not mobile API or CLI

### STT Research Findings (informing voice phase)
- Current faster-whisper small.en has ~15-20% WER (terrible for commands)
- NVIDIA Parakeet TDT 0.6B: 6.05% WER, 50x faster than Whisper (best local option)
- Deepgram Nova-3: best cloud option with keyterm prompting
- Silero VAD: replace energy-based VAD for better voice activity detection
- RealtimeSTT library: proven architecture pattern

## Accumulated Context

### Decisions
- v4.0 milestone covers: voice overhaul, learning activation, UI live-updates, platform stability, mobile readiness
- STT target: Parakeet TDT 0.6B (local) + Deepgram Nova-3 (cloud) + Silero VAD
- Learning activation: wire tracker read methods into QueryHandler, IntentClassifier, and dashboard
- Deepgram backend: used httpx REST API directly instead of deepgram-sdk (avoids SDK version uncertainty)
- Keyterm loading: strip parenthetical annotations from personal_vocab.txt for clean Deepgram keywords
- Parakeet TDT backend: baseline confidence 0.94 when log probs unavailable (from 6.05% WER)
- Parakeet model: with_timestamps() attempted first for log prob access, silent fallback to base model
- Fallback chain: string-based FALLBACK_CHAIN with dynamic getattr() resolution for mock-testability
- Separate _local_emergency_instance with large-v3 preserves backward-compatible small.en for forced local mode
- Wake word VAD threshold 0.3 (more sensitive than default 0.5) to avoid missing soft-spoken wake words
- Phase 1 complete: all 8 STT requirements verified with dedicated tests, 4212 tests passing
- Integration tests confirm full pipeline: VAD -> recording -> transcription -> fallback -> post-processing -> callers

### Blockers/Concerns
- None currently

## Session Continuity

Last session: 2026-03-03
Stopped at: Completed Phase 1 (Voice-to-Text Overhaul) -- all 5 plans complete, 01-05-PLAN.md verified
Resume file: None
