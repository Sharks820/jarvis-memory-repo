# Jarvis Android App - Design Document

**Date:** 2026-02-23
**Platform:** Samsung Galaxy S25 (Android 15+, Kotlin)
**Architecture:** Native Kotlin app as smart interface, desktop Python engine as brain

## Guiding Principles

1. **Learning and reasoning are the primary focus** - every feature feeds back into the intelligence engine
2. **Powerful as possible, cheap as possible** - use on-device ML where feasible, Ollama for local inference, cloud LLMs only when necessary
3. **The phone is the sensor/interface layer** - voice, calls, location, notifications, camera
4. **The desktop is the brain** - memory store, intelligence routing, proactive engine, heavy computation
5. **Bidirectional sync** - phone queues commands offline, flushes on WiFi; desktop pushes proactive alerts

## Architecture

```
Samsung Galaxy S25 (Kotlin)
+----------------------------------------------+
| JarvisService (Foreground Service)           |
|   Always-on, persistent notification         |
|   Syncs with desktop every N seconds         |
|   Queues commands when offline               |
+------+---------------------------------------+
       |
       +-- CallScreener (CallScreeningService)
       +-- VoiceEngine (SpeechRecognizer + TTS)
       +-- NotificationMgr (channels + priorities)
       +-- ScheduleSync (calendar + rx + tasks)
       +-- ContextDetector (driving/meeting/sleep)
       +-- DocumentScanner (CameraX + ML Kit OCR)
       +-- HabitEngine (pattern detection + nudges)
       |
+------+---------------------------------------+
| JarvisApiClient (Retrofit2 + OkHttp)        |
|   HMAC request signing (OkHttp interceptor)  |
|   Offline command queue (Room DB)            |
|   Exponential backoff + retry                |
+------+---------------------------------------+
       | WiFi / LAN
       v
Desktop PC (Python Engine)
+----------------------------------------------+
| Memory brain (SQLite + FTS5 + embeddings)    |
| Intelligence routing (Claude/Ollama/Gemini)  |
| Proactive engine (heartbeat loop)            |
| Learning + growth tracking                   |
| Knowledge graph + contradiction detection    |
+----------------------------------------------+
```

## Intelligence Strategy (Cheap + Powerful)

### On-Device (Free)
- **Android SpeechRecognizer** - STT, no API cost
- **Android TextToSpeech** - TTS, no API cost
- **ML Kit** - OCR for document scanning, no API cost
- **Pattern detection** - local Room DB queries for habits, commute patterns
- **Call screening** - local spam database lookup, no API cost

### Desktop Local (Free after hardware)
- **Ollama** (Llama 3.1 / Mistral) - intent classification, simple reasoning, email parsing
- **Sentence-transformers** - embeddings for memory search
- **SQLite FTS5** - fast text search across all memories

### Cloud LLM (Only When Needed)
- **Claude/GPT** - complex reasoning, nuanced scheduling decisions, relationship context
- **Cost control** - desktop engine already has cost tracking + budget limits
- Route simple queries to Ollama, complex ones to cloud = ~90% cost reduction

## Feature Modules

### 1. Voice Assistant
- Wake word detection or push-to-talk from notification / floating bubble
- Android native SpeechRecognizer (offline-capable on S25)
- Send text to desktop `/command` endpoint
- Desktop routes to intelligence, returns response
- Response spoken via Android TTS or Edge-TTS audio stream
- **Learning:** tracks which intents you use most, pre-loads context for frequent commands

### 2. Call Screening (Spam Defense)
- `CallScreeningService` intercepts calls before ringing
- Local spam database synced from desktop phone_guard module
- Scoring: unknown number + high call frequency + short duration pattern = spam
- Actions: block, silence, send to voicemail, allow
- **Learning:** "you always reject 800 numbers after 9pm" -> auto-block rule created

