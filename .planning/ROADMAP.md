# Roadmap: Jarvis -- Limitless Personal AI Assistant

## Overview

<details>
<summary>v1.0 Desktop Engine (Phases 1-9) -- SHIPPED 2026-02-23</summary>

The desktop Python engine is complete: SQLite + FTS5 + sqlite-vec memory engine, knowledge graph with fact locks and contradiction detection, intelligence routing (Ollama + Anthropic), real calendar/email/task connectors with daily briefing, multi-source knowledge harvesting, British butler persona with voice, continuous learning with golden eval, changelog-based encrypted sync, proactive intelligence with wake word, cost tracking with adversarial self-testing, and HMAC-signed mobile API with owner guard. 18 plans executed across 9 phases, 473 passing tests.

</details>

## v2.0 Native Android App

The desktop brain is built. Now Jarvis needs a body -- a native Kotlin Android app on the Samsung Galaxy S25 Ultra that transforms the phone from a web-panel-only interface into a full-featured smart mobile companion. Phase 10 stands up the Android project, connects it to the desktop brain, and delivers a working voice assistant with a Material 3 dashboard. Phase 11 adds the intelligence layer: call screening, notification parsing for scheduling, contextual silence, and proactive notifications. Phase 12 delivers life management features: prescriptions, finance monitoring, document scanning, and commute intelligence. Phase 13 completes the learning loop: habit tracking with adaptive nudges, relationship memory with social context, and feedback loops that make every feature smarter over time.

## Phases

**Phase Numbering:**
- Phases 1-9: v1.0 Desktop Engine (all complete)
- Phases 10-13: v2.0 Native Android App (current milestone)
- Decimal phases (10.1, 10.2): Urgent insertions if needed

- [ ] **Phase 10: Foundation and Daily Driver** - Android project with Compose UI, desktop API client with HMAC signing, biometric security, encrypted Room DB, foreground sync service, dashboard UI (home/chat/memory/settings), and voice assistant
- [ ] **Phase 11: Intelligence Core** - Call screening with spam defense, notification-based scheduling extraction, proactive notification channels with smart batching, and contextual silence (meeting/driving/sleep detection)
- [ ] **Phase 12: Life Management** - Prescription tracking with alarm reminders, financial watchdog for bank notifications, document scanner with encrypted OCR, and commute intelligence with parking memory
- [ ] **Phase 13: Deep Learning and Social** - Habit detection with adaptive nudges, relationship memory with pre/post-call context, and learning feedback loops across all features

## Phase Details

### Phase 10: Foundation and Daily Driver
**Goal**: Jarvis lives on the phone as a working daily-use app -- the user can unlock with biometrics, see their day at a glance, have a voice conversation with the desktop brain, and all data is encrypted and synced
**Depends on**: v1.0 Desktop Engine (complete)
**Requirements**: FOUND-01, FOUND-02, FOUND-03, FOUND-04, FOUND-05, FOUND-06, ASEC-01, ASEC-02, ASEC-03, ASEC-04, DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06, AVOICE-01, AVOICE-02, AVOICE-03, AVOICE-04
**Success Criteria** (what must be TRUE):
  1. User opens the app on Samsung Galaxy S25 Ultra, authenticates with fingerprint or face, and sees a Material 3 home screen with today's schedule, weather, tasks, and quick actions -- all data pulled from the desktop engine over LAN
  2. User taps push-to-talk (from notification or in-app), speaks a command, and hears the desktop brain's response spoken back through the phone -- the full voice round-trip works end-to-end
  3. User can browse conversation history in the Chat tab, search memories in the Memory tab, and configure sync/notification/voice/security settings in the Settings tab
  4. When the phone loses WiFi connectivity, commands queue locally in the encrypted Room database and automatically flush to the desktop when connectivity returns -- no commands are lost
  5. All local data (Room DB, tokens, signing keys) is encrypted at rest via SQLCipher and EncryptedSharedPreferences, and sensitive operations (prescriptions, finance, documents) require master password confirmation
**Plans**: 3 plans

Plans:
- [ ] 10-01-PLAN.md -- Android project scaffold, Gradle build, Compose navigation, Material 3 dark theme, biometric lock, encrypted Room DB with SQLCipher
- [ ] 10-02-PLAN.md -- JarvisApiClient (Retrofit2 + OkHttp + HMAC interceptor), foreground service with sync loop, offline command queue, exponential backoff, owner guard device bootstrap
- [ ] 10-03-PLAN.md -- Dashboard UI (home/chat/memory/settings tabs), voice engine (STT + command dispatch + TTS response), bootstrap onboarding screen

### Phase 11: Intelligence Core
**Goal**: The phone actively works for the user -- screening spam calls before they ring, extracting calendar events from notifications, adjusting behavior based on context (driving/meeting/sleeping), and delivering desktop proactive alerts through prioritized notification channels
**Depends on**: Phase 10
**Requirements**: CALL-01, CALL-02, CALL-03, CALL-04, SCHED-01, SCHED-02, SCHED-03, SCHED-04, ANOTIF-01, ANOTIF-02, ANOTIF-03, ANOTIF-04, CTX-01, CTX-02, CTX-03, CTX-04
**Success Criteria** (what must be TRUE):
  1. When an unknown number calls, the CallScreeningService intercepts it before ringing, scores it against the local spam database synced from desktop, and applies the user-configured action (block/silence/voicemail/allow) based on score threshold
  2. When the user receives an SMS or email containing a date/time/location, the notification listener extracts the scheduling cues, creates a calendar event via CalendarProvider, and the desktop engine flags any conflicts with existing schedule
  3. Desktop proactive alerts (bill reminders, meeting prep, medication) appear on the phone as notifications routed to the correct channel (URGENT bypasses DND, IMPORTANT, ROUTINE, BACKGROUND) with related notifications batched and summarized
  4. The phone detects current context -- calendar meeting, accelerometer driving pattern, time-based sleep, gaming mode sync -- and automatically adjusts notification aggressiveness, call screening strictness, and voice volume (driving: urgent-only read aloud; meeting: full silence except emergency contacts)
  5. Notification learning tracks which notifications the user acts on versus dismisses and adjusts priority scoring over time
