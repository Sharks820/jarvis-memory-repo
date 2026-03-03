---
phase: 01-voice-to-text-overhaul
plan: 02
subsystem: voice
tags: [stt, parakeet, onnx-asr, nvidia, speech-to-text, transcription]

# Dependency graph
requires:
  - phase: 01-voice-to-text-overhaul
    provides: "Research findings on Parakeet TDT 0.6B via onnx-asr (01-RESEARCH.md)"
provides:
  - "_try_parakeet() backend function for Parakeet TDT 0.6B transcription"
  - "_parakeet_model global with thread-safe lazy loading"
  - "8 comprehensive tests covering success, errors, singleton, confidence"
affects: [01-voice-to-text-overhaul/01-04, stt-fallback-chain]

# Tech tracking
tech-stack:
  added: [onnx-asr (optional, lazy import)]
  patterns: [double-checked-locking singleton for model loading, baseline confidence from known WER]

key-files:
  created: []
  modified:
    - engine/src/jarvis_engine/stt.py
    - engine/tests/test_stt.py

key-decisions:
  - "Baseline confidence 0.94 when log probs unavailable (derived from Parakeet 6.05% WER)"
  - "with_timestamps() attempted first for log prob access, falls back to base model silently"
  - "onnx-asr lazy-imported inside function body for zero startup cost"

patterns-established:
  - "Parakeet backend pattern: _try_parakeet() matches _try_groq()/_try_local() signature"
  - "Confidence from token logprobs: min(1.0, max(0.0, 1.0 + avg_logprob))"

requirements-completed: [STT-01, STT-06]

# Metrics
duration: 3min
completed: 2026-03-03
---

# Phase 01 Plan 02: Parakeet TDT Backend Summary

**NVIDIA Parakeet TDT 0.6B backend via onnx-asr with lazy loading, thread-safe singleton, and baseline confidence scoring**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-03T03:01:40Z
- **Completed:** 2026-03-03T03:05:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Added `_try_parakeet()` backend function following the exact pattern of `_try_groq()` and `_try_local()`
- Thread-safe lazy model loading with double-checked locking pattern
- Confidence scoring uses token log probs when available from onnx-asr timestamps model, falls back to 0.94 baseline (Parakeet's known 6.05% WER)
- Graceful degradation: returns None when onnx-asr not installed or model errors occur
- 8 new tests covering all code paths, all passing alongside 84 existing STT tests (92 total)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add _try_parakeet() backend function** - `2232459` (feat)
2. **Task 2: Add tests for Parakeet backend** - `7c21215` (test)

## Files Created/Modified
- `engine/src/jarvis_engine/stt.py` - Added `_try_parakeet()` function, `_parakeet_model` global, `_parakeet_lock` threading lock, updated module docstring
- `engine/tests/test_stt.py` - Added 8 tests: success, import error, model error, empty result, numpy array input, file path input, lazy model load singleton, confidence baseline

## Decisions Made
- Used baseline confidence of 0.94 when log probabilities are unavailable, derived from Parakeet's published 6.05% WER on LibriSpeech
- Attempted `model.with_timestamps()` for log probability access; falls back silently to base model if unavailable
- onnx-asr imported lazily inside function body (not at module level) to avoid any startup cost when Parakeet is not used
- numpy arrays pass `sample_rate=16000` to `recognize()`, file paths do not (onnx-asr auto-detects from file)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - onnx-asr is an optional dependency. When not installed, `_try_parakeet()` gracefully returns None.

## Next Phase Readiness
- `_try_parakeet()` is ready to be wired into the `transcribe_smart()` fallback chain in Plan 04
- No modifications to existing `transcribe_smart()` were made (as specified in the plan)
- All existing STT backends and tests remain unaffected

## Self-Check: PASSED

All artifacts verified:
- [x] engine/src/jarvis_engine/stt.py exists
- [x] engine/tests/test_stt.py exists
- [x] .planning/phases/01-voice-to-text-overhaul/01-02-SUMMARY.md exists
- [x] Commit 2232459 exists (Task 1)
- [x] Commit 7c21215 exists (Task 2)
- [x] 92 STT tests passing (84 existing + 8 new)

---
*Phase: 01-voice-to-text-overhaul*
*Completed: 2026-03-03*
