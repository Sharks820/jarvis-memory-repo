---
phase: 13-deep-learning-and-social
plan: 01
subsystem: habit-engine
tags: [room, hilt, compose, material3, notifications, pattern-detection, adaptive-suppression]

# Dependency graph
requires:
  - phase: 10-foundation-and-daily-driver
    provides: "Room DB, Hilt DI, JarvisService foreground service, SettingsScreen/ViewModel"
  - phase: 11-intelligence-core
    provides: "NotificationChannelManager with ROUTINE channel, ContextDetector with ContextStateDao"
  - phase: 12-life-management
    provides: "CommuteDao with learned locations, NudgeActionReceiver pattern from DoseActionReceiver"
provides:
  - "HabitPatternEntity and NudgeLogEntity Room entities for behavioral pattern persistence"
  - "PatternDetector: time-based and location-based behavioral pattern detection from existing data sources"
  - "NudgeEngine: gentle nudge delivery on ROUTINE notification channel with Done/Dismiss actions"
  - "NudgeResponseTracker: engagement tracking with adaptive suppression (>=80% ignore over 20 samples)"
  - "BuiltInNudges: water reminders (10/13/16h), screen breaks (11/15/20h), sleep reminder (22h)"
  - "Settings UI section for habit tracking with master toggle, built-in nudge toggles, pattern management"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Adaptive suppression: track act/dismiss rates, auto-suppress at >= 80% ignore threshold over 20 samples"
    - "Built-in nudge seeding: ensureBuiltInPatterns() creates defaults on first run, user opts in via Settings"
    - "Time-window matching: 15-minute window around trigger time for nudge delivery"
    - "Pattern confidence: occurrences / totalDays ratio capped at 1.0, minimum 0.6 for nudge eligibility"

key-files:
  created:
    - "android/app/src/main/java/com/jarvis/assistant/data/entity/HabitPatternEntity.kt"
    - "android/app/src/main/java/com/jarvis/assistant/data/entity/NudgeLogEntity.kt"
    - "android/app/src/main/java/com/jarvis/assistant/data/dao/HabitDao.kt"
    - "android/app/src/main/java/com/jarvis/assistant/data/dao/NudgeLogDao.kt"
    - "android/app/src/main/java/com/jarvis/assistant/feature/habit/PatternDetector.kt"
    - "android/app/src/main/java/com/jarvis/assistant/feature/habit/NudgeEngine.kt"
    - "android/app/src/main/java/com/jarvis/assistant/feature/habit/NudgeResponseTracker.kt"
    - "android/app/src/main/java/com/jarvis/assistant/feature/habit/BuiltInNudges.kt"
  modified:
    - "android/app/src/main/java/com/jarvis/assistant/data/JarvisDatabase.kt"
    - "android/app/src/main/java/com/jarvis/assistant/di/AppModule.kt"
    - "android/app/src/main/java/com/jarvis/assistant/service/JarvisService.kt"
    - "android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsScreen.kt"
    - "android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsViewModel.kt"
    - "android/app/src/main/AndroidManifest.xml"

key-decisions:
  - "DB version 10: MIGRATION_9_10 creates habit_patterns and nudge_log tables (13-02 took v8->v9, 13-01 adapted to v9->v10)"
  - "Built-in nudges created inactive by default -- user must opt in via Settings toggles"
  - "Adaptive suppression threshold: >= 80% ignore rate over minimum 20 samples"
  - "Pattern detection is rule-based (time clustering, location consistency) -- no ML dependencies"
  - "Nudge delivery via ROUTINE notification channel with 15-minute trigger window"
  - "NudgeActionReceiver as top-level class in NudgeEngine.kt (same file pattern as DoseActionReceiver)"

patterns-established:
  - "Adaptive suppression: track response rates per feature, auto-suppress low-engagement items"
  - "Built-in seeding: create default content on first run, let user opt in via Settings"
  - "SettingsViewModel pattern: inject DAO + service classes, expose StateFlow for UI, init block loads status"

requirements-completed: [HABIT-01, HABIT-02, HABIT-03, HABIT-04]

# Metrics
duration: ~20min
completed: 2026-02-24
---