### 3. Intelligent Scheduling
- `NotificationListenerService` reads incoming notifications (SMS, email apps)
- Regex + Ollama extract scheduling cues: dates, times, locations, people
- Creates calendar events via CalendarProvider
- Desktop proactive engine cross-references with existing schedule for conflicts
- **Learning:** learns your appointment patterns, preferred times, travel buffers

### 4. Prescription Management
- Medication schedule stored in Room DB + synced to desktop
- AlarmManager with EXACT_ALARM for dose reminders (survives DND)
- Voice: "Jarvis, did I take my morning meds?" -> checks today's log
- Refill tracking: "Refill in 3 days, want me to set a pharmacy reminder?"
- **Learning:** detects if you're consistently late on doses, adjusts reminder timing

### 5. Notifications & Proactive Intelligence
- Desktop pushes proactive alerts via sync polling (phone checks every 30s)
- 4 notification channels: URGENT (bypasses DND), IMPORTANT, ROUTINE, BACKGROUND
- Smart batching: groups related notifications, summarizes
- "3 emails from work, 1 needs action: budget approval by Friday"
- **Learning:** tracks which notifications you act on vs dismiss, adjusts priority

### 6. Dashboard UI (Material Design 3)
- **Home tab:** today's schedule, weather, tasks, quick actions
- **Chat tab:** scrollable conversation with Jarvis (send text or voice)
- **Memory tab:** search your memories, recent learnings, brain status
- **Settings tab:** sync frequency, notification prefs, voice prefs, security
- Dynamic color theming (matches wallpaper via Material You)
- Dark mode by default (matches desktop widget aesthetic)

### 7. Contextual Silence
- Detects context from: calendar (meeting), accelerometer (driving), time (sleeping), gaming mode
- Auto-adjusts: notification aggressiveness, call screening strictness, voice response volume
- Driving: only urgent notifications read aloud, all others queued
- Meeting: full silence except emergency contacts
- **Learning:** builds your daily context profile over weeks

### 8. Relationship Memory
- Desktop brain stores social context graph from conversations
- Before calls: "Last spoke to Mom 2 weeks ago. She mentioned knee surgery."
- After calls: "How was the call with Mom?" -> logs context for next time
- Surfaces in proactive alerts: "Dad's birthday is in 5 days"
- **Learning:** builds relationship strength scores, nudges for neglected connections

### 9. Financial Watchdog
- Reads bank notification emails/SMS for charges
- Alerts on: unusual amounts, new merchants, subscription price changes
- Weekly spend summary pushed as ROUTINE notification
- "Netflix went up $3/month since last bill"
- **Learning:** builds your normal spending profile, anomaly detection improves

### 10. Habit Tracker & Nudges
- Detects patterns from phone usage, location, time
- Gentle nudges: "You usually work out at 5pm on Tuesdays"
- Tracks nudge response rate - stops sending ones you consistently ignore
- Water reminders, screen time awareness, sleep schedule nudges
- **Learning:** core learning loop - every dismissed nudge teaches Jarvis about you

### 11. Commute Intelligence
- Learns home/work locations from patterns (no manual setup)
- Before departure: checks traffic, suggests leave time or alternate route
- Parking memory: saves GPS when Bluetooth disconnects from car
- "Your car is in section B3, 0.2 miles northeast"
- **Learning:** builds commute time models per day-of-week and time-of-day

### 12. Document Memory
- CameraX for document scanning, ML Kit OCR for text extraction
- Stored encrypted in Room DB + synced to desktop memory brain
- Searchable: "find my Best Buy receipt from January"
- Smart reminders: "Your laptop warranty expires in 8 months"
- Categories: receipts, warranties, IDs, medical, insurance, other
- **Learning:** improves OCR accuracy with corrections, learns document patterns

## Security

### Authentication
- **Biometric lock** - fingerprint/face required to open app (BiometricPrompt API)
- **Master password** - required for sensitive ops (prescriptions, financial, document access)
- **Owner guard sync** - same trust model as desktop, device registered via bootstrap

