# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-23)

**Core value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.
**Current focus:** Post-milestone hardening -- security, visibility, process control, intelligence growth

## Current Position

Phase: Post-milestone hardening (security + visibility + process control + test coverage)
Status: Active -- 765 tests passing, 1 skipped, 0 failures
Last activity: 2026-02-25 -- Major quality hardening: 156 new tests, STT confidence fix, _summarize off-by-one fix, Android critical bug fixes, engine resource leak fixes, gateway audit rotation

Progress (v2.0): [██████████] 100% (11/11 plans)

## Performance Metrics

**v1.0 Desktop Engine (Complete):**
- Total plans completed: 18
- Average duration: ~8min
- Total execution time: ~3 hours
- Final test count: 475

**v2.0 Android App:**
- Total plans completed: 11
- Phases: 4 (phases 10-13), 11 plans total
- Phase 10: 3/3 plans complete
- Phase 11: 3/3 plans complete (11-01 call screening: ~8min, 11-02 scheduling: ~12min, 11-03 notifications+context: ~10min)
- Phase 12: 3/3 plans complete (12-01 prescription management: ~7min, 12-02 finance+commute: ~15min, 12-03 document scanner: ~12min)
- Phase 13: 2/2 plans complete (13-01 habit tracking: ~20min, 13-02 relationship memory: ~15min)

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.

**v1.0 key decisions (carried forward):**
- HMAC-SHA256 request signing with nonce replay protection (mobile API port 8787)
- Fernet encryption with PBKDF2HMAC for sync payloads
- Owner guard with device trust (galaxy_s25_primary already registered)

**v2.0 decisions (new):**
- Native Kotlin (not cross-platform) for full Android platform API access
- Phone is sensor/interface layer, desktop is brain
- Offline-first with Room DB command queue
- Jetpack Compose + Material 3 for UI
- Room + SQLCipher for encrypted local storage
- Retrofit2 + OkHttp with HMAC interceptor for networking
- SQLCipher passphrase derived from signing key (EncryptedSharedPreferences)
- TextToSpeech Locale.UK for British butler persona consistency
- Foreground service with configurable sync interval (default 30s)
- Command response polling (500ms intervals, 30s timeout) for voice round-trip
- Spam DB sync via /command endpoint (not dedicated /spam/candidates endpoint) for desktop compatibility
- Call screening thresholds in SharedPreferences for hot-path performance
- Spam DB sync throttled to 10-minute intervals within 30s sync loop
- EntryPoint + EntryPointAccessors for Hilt injection in NotificationListenerService
- SHA-256 content hash dedup for extracted scheduling events
- Confidence scoring thresholds: 0.3 (date), 0.5 (date+time), 0.7 (+location), 0.9 (all cues)
- DB version 3: ConversationEntity + CommandQueueEntity + SpamEntity + ExtractedEventEntity
- DB version 5: + NotificationLogEntity + ContextStateEntity (fallbackToDestructiveMigration)
- DB version 6: + MedicationEntity + MedicationLogEntity (explicit MIGRATION_5_6)
- AlarmManager.setExactAndAllowWhileIdle for Doze-safe medication reminders
- Separate DoseActionReceiver for notification Taken/Skip button handling
- JSON-serialized scheduledTimes in MedicationEntity for simplicity
- SharedPreferences date-key throttling for once-per-day refill reminders
- Accelerometer-based driving detection (avoids Google Play Services dependency)
- Context detection every 2 minutes in foreground service sync loop
- 4-tier notification channels: URGENT (bypasses DND), IMPORTANT, ROUTINE, BACKGROUND
- Priority learning via 80% act/dismiss threshold over 30-day rolling window
- DB version 7: + TransactionEntity + CommuteLocationEntity + ParkingEntity (explicit MIGRATION_6_7)
- SHA-256 notification hash dedup for financial transaction records
- Regex bank notification parsing: Chase, BoA, Wells Fargo patterns with generic fallback
- Anomaly thresholds: 3x category avg (unusual), first merchant >$50 (new), subscription >10% delta
- Haversine distance with 200m radius for GPS location proximity matching
- Auto-classify locations after 5 visits: home (evening/night), work (weekday business hours)
- Runtime BroadcastReceiver for BT disconnect tied to JarvisService lifecycle
- WorkManager PeriodicWorkRequest (7-day) for weekly spend summary on Sunday 10 AM
- Desktop brain as traffic proxy (avoids Google Maps API key dependency)
- DB version 8: + ScannedDocumentEntity (explicit MIGRATION_7_8)
- SQL LIKE search on ocrText instead of FTS4 (SQLCipher FTS4 compatibility uncertain, LIKE sufficient at document scale)
- Images stored as files in filesDir/documents/ (not Room BLOB) for large binary efficiency
- OCR text truncated to 5000 chars for desktop sync (/command endpoint practical limits)
- Category priority: id > medical > insurance > warranty > receipt > other (critical categories first)
- CameraX ImageCapture shared via mutableStateOf between AndroidView and Compose FAB
- CameraX 1.4.1 + ML Kit text-recognition 16.0.1 for on-device OCR
- DB version 10: + ContactContextEntity + CallLogEntity (MIGRATION_8_9) + HabitPatternEntity + NudgeLogEntity (MIGRATION_9_10)
- Phone number normalization: strip non-digits, take last 10 for US matching
- RemoteInput inline reply for post-call note capture (avoids complex activity-from-notification)
- Importance score: callFrequency * 0.4 + recency * 0.6 (range 0.0-1.0)
- Max 2 neglected contact alerts per day (SharedPreferences date-key dedup)
- EntryPointAccessors for CallStateReceiver + PostCallLogReceiver DI (BroadcastReceiver pattern)
- Built-in nudges created inactive by default (user opts in via Settings toggles)
- Adaptive suppression: >= 80% ignore rate over 20 samples auto-suppresses nudge pattern
- Rule-based pattern detection (time clustering, location consistency) -- no ML dependencies
- SharedPreferences habit_nudges_enabled for hot-path check in JarvisService sync loop
- NudgeActionReceiver top-level class in NudgeEngine.kt (same pattern as DoseActionReceiver)

### Pending Todos

None yet.

### Blockers/Concerns

- Desktop API endpoint coverage: voice commands use keyword matching (not NLP). Android app will need to send exact command phrases or desktop needs fuzzy matching upgrade.
- Sync protocol: /sync/pull and /sync/push exist but haven't been load-tested with real mobile traffic.
- CallScreeningService requires ROLE_CALL_SCREENING -- permission request button added in Settings UI (11-01).
- NotificationListenerService requires explicit user grant in Android Settings -- "Enable Notification Access" button added in Settings UI (11-02).

## Session Continuity

Last session: 2026-02-25
Stopped at: Quality hardening in progress. 765 tests passing. Continuing comprehensive bug fix and test coverage expansion.
Resume file: None
