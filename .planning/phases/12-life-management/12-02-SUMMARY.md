---
phase: 12-life-management
plan: 02
subsystem: android-features
tags: [room, workmanager, bluetooth, gps, notifications, finance, commute, hilt, coroutines]

# Dependency graph
requires:
  - phase: 11-intelligence-core
    provides: "NotificationListenerService, JarvisService sync loop, ContextDetector, NotificationChannelManager, SettingsScreen/ViewModel"
  - phase: 12-life-management plan 01
    provides: "DB version 6 with MedicationEntity/MedicationLogEntity, MedicationScheduler, RefillTracker"
provides:
  - "TransactionEntity/TransactionDao for financial transaction storage and aggregation"
  - "CommuteLocationEntity/ParkingEntity/CommuteDao for learned locations and parking memory"
  - "BankNotificationParser for extracting transactions from bank SMS/email notifications"
  - "AnomalyDetector for unusual spending alerts (3x average, new merchants, subscription changes)"
  - "SpendSummaryWorker for weekly financial summary via WorkManager"
  - "LocationLearner for automatic home/work GPS pattern classification"
  - "TrafficChecker for pre-departure commute suggestions"
  - "ParkingMemory for Bluetooth-triggered parking GPS saves"
  - "Financial Watchdog and Commute Intelligence Settings UI sections"
affects: [13-final-polish]

# Tech tracking
tech-stack:
  added: [androidx.work:work-runtime-ktx:2.9.1, androidx.hilt:hilt-work:1.2.0]
  patterns: [HiltWorker with AssistedInject for WorkManager, BroadcastReceiver runtime registration in foreground service, haversine distance for GPS proximity, SHA-256 notification dedup, regex bank notification parsing]

key-files:
  created:
    - android/app/src/main/java/com/jarvis/assistant/data/entity/TransactionEntity.kt
    - android/app/src/main/java/com/jarvis/assistant/data/entity/CommuteLocationEntity.kt
    - android/app/src/main/java/com/jarvis/assistant/data/entity/ParkingEntity.kt
    - android/app/src/main/java/com/jarvis/assistant/data/dao/TransactionDao.kt
    - android/app/src/main/java/com/jarvis/assistant/data/dao/CommuteDao.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/finance/BankNotificationParser.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/finance/AnomalyDetector.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/finance/SpendSummaryWorker.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/commute/LocationLearner.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/commute/TrafficChecker.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/commute/ParkingMemory.kt
  modified:
    - android/app/src/main/java/com/jarvis/assistant/data/JarvisDatabase.kt
    - android/app/src/main/java/com/jarvis/assistant/di/AppModule.kt
    - android/app/src/main/java/com/jarvis/assistant/feature/scheduling/JarvisNotificationListenerService.kt
    - android/app/src/main/java/com/jarvis/assistant/service/JarvisService.kt
    - android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsScreen.kt
    - android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsViewModel.kt
    - android/app/src/main/AndroidManifest.xml
    - android/app/build.gradle.kts

key-decisions:
  - "DB version 7 with MIGRATION_6_7 (appended to 12-01's v6 with medications)"
  - "SHA-256 hash dedup on notification text prevents duplicate transaction records"
  - "Regex patterns for Chase, BoA, Wells Fargo with generic fallback for bank notification parsing"
  - "Anomaly thresholds: 3x category average (unusual), first-time merchant > $50, subscription delta > 10%"
  - "Haversine distance formula for GPS proximity matching (200m radius default)"
  - "Auto-classify locations after 5 visits: home (evening/night), work (weekday business hours)"
  - "Runtime BroadcastReceiver for BT disconnect (not manifest-registered) -- tied to service lifecycle"
  - "WorkManager PeriodicWorkRequest with 7-day period and initial delay to next Sunday 10 AM"
  - "TrafficChecker queries desktop brain as traffic proxy (avoids Google Maps API key dependency)"

patterns-established:
  - "HiltWorker pattern: @HiltWorker + @AssistedInject for WorkManager CoroutineWorker DI"
  - "Runtime BroadcastReceiver in foreground service: register in onCreate, unregister in onDestroy"
  - "Financial notification routing: bank apps routed through BankNotificationParser before scheduling extraction"
  - "GPS pattern learning: running averages for arrival/departure hours with confidence = visitCount/20"

requirements-completed: [FIN-01, FIN-02, FIN-03, COMM-01, COMM-02, COMM-03]

# Metrics
duration: ~15min
completed: 2026-02-24
---

# Phase 12 Plan 02: Financial Watchdog and Commute Intelligence Summary

**Bank notification parsing with anomaly detection (3x avg, new merchant, subscription changes), GPS pattern learning for home/work auto-classification, Bluetooth-triggered parking memory, and weekly spend summary via WorkManager**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-02-24
- **Completed:** 2026-02-24
- **Tasks:** 3
- **Files created:** 11
- **Files modified:** 8