### Data Protection
- **EncryptedSharedPreferences** for tokens and signing keys
- **Room DB with SQLCipher** for all local data (documents, medications, habits)
- **HMAC signing** on all API requests to desktop (existing protocol)
- **Certificate pinning** if HTTPS is added later

### Privacy
- All data stays on-device + your desktop. Nothing goes to cloud except LLM queries.
- LLM queries are anonymized (no PII sent to Claude/GPT unless necessary)
- Document images stored encrypted, never uploaded

## Tech Stack

| Component | Technology | Cost |
|-----------|-----------|------|
| Language | Kotlin | Free |
| UI | Jetpack Compose + Material 3 | Free |
| Networking | Retrofit2 + OkHttp | Free |
| Local DB | Room + SQLCipher | Free |
| Voice STT | Android SpeechRecognizer | Free |
| Voice TTS | Android TextToSpeech | Free |
| OCR | ML Kit Text Recognition | Free |
| Call Screening | CallScreeningService API | Free |
| Notifications | NotificationListenerService | Free |
| Background | Foreground Service + WorkManager | Free |
| Build | Android Studio + Gradle | Free |

**Total infrastructure cost: $0/month** (all on-device + your existing desktop)

## Project Structure

```
android/
  app/
    src/main/
      java/com/jarvis/assistant/
        JarvisApp.kt                    # Application class
        MainActivity.kt                 # Single activity (Compose)
        service/
          JarvisService.kt              # Foreground service
          CallScreenerService.kt        # CallScreeningService impl
          NotificationListener.kt       # NotificationListenerService
        api/
          JarvisApiClient.kt            # Retrofit + HMAC interceptor
          HmacInterceptor.kt            # OkHttp HMAC signing
          models/                        # API request/response models
        data/
          JarvisDatabase.kt             # Room database
          dao/                           # Data access objects
          entity/                        # Database entities
          repository/                    # Repository pattern
        ui/
          theme/                         # Material 3 theme
          home/                          # Home/dashboard screen
          chat/                          # Chat/conversation screen
          memory/                        # Memory browser screen
          settings/                      # Settings screen
        feature/
          voice/VoiceEngine.kt          # STT + TTS management
          scheduling/ScheduleSync.kt    # Calendar integration
          prescription/RxManager.kt     # Medication management
          habits/HabitEngine.kt         # Pattern detection + nudges
          commute/CommuteTracker.kt     # Traffic + parking
          documents/DocumentScanner.kt  # OCR + encrypted storage
          finance/FinanceWatchdog.kt    # Spend monitoring
          context/ContextDetector.kt    # Meeting/driving/sleep detection
          social/RelationshipMemory.kt  # Social context graph
        security/
          BiometricHelper.kt            # Fingerprint/face auth
          CryptoHelper.kt              # Encryption utilities
          OwnerGuard.kt                # Desktop trust sync
```

## Implementation Phases

### Phase 1: Foundation (must work first)
- Android Studio project setup with Compose + Material 3
- JarvisApiClient with HMAC signing (connects to existing desktop API)
- Foreground service with sync loop
- Biometric lock + encrypted storage
- Basic dashboard UI (home + chat tabs)
- Voice command (STT -> desktop -> TTS response)

### Phase 2: Intelligence Core
- Call screening service (spam blocking)
- Notification listener (scheduling cue extraction)
- Context detector (meeting/driving/sleep)
- Offline command queue with Room DB

### Phase 3: Life Management
- Prescription management + alarm reminders
- Financial watchdog (bank SMS/email parsing)
- Document scanner + encrypted OCR storage
- Commute intelligence (traffic + parking)

### Phase 4: Deep Learning
- Habit tracker + adaptive nudges
- Relationship memory + social graph
- Contextual silence (auto-adjust behavior)
- Learning feedback loops across all features