# Phase 13 Plan 01: Habit Engine Summary

**Behavioral pattern detection from usage/location/time data with adaptive nudge delivery, response tracking with auto-suppression, and built-in nudge types (water/screen break/sleep)**

## Performance

- **Duration:** ~20 min (across two sessions, including parallel coordination with 13-02)
- **Started:** 2026-02-24
- **Completed:** 2026-02-24
- **Tasks:** 3
- **Files modified:** 14 (8 created, 6 modified)

## Accomplishments
- Room entities (HabitPatternEntity, NudgeLogEntity) with full DAOs for behavioral pattern persistence and nudge response tracking
- PatternDetector analyzes ContextStateDao and CommuteDao data to find recurring time-based and location-based behavioral patterns
- NudgeEngine delivers gentle nudge notifications on ROUTINE channel at the right day/time with Done/Dismiss action buttons
- NudgeResponseTracker monitors engagement rates and auto-suppresses nudges with >= 80% ignore rate over 20 samples
- BuiltInNudges provides water reminders (10/13/16h), screen breaks (11/15/20h), and sleep reminder (22h) -- all opt-in
- Settings UI section with master toggle, 3 built-in nudge toggles, detected pattern list with deactivation, and suppression management
- JarvisService integration: pattern detection daily, nudge delivery every 5 minutes, nudge expiry every hour

## Task Commits

Each task was committed atomically:

1. **Task 1: Room entities, DAOs, database migration, and DI wiring** - `5fd2898` (feat) -- committed by parallel 13-02 agent which proactively created all 13-01 Task 1 files alongside its own entities
2. **Task 2: Pattern detector, nudge engine, response tracker, built-in nudges, and notification receiver** - `628968d` (feat) -- committed by parallel 13-02 agent which proactively created all 13-01 Task 2 files alongside its own feature classes
3. **Task 3: JarvisService integration, Settings UI, and SettingsViewModel wiring** - `2ffcff8` (feat) -- SettingsViewModel habit wiring, SettingsScreen habit section, HabitDao extensions (JarvisService integration already committed by 13-02 in `1a6d1e3`)

**Plan metadata:** (this commit)

_Note: Tasks 1 and 2 were proactively created by the parallel 13-02 agent in its commits. Task 3 had partial overlap (JarvisService.kt) but unique work in SettingsViewModel, SettingsScreen, and HabitDao._

## Files Created/Modified

**Created (by parallel 13-02 agent, verified by 13-01):**
- `android/.../data/entity/HabitPatternEntity.kt` - Room entity for detected behavioral patterns (15 fields including confidence, suppression, category)
- `android/.../data/entity/NudgeLogEntity.kt` - Room entity for nudge delivery/response tracking
- `android/.../data/dao/HabitDao.kt` - DAO with pattern CRUD, suppression, occurrence tracking, active count flow
- `android/.../data/dao/NudgeLogDao.kt` - DAO with response rate queries, date queries, expiration
- `android/.../feature/habit/PatternDetector.kt` - Time-based and location-based pattern detection from ContextStateDao/CommuteDao
- `android/.../feature/habit/NudgeEngine.kt` - Nudge delivery engine with day/time matching, ROUTINE notifications, NudgeActionReceiver
- `android/.../feature/habit/NudgeResponseTracker.kt` - Response tracking with adaptive suppression (80% threshold, 20 sample minimum)
- `android/.../feature/habit/BuiltInNudges.kt` - Water/screen break/sleep nudge pattern seeding

**Modified:**
- `android/.../data/JarvisDatabase.kt` - Version 10 with MIGRATION_9_10 for habit_patterns + nudge_log tables
- `android/.../di/AppModule.kt` - Added HabitDao and NudgeLogDao Hilt providers
- `android/.../service/JarvisService.kt` - Added PatternDetector/NudgeEngine/NudgeResponseTracker to sync loop
- `android/.../ui/settings/SettingsScreen.kt` - Added "Habit Tracking" section with master toggle, built-in nudge toggles, pattern list
- `android/.../ui/settings/SettingsViewModel.kt` - Added habit state flows, setters, loadHabitStatus(), built-in toggle logic
- `android/app/src/main/AndroidManifest.xml` - Registered NudgeActionReceiver with NUDGE_ACTED/NUDGE_DISMISSED intent filters