**Plans**: 3 plans

Plans:
- [ ] 11-01-PLAN.md -- CallScreeningService with local spam DB sync, scoring engine, configurable threshold actions
- [ ] 11-02-PLAN.md -- NotificationListenerService for scheduling cue extraction, CalendarProvider event creation, desktop conflict checking
- [ ] 11-03-PLAN.md -- Proactive notification channels (4 priority tiers), smart batching, notification learning, context detector (meeting/driving/sleep/gaming) with auto-adjustment rules

### Phase 12: Life Management
**Goal**: Jarvis manages the practical details of daily life -- reminding about medications on exact schedules that survive DND, watching bank transactions for anomalies, scanning and searching documents by content, and knowing commute patterns without manual setup
**Depends on**: Phase 10, Phase 11 (notification channels, context detection)
**Requirements**: RX-01, RX-02, RX-03, RX-04, FIN-01, FIN-02, FIN-03, DOC-01, DOC-02, DOC-03, DOC-04, COMM-01, COMM-02, COMM-03
**Success Criteria** (what must be TRUE):
  1. User sets up medication schedule, receives exact-time dose reminders that break through Do Not Disturb, can ask "did I take my morning meds?" by voice and get an accurate answer from today's log, and gets proactive refill reminders before running out
  2. Bank SMS and email notifications are parsed for charges, and the user receives alerts for unusual amounts, new merchants, and subscription price changes, plus a weekly spend summary as a ROUTINE notification
  3. User can scan a document with the camera, OCR extracts searchable text, the document is stored encrypted in Room DB and synced to desktop, and the user can search across all documents by content (e.g., "find my Best Buy receipt from January") with automatic categorization (receipts, warranties, IDs, medical, insurance)
  4. The app automatically learns home and work locations from GPS patterns, provides pre-departure traffic checks with leave-time suggestions, and saves parking GPS coordinates when car Bluetooth disconnects
**Plans**: TBD

Plans:
- [ ] 12-01-PLAN.md -- Prescription manager (Room DB schedule, AlarmManager EXACT_ALARM, voice query integration, refill tracking with proactive reminders)
- [ ] 12-02-PLAN.md -- Financial watchdog (bank SMS/email parsing, anomaly alerts, weekly summary) and commute intelligence (GPS pattern learning, traffic checks, Bluetooth parking memory)
- [ ] 12-03-PLAN.md -- Document scanner (CameraX + ML Kit OCR, encrypted Room storage, desktop sync, full-text search, auto-categorization)

### Phase 13: Deep Learning and Social
**Goal**: Jarvis becomes a learning companion -- detecting behavioral patterns and offering useful nudges, maintaining relationship context so the user is never caught off-guard in social situations, and continuously improving every feature through feedback loops
**Depends on**: Phase 10, Phase 11, Phase 12
**Requirements**: HABIT-01, HABIT-02, HABIT-03, HABIT-04, SOC-01, SOC-02, SOC-03
**Success Criteria** (what must be TRUE):
  1. The app detects behavioral patterns from phone usage, location, and time data, and delivers gentle nudges for detected routines (e.g., "You usually work out at 5pm on Tuesdays") including built-in types: water reminders, screen time awareness, and sleep schedule
  2. Nudge response rate is tracked -- nudges the user consistently ignores are automatically suppressed, and nudge timing/content adapts based on engagement patterns
  3. Before a phone call, the user sees a pre-call card showing last conversation date and key topics for that contact; after a call, the user is prompted to log conversation context for next time
  4. Proactive relationship alerts surface birthdays, anniversaries, and connections the user hasn't reached out to in a while, drawing from the desktop brain's social context graph
**Plans**: TBD

Plans:
- [ ] 13-01-PLAN.md -- Habit engine (pattern detection from usage/location/time, nudge delivery, response rate tracking, adaptive suppression, built-in nudge types)
- [ ] 13-02-PLAN.md -- Relationship memory (pre-call context cards, post-call logging prompts, proactive social alerts for birthdays/anniversaries/neglected connections)

## Progress

**Execution Order:**
Phases execute in numeric order: 10 -> 11 -> 12 -> 13

Note: Phase 12 depends on Phase 11 for notification channels and context detection. Phase 13 depends on all prior phases for data sources. Linear execution is the natural order.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1-9 | v1.0 Desktop Engine | 18/18 | Complete | 2026-02-23 |
| 10. Foundation and Daily Driver | v2.0 Android App | 0/3 | Planning complete | - |
| 11. Intelligence Core | v2.0 Android App | 0/3 | Planning complete | - |
| 12. Life Management | v2.0 Android App | 0/3 | Not started | - |
| 13. Deep Learning and Social | v2.0 Android App | 0/2 | Not started | - |
