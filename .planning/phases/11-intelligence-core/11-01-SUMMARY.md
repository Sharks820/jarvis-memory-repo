---
phase: 11-intelligence-core
plan: 01
subsystem: callscreen
tags: [android, callscreening, room, hilt, spam-detection, telecom]

# Dependency graph
requires:
  - phase: 10-foundation-and-daily-driver
    provides: "Room database, Hilt DI, Retrofit API client, foreground service, Settings UI"
provides:
  - "CallScreeningService intercepting incoming calls before phone rings"
  - "SpamScorer with configurable thresholds for block/silence/voicemail/allow"
  - "SpamEntity Room table with SpamDao for CRUD operations"
  - "SpamDatabaseSync pulling candidates from desktop phone_guard via /command"
  - "Settings UI for call screening toggle, threshold sliders, and role request"
affects: [11-intelligence-core, notification-intelligence]

# Tech tracking
tech-stack:
  added: []
  patterns: [android-callscreening-service, room-upsert-sync, threshold-based-scoring]

key-files:
  created:
    - android/app/src/main/java/com/jarvis/assistant/feature/callscreen/CallScreeningService.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/callscreen/SpamScorer.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/callscreen/SpamDatabase.kt
    - android/app/src/main/java/com/jarvis/assistant/data/entity/SpamEntity.kt
    - android/app/src/main/java/com/jarvis/assistant/data/dao/SpamDao.kt
  modified:
    - android/app/src/main/java/com/jarvis/assistant/data/JarvisDatabase.kt
    - android/app/src/main/java/com/jarvis/assistant/di/AppModule.kt
    - android/app/src/main/java/com/jarvis/assistant/api/JarvisApi.kt
    - android/app/src/main/java/com/jarvis/assistant/api/models/ApiModels.kt
    - android/app/src/main/AndroidManifest.xml
    - android/app/src/main/java/com/jarvis/assistant/service/JarvisService.kt
    - android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsScreen.kt
    - android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsViewModel.kt

key-decisions:
  - "Used /command endpoint exclusively for spam sync since /spam/candidates not yet on desktop"
  - "SpamScorer reads thresholds from SharedPreferences (not EncryptedSharedPreferences) for performance in call screening path"
  - "CallScreeningService uses ComponentActivity-based role launcher instead of AppCompatActivity to match existing dependency tree"
  - "Spam DB sync throttled to 10-minute intervals within the existing 30s sync loop"

patterns-established:
  - "Feature module pattern: feature/callscreen/ package for call screening domain"
  - "Threshold-based scoring: configurable block/silence/voicemail thresholds stored in SharedPreferences"
  - "Desktop sync pattern: send command via /command endpoint, parse stdout_tail response"

requirements-completed: [CALL-01, CALL-02, CALL-03, CALL-04]

# Metrics
duration: 8min
completed: 2026-02-24
---

# Phase 11 Plan 01: Call Screening Summary

**Android CallScreeningService with local spam DB sync from desktop phone_guard, threshold-based scoring, and Settings UI for configuration**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-24T00:58:51Z
- **Completed:** 2026-02-24T01:07:00Z
- **Tasks:** 2
- **Files modified:** 13

## Accomplishments
- CallScreeningService intercepts incoming calls before ringing, scores against local spam DB, applies block/silence/voicemail/allow
- SpamScorer ports normalizeNumber logic from desktop phone_guard.py with configurable threshold actions
- SpamDatabaseSync pulls pre-computed spam candidates from desktop via /command endpoint with graceful degradation
- Settings UI provides enable toggle, three threshold sliders, spam DB count display, and call screening role request button

## Task Commits

Each task was committed atomically:

1. **Task 1: Room entities, DAOs, spam scorer, and desktop sync** - `f5fd978` (feat)
2. **Task 2: CallScreeningService, manifest registration, and Settings UI** - `bf3d7e9` (feat)

**Plan metadata:** (pending)

