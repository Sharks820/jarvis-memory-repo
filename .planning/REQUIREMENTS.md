# Requirements: Jarvis -- Limitless Personal AI Assistant

**Defined:** 2026-02-22
**Updated:** 2026-02-23 (v2.0 Android App milestone)
**Core Value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## v1 Requirements (Desktop Engine -- Complete)

All v1 requirements shipped in milestone v1.0. See `.planning/MILESTONES.md` for details.

### Memory System (MEM) -- Complete

- [x] **MEM-01**: All memory records stored in SQLite with FTS5 full-text search index
- [x] **MEM-02**: All memory records have embedding vectors stored via sqlite-vec for semantic search
- [x] **MEM-03**: Hybrid search (FTS5 keyword + embedding cosine + recency decay) returns relevant results for any natural language query
- [x] **MEM-04**: Memory records are classified into branches using semantic classification
- [x] **MEM-05**: Three-tier memory hierarchy (hot/warm/cold) with automatic promotion/demotion
- [x] **MEM-06**: Ingestion pipeline chunks long content, extracts entities, generates embeddings, and classifies branch
- [x] **MEM-07**: Content-hash deduplication (SHA-256) prevents duplicate records
- [x] **MEM-08**: Migration script imports all existing JSONL/JSON memory data into SQLite without data loss

### Anti-Regression & Knowledge (KNOW) -- Complete

- [x] **KNOW-01**: Facts extracted from ingested content stored in knowledge graph (NetworkX + SQLite)
- [x] **KNOW-02**: Locked facts cannot be overwritten by lower-confidence information
- [x] **KNOW-03**: Contradictions against locked facts quarantined for owner review
- [x] **KNOW-04**: Regression report compares knowledge counts and fact integrity between snapshots
- [x] **KNOW-05**: Cross-branch fact relationships enable cross-domain reasoning queries
- [x] **KNOW-06**: Temporal metadata distinguishes permanent knowledge from time-sensitive information

### Multi-Source Knowledge Harvesting (HARV) -- Complete

- [x] **HARV-01** through **HARV-07**: All harvesting requirements shipped (MiniMax, Kimi, Claude Code, Codex, Gemini, dedup, budget controls)

### Intelligence Routing (INTL) -- Complete

- [x] **INTL-01** through **INTL-05**: All routing requirements shipped (gateway, classifier, fallback, cost tracking, progressive cost reduction)

### Voice & Personality (VOICE) -- Complete

- [x] **VOICE-01** through **VOICE-05**: All voice requirements shipped (Edge-TTS, persona, tone adaptation, faster-whisper STT, wake word)

### Mobile-Desktop Sync (SYNC) -- Complete

- [x] **SYNC-01** through **SYNC-05**: All sync requirements shipped (changelog sync, incremental, conflict resolution, encryption, bidirectional learning)

### Connectors (CONN) -- Complete

- [x] **CONN-01** through **CONN-05**: All connector requirements shipped (calendar, email, tasks, briefing, proactive)

### Architecture (ARCH) -- Complete

- [x] **ARCH-01** through **ARCH-06**: All architecture requirements shipped (CQRS command bus, 70+ commands, 473 tests)

### Self-Improvement (GROW) -- Complete

- [x] **GROW-01** through **GROW-04**: All growth requirements shipped (learning engine, golden eval, adversarial self-test, cost reduction)

## v2 Requirements (Android App)

Requirements for the native Kotlin Android app. Each maps to roadmap phases 10-13.

### Foundation & Infrastructure (FOUND)

- [ ] **FOUND-01**: Android project with Kotlin, Jetpack Compose, Material 3, and Gradle build system compiles and runs on Samsung Galaxy S25 Ultra
- [ ] **FOUND-02**: JarvisApiClient using Retrofit2 + OkHttp connects to desktop engine with HMAC-SHA256 signing interceptor matching existing protocol
- [ ] **FOUND-03**: Always-on foreground service (JarvisService) with persistent notification runs sync loop at configurable interval
- [ ] **FOUND-04**: Room database with SQLCipher encryption stores all local data
- [ ] **FOUND-05**: Offline command queue in Room DB caches commands when desktop is unreachable and auto-flushes on reconnect
- [ ] **FOUND-06**: Exponential backoff and retry on all API calls to desktop engine

### Android Security (ASEC)

- [ ] **ASEC-01**: Biometric lock (fingerprint/face) via BiometricPrompt required to open app
- [ ] **ASEC-02**: EncryptedSharedPreferences stores tokens and signing keys
- [ ] **ASEC-03**: Owner guard device bootstrap registers phone as trusted device on desktop engine
- [ ] **ASEC-04**: Master password prompt required for sensitive operations (prescriptions, financial, document access)

