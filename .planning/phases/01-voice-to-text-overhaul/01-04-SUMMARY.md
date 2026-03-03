---
phase: 01-voice-to-text-overhaul
plan: 04
subsystem: voice
tags: [stt, fallback-chain, silero-vad, wakeword, parakeet, deepgram, groq, faster-whisper, large-v3]

# Dependency graph
requires:
  - phase: 01-voice-to-text-overhaul
    provides: "Silero VAD (Plan 01), Parakeet TDT backend (Plan 02), Deepgram Nova-3 backend (Plan 03)"
provides:
  - "4-tier fallback chain in transcribe_smart(): Parakeet -> Deepgram -> Groq -> faster-whisper large-v3"
  - "Low-confidence fallthrough between backends (CONFIDENCE_RETRY_THRESHOLD=0.6)"
  - "Forced backend modes: parakeet, deepgram, groq, local via JARVIS_STT_BACKEND"
  - "Emergency fallback using faster-whisper large-v3 model"
  - "Silero VAD integration in wake word detection (threshold=0.3)"
  - "VAD state reset after detection and resume for clean slate"
affects: [01-voice-to-text-overhaul, stt-pipeline, wakeword-pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns: [dynamic-function-resolution, fallback-chain-pattern, vad-prefilter-with-rms-fallback]

key-files:
  created: []
  modified:
    - engine/src/jarvis_engine/stt.py
    - engine/src/jarvis_engine/wakeword.py
    - engine/tests/test_stt.py
    - engine/tests/test_wakeword.py

key-decisions:
  - "Used string-based FALLBACK_CHAIN with dynamic getattr() resolution for mock-testability"
  - "Created separate _local_emergency_instance with large-v3 to preserve backward-compatible small.en for forced local mode"
  - "Kept _confidence_retry() function in codebase but removed from transcribe_smart() auto path"
  - "Silero VAD threshold 0.3 for wakeword (more sensitive than default 0.5) to avoid missing soft-spoken wake words"

patterns-established:
  - "Fallback chain: iterate string-keyed backends, resolve via getattr(sys.modules[__name__], fn_name) for testability"
  - "Low-confidence fallthrough: store best_so_far, continue chain if below CONFIDENCE_RETRY_THRESHOLD"
  - "VAD pre-filter with RMS fallback: check self._vad_available, use process_chunk() if available, else RMS energy"

requirements-completed: [STT-05, STT-08]

# Metrics
duration: 25min
completed: 2026-03-03
---

# Phase 1 Plan 4: Fallback Chain & VAD Integration Summary

**4-tier STT fallback chain (Parakeet -> Deepgram -> Groq -> faster-whisper large-v3) with Silero VAD wake word pre-filter replacing RMS energy detection**

## Performance

- **Duration:** 25 min
- **Started:** 2026-03-03T03:05:00Z
- **Completed:** 2026-03-03T03:31:12Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Rewrote transcribe_smart() with FALLBACK_CHAIN iterating 4 backends in priority order, with low-confidence fallthrough
- Added forced backend modes for all 4 backends (parakeet, deepgram, groq, local) via JARVIS_STT_BACKEND env var
- Created _try_local_emergency() with separate large-v3 SpeechToText singleton for maximum accuracy fallback
- Integrated Silero VAD into wake word detection with 0.3 threshold, RMS energy fallback when unavailable
- VAD state properly reset after detection and in resume() for clean slate
- 14 new tests total: 9 for fallback chain, 5 for VAD integration
- All 4196 tests passing (11 skipped, 0 failures)

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite transcribe_smart() with 4-tier fallback chain** - `819989c` (feat)
2. **Task 2: Integrate Silero VAD into wake word detection** - `e533bce` (feat)

## Files Created/Modified
- `engine/src/jarvis_engine/stt.py` - FALLBACK_CHAIN constant, _BACKEND_FN_MAP, _try_local_emergency(), rewritten transcribe_smart() auto mode, forced parakeet/deepgram modes
- `engine/src/jarvis_engine/wakeword.py` - Silero VAD initialization in start(), VAD pre-filter replacing RMS energy, VAD reset after detection and in resume(), _vad/_vad_available instance vars
- `engine/tests/test_stt.py` - 9 new fallback chain tests, ~10 existing tests updated for new auto mode behavior
- `engine/tests/test_wakeword.py` - 5 new VAD integration tests (integration, reset, RMS fallback, instance storage, resume reset)

## Decisions Made
- **String-based FALLBACK_CHAIN with getattr()**: Direct function references in FALLBACK_CHAIN broke unittest mock patches (list held original refs, not mocked ones). Using string keys with dynamic `getattr(sys.modules[__name__], fn_name)` resolution ensures mocks are picked up at call time.
- **Separate _local_emergency_instance**: Created a dedicated SpeechToText singleton with model_size="large-v3" rather than changing the default model. This preserves backward compatibility for users with JARVIS_STT_BACKEND=local who expect small.en behavior.
- **Kept _confidence_retry()**: The old single-retry function was kept in the codebase but removed from transcribe_smart(). The fallback chain IS the retry mechanism now. Removal deferred to avoid breaking any external callers.
- **VAD threshold 0.3 for wakeword**: Lower than the default 0.5 to be more sensitive for wake word detection, since missing a wake word is worse than occasionally running a few extra ML inferences.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed FALLBACK_CHAIN testability with dynamic resolution**
- **Found during:** Task 1 (fallback chain implementation)
- **Issue:** Initial implementation used direct function references in FALLBACK_CHAIN tuple list. When tests patched `_try_parakeet` etc., the mocked functions were never called because the list held references to the original functions.
- **Fix:** Changed FALLBACK_CHAIN to list of string keys, added _BACKEND_FN_MAP for function name lookup, used getattr(sys.modules[__name__], fn_name) for dynamic resolution at call time
- **Files modified:** engine/src/jarvis_engine/stt.py
- **Verification:** All 9 fallback chain tests pass with proper mock interception
- **Committed in:** 819989c

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential for testability. The initial approach was technically correct at runtime but broke the test mock pattern. No scope creep.

## Issues Encountered
- Multiple existing tests (10+) needed updating because they relied on the old 2-backend auto mode (groq-then-local) or mocked `_confidence_retry()`. All were updated to mock the 4 fallback chain functions appropriately.

## User Setup Required

None - no external service configuration required. All backends (Parakeet, Deepgram, Groq) were configured in prior plans. The fallback chain degrades gracefully when API keys or models are unavailable.

## Next Phase Readiness
- Full 4-tier STT pipeline operational: Parakeet TDT (local) -> Deepgram Nova-3 (cloud) -> Groq Whisper (cloud) -> faster-whisper large-v3 (emergency)
- Wake word detection upgraded with ML-based voice activity detection
- Plan 05 (final plan) can proceed: likely testing/polish/documentation
- All 4196 tests passing with 0 failures

## Self-Check: PASSED

- All 4 source/test files exist
- Both task commits verified (819989c, e533bce)
- SUMMARY.md created at expected path
- 4196 tests passing, 11 skipped, 0 failures

---
*Phase: 01-voice-to-text-overhaul*
*Completed: 2026-03-03*
