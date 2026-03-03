# Roadmap: Jarvis v4.0 — Intelligence Activation & Voice Overhaul

## Overview

v1.0 Desktop Engine, v2.0 Android App, and v3.0 Hardening are all shipped. v4.0 makes Jarvis actually intelligent: closing the learning feedback loop so accumulated data personalizes responses, completely overhauling voice-to-text for reliable speech understanding, fixing UI live-updating, and preparing for mobile deployment on S25 Ultra.

## Phases

- [x] **Phase 1: Voice-to-Text Overhaul** — Replace failing STT pipeline with best-in-class models. Parakeet TDT 0.6B (local), Deepgram Nova-3 (cloud), Silero VAD, streaming pipeline, fallback chain. (completed 2026-03-03)
- [ ] **Phase 2: Learning System Activation** — Close the write-only feedback loop. Wire preference/feedback/usage tracker reads into QueryHandler, IntentClassifier, and dashboard. Pass route params. Integrate relevance scoring.
- [ ] **Phase 3: Widget & UI Live Updates** — Fix brain UI live-updating for all functions. Add activity feed to conversation display. Parse and display all command outputs cleanly.
- [ ] **Phase 4: Platform Stability** — Fix db_path gate, add logging to silent blocks, expose MemoryConsolidator via CQRS, proactive trigger diagnostics. End-to-end verification scan.
- [ ] **Phase 5: Mobile App Readiness** — Verify mobile-desktop sync, learning sync, offline queue, API surface compatibility. Prepare for S25 Ultra deployment.

## Phase Details

### Phase 1: Voice-to-Text Overhaul
**Goal**: Reliable voice understanding — commands and conversational speech recognized accurately
**Requirements**: STT-01 through STT-08
**Plans:** 5/5 plans complete

Plans:
- [x] 01-01-PLAN.md — Silero VAD module + record_from_microphone integration
- [x] 01-02-PLAN.md — NVIDIA Parakeet TDT 0.6B backend via onnx-asr
- [x] 01-03-PLAN.md — Deepgram Nova-3 backend with keyterm prompting
- [x] 01-04-PLAN.md — Fallback chain rewrite + wake word VAD integration
- [x] 01-05-PLAN.md — Integration testing + end-to-end verification

**Success Criteria**:
  1. Local STT WER < 8% on conversational speech (currently ~15-20%)
  2. Cloud STT available as fallback with keyterm prompting
  3. Silero VAD correctly detects speech start/end (no premature cutoff, no hanging)
  4. Wake word detection unaffected by VAD change
  5. Personal vocabulary (names, places) recognized correctly
  6. Fallback chain works: Parakeet -> Deepgram -> Groq Whisper -> faster-whisper
**Key Research**: NVIDIA Parakeet TDT 0.6B (onnx-asr), Deepgram Python SDK, Silero VAD

### Phase 2: Learning System Activation
**Goal**: Jarvis actually uses what it learns — preferences shape responses, feedback improves routing, usage patterns drive proactive features
**Requirements**: LEARN-01 through LEARN-08
**Depends on**: None (independent of Phase 1)
**Plans:** 3 plans

Plans:
- [ ] 02-01-PLAN.md — Fix data quality (route/topic in LearnInteractionCommand) + bus exposure
- [ ] 02-02-PLAN.md — Wire preferences into prompts, route quality into classifier, usage into daemon
- [ ] 02-03-PLAN.md — Relevance scoring in search, tier management in consolidator, learning dashboard

**Success Criteria**:
  1. User preferences (detected over time) appear in LLM system prompts
  2. Routes with poor feedback scores deprioritized in IntentClassifier
  3. Usage pattern predictions populated with real route/topic data
  4. Intelligence dashboard shows per-route quality and preference summary
  5. Relevance scoring ranks memory retrieval results
  6. Memory consolidator auto-archives stale, promotes hot

### Phase 3: Widget & UI Live Updates
**Goal**: User always sees what Jarvis is doing — live status updates, activity feed, clean output
**Requirements**: UI-01 through UI-05
**Depends on**: Phase 2 (learning events feed into activity display)
**Success Criteria**:
  1. Cancelling a mission immediately updates brain status in widget
  2. Learning events (new preference, fact, memory) show indicator
  3. Activity feed scrollable in conversation window with timestamps
  4. All command types produce clean, user-readable output

### Phase 4: Platform Stability
**Goal**: Zero silent failures, all subsystems properly initialized, comprehensive test coverage
**Requirements**: STAB-01 through STAB-05
**Depends on**: Phases 1-3 (stability scan validates all new work)
**Success Criteria**:
  1. Fresh install creates DB automatically (no exists() gate)
  2. Silent except blocks have logging
  3. MemoryConsolidator accessible via CLI command and mobile API
  4. Proactive triggers report when no data available
  5. 4200+ tests passing

### Phase 5: Mobile App Readiness
**Goal**: S25 Ultra deployment ready — sync works, learning flows, offline queue reliable
**Requirements**: MOB-01 through MOB-05
**Depends on**: Phases 1-4 (desktop must be stable first)
**Success Criteria**:
  1. Memory sync bidirectional and verified
  2. Mobile interactions update desktop learning trackers
  3. Offline queue caches and flushes correctly
  4. Android app builds and connects to current API surface
  5. Voice input on Android reaches desktop and gets accurate STT

## Progress

| Phase | Plans Complete | Status | Date |
|-------|---------------|--------|------|
| 1. Voice-to-Text Overhaul | 5/5 | Complete | 2026-03-03 |
| 2. Learning System Activation | 0/3 | Planned | |
| 3. Widget & UI Live Updates | 0/? | Not started | |
| 4. Platform Stability | 0/? | Not started | |
| 5. Mobile App Readiness | 0/? | Not started | |