### Dashboard UI (DASH)

- [ ] **DASH-01**: Home tab displays today's schedule, weather, pending tasks, and quick action buttons
- [ ] **DASH-02**: Chat tab provides scrollable conversation history with text and voice input
- [ ] **DASH-03**: Memory tab allows searching memories, viewing recent learnings, and checking brain status
- [ ] **DASH-04**: Settings tab configures sync frequency, notification preferences, voice preferences, and security
- [ ] **DASH-05**: Material You dynamic color theming matches device wallpaper
- [ ] **DASH-06**: Dark mode as default theme matching desktop widget aesthetic

### Android Voice (AVOICE)

- [ ] **AVOICE-01**: Push-to-talk activation from persistent notification or floating bubble
- [ ] **AVOICE-02**: On-device STT via Android SpeechRecognizer (offline-capable on S25)
- [ ] **AVOICE-03**: Transcribed text sent to desktop /command endpoint for processing and response
- [ ] **AVOICE-04**: Desktop response spoken via Android TextToSpeech or streamed Edge-TTS audio

### Call Screening (CALL)

- [x] **CALL-01**: CallScreeningService intercepts incoming calls before ringing
- [x] **CALL-02**: Local spam database synced from desktop phone_guard module
- [x] **CALL-03**: Spam scoring based on unknown number, call frequency, and short duration patterns
- [x] **CALL-04**: User-configurable actions per score threshold: block, silence, voicemail, or allow

### Intelligent Scheduling (SCHED)

- [x] **SCHED-01**: NotificationListenerService reads incoming notifications from SMS and email apps
- [x] **SCHED-02**: Scheduling cue extraction (dates, times, locations, people) via regex + desktop Ollama
- [x] **SCHED-03**: Automatic calendar event creation via CalendarProvider from extracted cues
- [x] **SCHED-04**: Desktop proactive engine cross-references new events with existing schedule for conflicts

### Prescription Management (RX)

- [x] **RX-01**: Medication schedule stored in Room DB and synced to desktop brain
- [x] **RX-02**: AlarmManager with EXACT_ALARM for dose reminders that survive Do Not Disturb
- [x] **RX-03**: Voice query "did I take my morning meds?" checks today's medication log
- [x] **RX-04**: Refill tracking with proactive pharmacy reminder notifications

### Notifications & Proactive Intelligence (ANOTIF)

- [x] **ANOTIF-01**: Desktop proactive alerts received via sync polling (phone checks every 30 seconds)
- [x] **ANOTIF-02**: Four notification channels: URGENT (bypasses DND), IMPORTANT, ROUTINE, BACKGROUND
- [x] **ANOTIF-03**: Smart notification batching groups related notifications and provides summary
- [x] **ANOTIF-04**: Notification learning tracks user act-vs-dismiss patterns to adjust priority over time

### Contextual Silence (CTX)

- [x] **CTX-01**: Context detection from calendar (meeting), accelerometer (driving), time (sleeping), and gaming mode sync
- [x] **CTX-02**: Auto-adjustment of notification aggressiveness, call screening strictness, and voice volume by detected context
- [x] **CTX-03**: Driving mode restricts to urgent-only notifications read aloud, all others queued
- [x] **CTX-04**: Meeting mode enables full silence except for emergency contacts

### Relationship Memory (SOC)

- [ ] **SOC-01**: Pre-call context display showing last conversation date and key topics per contact
- [ ] **SOC-02**: Post-call logging prompt to capture conversation context for next time
- [ ] **SOC-03**: Proactive relationship alerts surfacing birthdays, anniversaries, and neglected connections

### Financial Watchdog (FIN)

- [x] **FIN-01**: Bank notification parsing from SMS and email for charge detection
- [x] **FIN-02**: Alerts on unusual amounts, new merchants, and subscription price changes
- [x] **FIN-03**: Weekly spend summary pushed as ROUTINE notification

### Habit Tracker (HABIT)

- [ ] **HABIT-01**: Pattern detection from phone usage, location, and time data
- [ ] **HABIT-02**: Gentle nudges for detected routines ("You usually work out at 5pm on Tuesdays")
- [ ] **HABIT-03**: Nudge response rate tracking that stops sending consistently-ignored nudges
- [ ] **HABIT-04**: Built-in nudge types: water reminders, screen time awareness, sleep schedule

### Commute Intelligence (COMM)