## Accomplishments
- Bank SMS/email notifications parsed into TransactionEntity with regex patterns for Chase, Bank of America, Wells Fargo, and generic fallback
- Anomaly detection alerts for unusual amounts (3x category average), new merchants (>$50), and subscription price changes (>10% delta)
- Weekly spend summary worker runs every Sunday via WorkManager with top merchants and anomaly count
- GPS locations auto-learned and classified as home (evening visits) or work (weekday business hours) after 5+ visits
- Pre-departure traffic checker queries desktop brain with leave-time suggestions before typical commute
- Parking GPS saved automatically when car Bluetooth disconnects via runtime BroadcastReceiver
- Financial Watchdog and Commute Intelligence sections added to Settings UI with all feature toggles

## Task Commits

Each task was committed atomically:

1. **Task 1: Room entities, DAOs, database migration, and financial notification parser** - `ba439ed` (feat)
2. **Task 2: Commute intelligence, parking memory, and weekly spend worker** - `69c9707` (feat)
3. **Task 3: NotificationListener integration, JarvisService wiring, and Settings UI** - `c4bb3e9` (feat)

## Files Created/Modified

**Created:**
- `android/.../data/entity/TransactionEntity.kt` - Room entity for parsed bank transactions with SHA-256 hash dedup
- `android/.../data/entity/CommuteLocationEntity.kt` - Room entity for learned GPS locations (home/work/frequent)
- `android/.../data/entity/ParkingEntity.kt` - Room entity for Bluetooth-triggered parking GPS saves
- `android/.../data/dao/TransactionDao.kt` - DAO with aggregation queries (spend range, merchant stats, category averages)
- `android/.../data/dao/CommuteDao.kt` - DAO for location CRUD, parking save/deactivate, Flow accessors
- `android/.../feature/finance/BankNotificationParser.kt` - Parses bank SMS/email via regex, SHA-256 dedup, stores TransactionEntity
- `android/.../feature/finance/AnomalyDetector.kt` - Flags unusual amounts, new merchants, subscription price changes
- `android/.../feature/finance/SpendSummaryWorker.kt` - HiltWorker posting weekly spend summary via WorkManager
- `android/.../feature/commute/LocationLearner.kt` - GPS pattern learning with haversine distance and auto-classification
- `android/.../feature/commute/TrafficChecker.kt` - Pre-departure traffic check with desktop brain integration
- `android/.../feature/commute/ParkingMemory.kt` - BT disconnect receiver for automatic parking GPS save

**Modified:**
- `android/.../data/JarvisDatabase.kt` - Bumped v6->v7, added 3 entities, 2 DAOs, MIGRATION_6_7
- `android/.../di/AppModule.kt` - Added TransactionDao and CommuteDao Hilt providers
- `android/.../feature/scheduling/JarvisNotificationListenerService.kt` - Bank notification routing via EntryPoint
- `android/.../service/JarvisService.kt` - Location (15min), traffic (30min), parking BT, SpendSummaryWorker
- `android/.../ui/settings/SettingsScreen.kt` - Financial Watchdog and Commute Intelligence UI sections
- `android/.../ui/settings/SettingsViewModel.kt` - Financial/commute state flows, loaders, and setters
- `android/app/src/main/AndroidManifest.xml` - Location and Bluetooth permissions
- `android/app/build.gradle.kts` - WorkManager + Hilt Worker dependencies

## Decisions Made
- Coordinated with parallel plan 12-01: built on DB v6 (medications), bumped to v7 (transactions + commute + parking)
- SHA-256 notification hash for dedup prevents duplicate transactions from repeated notifications
- Regex bank parsing targets Chase, BoA, Wells Fargo specifically; generic $amount fallback for other banks
- Haversine distance with 200m default radius for GPS location matching
- Location auto-classification after 5 visits based on time-of-day patterns
- Runtime BT receiver (not manifest-registered) tied to JarvisService lifecycle for proper cleanup
- Desktop brain as traffic proxy avoids requiring Google Maps API key
- WorkManager 7-day period with Sunday 10 AM initial delay for weekly spend summary

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed TrafficChecker API type mismatch**
- **Found during:** Task 2 (TrafficChecker implementation)
- **Issue:** Initially used `apiClient.api().sendCommand(mapOf(...))` but sendCommand takes CommandRequest, not Map. Also referenced `response.response` which doesn't exist on CommandResponse (has `stdoutTail`).
- **Fix:** Imported CommandRequest, used `CommandRequest(text = commandText)`, and changed to `response.stdoutTail.joinToString(" ")`.
- **Files modified:** TrafficChecker.kt
- **Verification:** Code compiles with correct API types
- **Committed in:** `69c9707` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor API type correction. No scope creep.

## Issues Encountered
- Parallel plan coordination: 12-01 had already modified JarvisDatabase (v6), AppModule, JarvisService, SettingsScreen, and SettingsViewModel. All modifications were successfully appended to existing content without conflicts.

## User Setup Required
None - no external service configuration required. Location and Bluetooth permissions will be requested at runtime via standard Android permission flows.

## Next Phase Readiness
- Financial watchdog and commute intelligence fully integrated into existing notification listener, foreground service, and settings UI
- Phase 12 plan 03 (if exists) can build on DB v7 with full transaction and commute data
- Phase 13 (final polish) has complete feature set to work with

## Self-Check: PASSED

- All 11 created files verified present on disk
- Commit ba439ed (Task 1): FOUND
- Commit 69c9707 (Task 2): FOUND
- Commit c4bb3e9 (Task 3): FOUND

---
*Phase: 12-life-management*
*Completed: 2026-02-24*