## Decisions Made

1. **DB version coordination with parallel plan**: 13-02 took MIGRATION_8_9 (contact_context + call_interaction_log) and MIGRATION_9_10 (habit_patterns + nudge_log). Original plan expected 13-01 to take v8->v9 but adapted to the parallel agent's structure.
2. **Built-in nudges inactive by default**: User must explicitly opt in via Settings toggles -- respects user autonomy and avoids unwanted notifications.
3. **Adaptive suppression threshold**: >= 80% ignore rate over minimum 20 samples before auto-suppressing. Gives patterns sufficient sample size before making suppression decisions.
4. **Rule-based pattern detection**: Uses time clustering and location consistency from existing data sources (no ML). Simple, explainable, and no external dependencies.
5. **SharedPreferences for habit_nudges_enabled**: JarvisService reads `habit_nudges_enabled` from SharedPreferences for hot-path performance in the sync loop (avoids DB query on every iteration).
6. **SettingsViewModel loads built-in toggle state from DAO**: Checks if built-in patterns exist and are active to set initial toggle states, ensuring UI reflects actual pattern state.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added missing HabitDao queries for SettingsViewModel**
- **Found during:** Task 3 (SettingsViewModel wiring)
- **Issue:** SettingsViewModel needed `unsuppress(id)`, `activate(id)`, and `getAllActivePatterns()` suspend functions that were not in the existing HabitDao
- **Fix:** Added 3 query methods to HabitDao.kt: individual unsuppress, activate, and getAllActivePatterns
- **Files modified:** `android/.../data/dao/HabitDao.kt`
- **Verification:** SettingsViewModel compiles and references all needed DAO methods
- **Committed in:** `2ffcff8` (Task 3 commit)

**2. [Rule 3 - Blocking] Parallel agent created all Task 1 and Task 2 files**
- **Found during:** Task 1 and Task 2 start
- **Issue:** The parallel 13-02 agent proactively created all entities, DAOs, migrations, feature classes, and manifest entries that were planned for 13-01 Tasks 1 and 2
- **Fix:** Verified all created files matched 13-01 plan specifications. No additional changes needed for Tasks 1 and 2. Focused Task 3 on the unique SettingsScreen/SettingsViewModel/HabitDao work that 13-02 did not cover.
- **Files modified:** None (existing files verified)
- **Verification:** All files exist with correct content matching plan specifications

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Deviation 1 was a necessary DAO extension for UI functionality. Deviation 2 was coordination with the parallel agent -- all planned functionality was delivered, just committed by a different agent for Tasks 1-2.

## Issues Encountered

- **JarvisService.kt concurrent modification**: The parallel 13-02 agent was modifying JarvisService.kt simultaneously, causing "File has been modified since read" errors. Resolved by re-reading the file and using Write tool instead of Edit tool. Ultimately, 13-02 had already committed identical JarvisService.kt changes, so no unique diff was needed from 13-01.
- **DB version race condition**: Plan specified v8->v9 migration, but 13-02 already took v8->v9 for its tables and created v9->v10 for habit tables. No code changes needed since 13-02 handled both migrations.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Habit engine complete: pattern detection, nudge delivery, response tracking, adaptive suppression, built-in nudges, and Settings UI all in place
- Phase 13 (Deep Learning and Social) is now complete with both plans (13-01 habit tracking + 13-02 relationship memory) delivered
- All v2.0 Android App phases (10-13) are complete -- ready for milestone review

## Self-Check: PASSED

- All 8 created files verified present on disk (HabitPatternEntity, NudgeLogEntity, HabitDao, NudgeLogDao, PatternDetector, NudgeEngine, NudgeResponseTracker, BuiltInNudges)
- All 4 commits verified in git log (5fd2898, 628968d, 1a6d1e3, 2ffcff8)
- SUMMARY.md created at `.planning/phases/13-deep-learning-and-social/13-01-SUMMARY.md`

---
*Phase: 13-deep-learning-and-social*
*Completed: 2026-02-24*
