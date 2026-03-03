# Requirements: Jarvis v4.0 — Intelligence Activation & Voice Overhaul

**Defined:** 2026-03-02
**Core Value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## Voice-to-Text Overhaul (STT)

- **STT-01**: Replace faster-whisper small.en with NVIDIA Parakeet TDT 0.6B as primary local STT model (target: <8% WER on conversational speech)
- [x] **STT-02**: Integrate Deepgram Nova-3 as cloud STT provider with keyterm prompting for proper nouns and domain vocabulary
- **STT-03**: Replace energy-based VAD with Silero VAD for accurate voice activity detection (pre-speech, end-of-speech, silence handling)
- **STT-04**: Implement streaming/chunked STT pipeline — detect speech start, transcribe incrementally, detect speech end, finalize
- **STT-05**: Wake word detection continues to work with new VAD (Silero feeds into OpenWakeWord or existing porcupine)
- **STT-06**: STT confidence scoring accurate and actionable — low-confidence triggers re-listen or clarification prompt
- [x] **STT-07**: Personal vocabulary and entity correction integrated into STT post-processing (names, places, custom terms)
- **STT-08**: Fallback chain: Parakeet TDT (local) → Deepgram Nova-3 (cloud) → Groq Whisper (cloud) → faster-whisper large-v3 (local emergency)

## Learning System Activation (LEARN)

- **LEARN-01**: PreferenceTracker.get_preferences() wired into QueryHandler — detected communication/format preferences injected into LLM system prompt
- **LEARN-02**: ResponseFeedbackTracker.get_route_quality() wired into IntentClassifier — routes with poor satisfaction penalized in classification scoring
- **LEARN-03**: UsagePatternTracker.predict_context() wired into daemon proactive checks — time-of-day patterns drive proactive suggestions
- **LEARN-04**: LearnInteractionCommand carries route name — record_feedback() and record_interaction() receive actual route, not empty string
- **LEARN-05**: compute_relevance_score() integrated into memory retrieval — hybrid_search results ranked by frequency, recency, and KG connections
- **LEARN-06**: classify_tier_by_relevance() integrated into MemoryConsolidator — auto-archive stale memories, promote hot memories
- **LEARN-07**: capture_knowledge_metrics() wired into BrainStatusCommand or dashboard — KG health visible to user
- **LEARN-08**: Intelligence dashboard shows per-route quality scores, preference summary, peak usage hours

## Widget & UI Live Updates (UI)

- **UI-01**: Widget brain status updates live when missions are cancelled, completed, or retried
- **UI-02**: Widget learning indicator updates live when preferences, facts, or memories are acquired
- **UI-03**: Activity feed in primary conversation display shows real-time bot activity (daemon cycle events, mission progress, learning events, sync status)
- **UI-04**: Activity feed entries timestamped and categorized (learning, mission, sync, proactive, security)
- **UI-05**: Widget response display handles all command types with clear, parsed output (not raw stdout)

## Platform Stability (STAB)

- **STAB-01**: Fix db_path.exists() gate — create database on first run instead of silently disabling brain
- **STAB-02**: Add logging to all silent except blocks in desktop_widget.py (at least non-UI-lifecycle blocks)
- **STAB-03**: MemoryConsolidator exposed through CQRS command bus for CLI and mobile API access
- **STAB-04**: Proactive triggers show diagnostic when no connector data available (not silent empty)
- **STAB-05**: All functions verified end-to-end with passing tests (target: 4200+ tests)

## Mobile App Readiness (MOB)

- **MOB-01**: Mobile-desktop sync verified end-to-end — memory, learning, KG facts flow both directions
- **MOB-02**: Mobile API endpoints tested for reliability under real S25 Ultra usage patterns
- **MOB-03**: Learning sync — mobile interactions contribute to desktop learning trackers (preferences, feedback, usage)
- **MOB-04**: Offline command queue verified — commands cache when desktop unreachable, flush on reconnect
- **MOB-05**: Android app build verified against current desktop API surface (no stale endpoints)

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| STT-01 | Phase 1 | Pending |
| STT-02 | Phase 1 | Complete |
| STT-03 | Phase 1 | Pending |
| STT-04 | Phase 1 | Pending |
| STT-05 | Phase 1 | Pending |
| STT-06 | Phase 1 | Pending |
| STT-07 | Phase 1 | Complete |
| STT-08 | Phase 1 | Pending |
| LEARN-01 through LEARN-08 | Phase 2 | Pending |
| UI-01 through UI-05 | Phase 3 | Pending |
| STAB-01 through STAB-05 | Phase 4 | Pending |
| MOB-01 through MOB-05 | Phase 5 | Pending |

**Coverage:**
- Total requirements: 31
- Phase 1 (Voice): 8 requirements
- Phase 2 (Learning): 8 requirements
- Phase 3 (UI): 5 requirements
- Phase 4 (Stability): 5 requirements
- Phase 5 (Mobile): 5 requirements
- Mapped: 31 / Unmapped: 0

---
*Requirements defined: 2026-03-02*