- [x] **COMM-01**: Automatic home/work location learning from GPS patterns (no manual setup)
- [x] **COMM-02**: Pre-departure traffic check with leave-time and alternate route suggestions
- [x] **COMM-03**: Parking memory saves GPS coordinates when car Bluetooth disconnects

### Document Scanner (DOC)

- [x] **DOC-01**: CameraX-based document scanning with ML Kit OCR text extraction
- [x] **DOC-02**: Encrypted storage in Room DB with sync to desktop memory brain
- [x] **DOC-03**: Full-text search across scanned documents ("find my Best Buy receipt from January")
- [x] **DOC-04**: Document categorization: receipts, warranties, IDs, medical, insurance, other

## v3 Requirements (Future)

Deferred beyond v2.0. Tracked but not in current roadmap.

### Advanced Intelligence

- **ADV-01**: Owner behavioral model that learns patterns, preferences, and routines from months of interaction data
- **ADV-02**: Emotional context awareness detecting owner's state from interaction patterns
- **ADV-03**: Full duplex real-time voice conversation (interrupt handling, multi-turn voice dialogue)

### Extended Platform

- **PLAT-01**: Wear OS companion app for Galaxy Watch
- **PLAT-02**: Smart home integration via MCP protocol to Home Assistant
- **PLAT-03**: Material You home screen widgets for quick actions and status

## Out of Scope

| Feature | Reason |
|---------|--------|
| Cloud-hosted deployment | Local-first is the core value proposition. Privacy is non-negotiable. |
| Custom LLM training/fine-tuning | RAG + prompting + memory achieves 90% of the benefit at 1% of the cost |
| iOS app | Samsung Galaxy S25 Ultra only. Single device. |
| Multi-user support | Single-owner assistant by design |
| LangChain / LiteLLM integration | Massive overhead for a 2-provider routing problem. Direct SDKs preferred. |
| Cross-platform framework (Flutter/React Native) | Need full access to Android platform APIs (CallScreening, NotificationListener) |
| Cloud push notifications (FCM) | LAN polling from foreground service is sufficient for single-user local-first |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

### v1 Requirements (Phases 1-9) -- All Complete

| Requirement | Phase | Status |
|-------------|-------|--------|
| ARCH-01 through ARCH-06 | Phase 1 | Complete |
| MEM-01 through MEM-08 | Phase 1 | Complete |
| KNOW-01 through KNOW-04 | Phase 2 | Complete |
| INTL-01 through INTL-04 | Phase 3 | Complete |
| CONN-01 through CONN-04 | Phase 4 | Complete |
| HARV-01 through HARV-07 | Phase 5 | Complete |
| VOICE-01 through VOICE-04 | Phase 6 | Complete |
| KNOW-05, KNOW-06, GROW-01, GROW-02 | Phase 7 | Complete |
| SYNC-01 through SYNC-05 | Phase 8 | Complete |
| CONN-05, VOICE-05, INTL-05, GROW-03, GROW-04 | Phase 9 | Complete |

### v2 Requirements (Phases 10-13) -- Pending

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 through FOUND-06 (6) | Phase 10 | Pending |
| ASEC-01 through ASEC-04 (4) | Phase 10 | Pending |
| DASH-01 through DASH-06 (6) | Phase 10 | Pending |
| AVOICE-01 through AVOICE-04 (4) | Phase 10 | Pending |
| CALL-01 through CALL-04 (4) | Phase 11 | Pending |
| SCHED-01 through SCHED-04 (4) | Phase 11 | Pending |
| ANOTIF-01 through ANOTIF-04 (4) | Phase 11 | Pending |
| CTX-01 through CTX-04 (4) | Phase 11 | Pending |
| RX-01 through RX-04 (4) | Phase 12 | Complete |
| FIN-01 through FIN-03 (3) | Phase 12 | Complete |
| DOC-01 through DOC-04 (4) | Phase 12 | Complete |
| COMM-01 through COMM-03 (3) | Phase 12 | Complete |
| HABIT-01 through HABIT-04 (4) | Phase 13 | Pending |
| SOC-01 through SOC-03 (3) | Phase 13 | Pending |

**Coverage:**
- v1 requirements: 49 total (all complete)
- v2 requirements: 57 total
- Phase 10: 20 requirements (FOUND + ASEC + DASH + AVOICE)
- Phase 11: 16 requirements (CALL + SCHED + ANOTIF + CTX)
- Phase 12: 14 requirements (RX + FIN + DOC + COMM)
- Phase 13: 7 requirements (HABIT + SOC)
- Mapped to phases: 57
- Unmapped: 0

---
*Requirements defined: 2026-02-22*
*Last updated: 2026-02-23 after v2.0 roadmap creation (corrected count from 53 to 57)*
