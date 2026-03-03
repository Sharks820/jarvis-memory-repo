---
phase: 01-voice-to-text-overhaul
plan: 03
subsystem: voice
tags: [deepgram, nova-3, stt, keyterm-prompting, cloud-stt, httpx]

# Dependency graph
requires:
  - phase: 01-voice-to-text-overhaul
    provides: "existing stt.py backend pattern, _numpy_to_wav_bytes helper, TranscriptionResult dataclass"
provides:
  - "_try_deepgram() cloud STT backend with Deepgram Nova-3"
  - "_load_keyterms() helper for personal vocabulary boosting"
  - "Keyterm prompting from personal_vocab.txt (19 terms, parenthetical-stripped)"
affects: [01-voice-to-text-overhaul, fallback-chain-rewrite]

# Tech tracking
tech-stack:
  added: [deepgram-nova3-api]
  patterns: [httpx-rest-api, keyterm-prompting, tuple-params-for-repeated-query-keys]

key-files:
  created: []
  modified:
    - engine/src/jarvis_engine/stt.py
    - engine/tests/test_stt.py

key-decisions:
  - "Used httpx REST API directly instead of deepgram-sdk to avoid SDK version uncertainty and keep dependency minimal"
  - "Stripped parenthetical annotations from personal_vocab.txt for clean Deepgram keyword boosting"
  - "Used list-of-tuples for params to support repeated 'keywords' query parameters"

patterns-established:
  - "Keyterm loading: _load_keyterms() with process-lifetime cache and parenthetical stripping"
  - "Cloud STT backend pattern: check env var first, return None immediately if not set (zero latency penalty)"

requirements-completed: [STT-02, STT-07]

# Metrics
duration: 7min
completed: 2026-03-02
---

# Phase 1 Plan 3: Deepgram Nova-3 Backend Summary

**Deepgram Nova-3 cloud STT with keyterm prompting from personal_vocab.txt for proper noun accuracy**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-03T03:01:37Z
- **Completed:** 2026-03-03T03:08:45Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Added `_try_deepgram()` cloud STT function using Deepgram Nova-3 model via httpx REST API
- Added `_load_keyterms()` helper that reads personal_vocab.txt, strips parenthetical annotations, and caches 19 domain-specific terms
- Deepgram keywords boosting enables proper noun recognition (Conner, Jarvis, Ollama, Kimi K2, etc.)
- 8 new tests covering keyterm loading, caching, API success/error paths, WAV conversion, and import errors
- All 100 STT tests passing (50 existing + 8 Parakeet + 8 Deepgram + 34 prior tests)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add _try_deepgram() backend with keyterm loading** - `7c21215` (feat)
2. **Task 2: Add tests for Deepgram backend and keyterm loading** - `aa38826` (test)

## Files Created/Modified
- `engine/src/jarvis_engine/stt.py` - Added _load_keyterms(), _try_deepgram() with Nova-3 REST API, keyterm cache
- `engine/tests/test_stt.py` - 8 new Deepgram/keyterm tests (D1-D8)

## Decisions Made
- **httpx REST API over deepgram-sdk**: The Deepgram Python SDK v6 API surface was uncertain per research (multiple possible method signatures). Using httpx REST API directly gives full control over request params, avoids adding a new dependency (httpx already used for Groq), and allows clean repeated query params for keywords via list-of-tuples.
- **Parenthetical stripping**: personal_vocab.txt entries like "Conner (not Connor, Conor)" contain helpful annotations for humans but are not suitable as Deepgram keywords. Extracting just the primary term ("Conner") gives clean keyword boosting.
- **Process-lifetime caching**: _load_keyterms() reads the vocab file once and caches in module-level global, since the file is static and re-reading per request would be wasteful.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stripped parenthetical annotations from keyterms**
- **Found during:** Task 1 (keyterm loader implementation)
- **Issue:** personal_vocab.txt entries include parenthetical annotations (e.g., "Conner (not Connor, Conor)") which are not suitable as Deepgram keywords
- **Fix:** Added parenthetical stripping in _load_keyterms() to extract only the primary term
- **Files modified:** engine/src/jarvis_engine/stt.py
- **Verification:** _load_keyterms() returns clean terms like "Conner" instead of "Conner (not Connor, Conor)"
- **Committed in:** 7c21215

---

**Total deviations:** 1 auto-fixed (1 bug prevention)
**Impact on plan:** Essential for correctness - passing full annotated lines as Deepgram keywords would not boost recognition properly. No scope creep.

## Issues Encountered
- Task 1 implementation was committed alongside Plan 02 (Parakeet) changes in a prior session. The code was already in HEAD when this executor started, so Task 1 was effectively pre-committed. Task 2 tests committed cleanly as a new commit.

## User Setup Required

**External service requires manual configuration.** The Deepgram backend requires:
- **DEEPGRAM_API_KEY** environment variable: Get from [Deepgram Console](https://console.deepgram.com/) -> Settings -> API Keys -> Create Key
- **Verification:** `python -c "from jarvis_engine.stt import _try_deepgram; import numpy as np; print(_try_deepgram(np.zeros(16000, dtype=np.float32), language='en'))"`
- Without the API key, `_try_deepgram()` returns None immediately (zero latency penalty) and the fallback chain skips to the next backend.

## Next Phase Readiness
- Deepgram Nova-3 backend ready for integration into fallback chain (Plan 04)
- All 3 new backends now available: Parakeet TDT (local), Deepgram Nova-3 (cloud), existing Groq Whisper (cloud)
- Plan 04 (Fallback chain rewrite) can wire all backends into transcribe_smart()

---
*Phase: 01-voice-to-text-overhaul*
*Completed: 2026-03-02*
