# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-23)

**Core value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.
**Current focus:** v2.0 Native Android App -- Phase 11: Intelligence Core

## Current Position

Phase: 11 of 13 (Intelligence Core) -- COMPLETE
Plan: 3 of 3 in current phase (COMPLETE)
Status: Phase 11 complete - all intelligence core features implemented
Last activity: 2026-02-24 -- Completed 11-03 proactive notifications + context detection

Progress (v2.0): [██████░░░░] 55% (6/11 plans)

## Performance Metrics

**v1.0 Desktop Engine (Complete):**
- Total plans completed: 18
- Average duration: ~8min
- Total execution time: ~3 hours
- Final test count: 475

**v2.0 Android App:**
- Total plans completed: 6
- Phases: 4 (phases 10-13), 11 plans total
- Phase 10: 3/3 plans complete
- Phase 11: 3/3 plans complete (11-01 call screening: ~8min, 11-02 scheduling: ~12min, 11-03 notifications+context: ~10min)

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
- Accelerometer-based driving detection (avoids Google Play Services dependency)
- Context detection every 2 minutes in foreground service sync loop
- 4-tier notification channels: URGENT (bypasses DND), IMPORTANT, ROUTINE, BACKGROUND
- Priority learning via 80% act/dismiss threshold over 30-day rolling window

### Pending Todos

None yet.

### Blockers/Concerns

- Desktop API endpoint coverage: voice commands use keyword matching (not NLP). Android app will need to send exact command phrases or desktop needs fuzzy matching upgrade.
- Sync protocol: /sync/pull and /sync/push exist but haven't been load-tested with real mobile traffic.
- CallScreeningService requires ROLE_CALL_SCREENING -- permission request button added in Settings UI (11-01).
- NotificationListenerService requires explicit user grant in Android Settings -- "Enable Notification Access" button added in Settings UI (11-02).

## Session Continuity

Last session: 2026-02-24
Stopped at: Completed 11-03-PLAN.md (proactive notifications + context detection). Phase 11 complete. Ready for Phase 12.
Resume file: None