## Files Created/Modified
- `android/app/src/main/java/com/jarvis/assistant/data/entity/SpamEntity.kt` - Room entity for spam number records with score, reasons, userAction
- `android/app/src/main/java/com/jarvis/assistant/data/dao/SpamDao.kt` - Room DAO with findByNumber, getAllFlow, upsertAll, deleteStale
- `android/app/src/main/java/com/jarvis/assistant/data/JarvisDatabase.kt` - Bumped to v2, added SpamEntity and SpamDao
- `android/app/src/main/java/com/jarvis/assistant/feature/callscreen/SpamScorer.kt` - Scoring engine with normalizeNumber port and threshold-based action determination
- `android/app/src/main/java/com/jarvis/assistant/feature/callscreen/SpamDatabase.kt` - Sync manager pulling candidates from desktop via /command endpoint
- `android/app/src/main/java/com/jarvis/assistant/feature/callscreen/CallScreeningService.kt` - Android CallScreeningService with Hilt injection and role management utilities
- `android/app/src/main/java/com/jarvis/assistant/di/AppModule.kt` - Added SpamDao provider
- `android/app/src/main/java/com/jarvis/assistant/api/JarvisApi.kt` - Added /spam/candidates endpoint for future use
- `android/app/src/main/java/com/jarvis/assistant/api/models/ApiModels.kt` - Added SpamCandidatesResponse and SpamCandidateDto
- `android/app/src/main/AndroidManifest.xml` - Added call screening permissions and service registration
- `android/app/src/main/java/com/jarvis/assistant/service/JarvisService.kt` - Added spam DB sync with 10-minute throttle
- `android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsScreen.kt` - Added Call Screening section with toggle, sliders, count, and permission button
- `android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsViewModel.kt` - Added call screening state management and SpamDao count observation

## Decisions Made
- Used /command endpoint exclusively for spam sync since /spam/candidates is not yet implemented on the desktop engine. SpamDatabaseSync sends "Jarvis, run spam scan" then "Jarvis, show spam report" to trigger and retrieve data.
- SpamScorer reads thresholds from SharedPreferences (not EncryptedSharedPreferences) for performance in the call screening hot path. Thresholds are non-sensitive configuration.
- Changed registerCallScreeningRoleLauncher to use ComponentActivity instead of AppCompatActivity since the project does not include the appcompat dependency (uses activity-compose with FragmentActivity).
- Spam DB sync is throttled to every 10 minutes within the existing 30-second sync loop, balancing freshness with API load.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Changed AppCompatActivity to ComponentActivity in role launcher**
- **Found during:** Task 2 (CallScreeningService)
- **Issue:** Plan referenced AppCompatActivity for registerCallScreeningRoleLauncher, but project has no appcompat dependency
- **Fix:** Used ComponentActivity (parent of FragmentActivity which MainActivity extends) instead
- **Files modified:** CallScreeningService.kt
- **Verification:** Import resolves correctly against existing activity-compose dependency
- **Committed in:** bf3d7e9 (Task 2 commit)

**2. [Rule 2 - Missing Critical] Added call screening enabled check in onScreenCall**
- **Found during:** Task 2 (CallScreeningService)
- **Issue:** Plan code snippet did not check if call screening is enabled before scoring
- **Fix:** Added SharedPreferences check for call_screen_enabled before proceeding with score
- **Files modified:** CallScreeningService.kt
- **Verification:** When disabled, calls pass through without scoring
- **Committed in:** bf3d7e9 (Task 2 commit)

**3. [Rule 2 - Missing Critical] Added error handling in onScreenCall**
- **Found during:** Task 2 (CallScreeningService)
- **Issue:** Plan code snippet had no try-catch around scoring, which could leave calls unresponded on error
- **Fix:** Wrapped scoring in try-catch, falling through to allow on any exception
- **Files modified:** CallScreeningService.kt
- **Verification:** Any exception allows the call through safely
- **Committed in:** bf3d7e9 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (1 blocking, 2 missing critical)
**Impact on plan:** All fixes necessary for correctness and compatibility. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required. User must grant ROLE_CALL_SCREENING permission on device via the Settings UI button.

## Next Phase Readiness
- Call screening infrastructure complete, ready for notification intelligence (11-02) and context awareness (11-03)
- Desktop phone_guard data will sync automatically once the foreground service is running
- Future enhancement: dedicated /spam/candidates API endpoint on desktop for more efficient sync

## Self-Check: PASSED

- [x] SpamEntity.kt exists
- [x] SpamDao.kt exists
- [x] SpamScorer.kt exists
- [x] SpamDatabase.kt exists
- [x] CallScreeningService.kt exists
- [x] Commit f5fd978 exists (Task 1)
- [x] Commit bf3d7e9 exists (Task 2)

---
*Phase: 11-intelligence-core*
*Completed: 2026-02-24*
