---
phase: 10-foundation-and-daily-driver
plan: 03
status: complete
---

# Plan 10-03 Summary: Dashboard screens, voice engine, bootstrap

## What was built
- **4 dashboard tab screens** with ViewModels (Home, Chat, Memory, Settings)
- **Bootstrap/onboarding screen** for first-run desktop connection setup
- **VoiceEngine** — full STT → command dispatch → TTS response round-trip
- **VoiceState** — 6-state sealed class (Idle, Listening, Transcribing, Processing, Speaking, Error)
- **Data layer**: Room DAOs, JarvisDatabase with SQLCipher encryption, CommandQueueProcessor
- **JarvisService** — foreground sync service with configurable interval
- **DI module** (AppModule) — Hilt bindings for database, DAOs, CryptoHelper
- **Updated NavGraph** — conditional start (bootstrap vs home), real screens wired in
- **Updated MainActivity** — VoiceEngine injection, service startup, voice intent handling

## Files created (19 new, 2 updated)
- `data/dao/ConversationDao.kt` — Room DAO for chat history
- `data/dao/CommandQueueDao.kt` — Room DAO for offline command queue
- `data/JarvisDatabase.kt` — Room database with SQLCipher SupportFactory
- `data/CommandQueueProcessor.kt` — Queue/flush/retry logic for commands
- `di/AppModule.kt` — Hilt DI module for singletons
- `service/JarvisService.kt` — Foreground service with sync loop + notification
- `feature/voice/VoiceState.kt` — Sealed class with 6 voice pipeline states
- `feature/voice/VoiceEngine.kt` — SpeechRecognizer + TextToSpeech (Locale.UK)
- `ui/home/HomeScreen.kt` — Dashboard with greeting, scores, rankings, ETAs, quick actions
- `ui/home/HomeViewModel.kt` — Fetches from /dashboard endpoint
- `ui/chat/ChatScreen.kt` — Chat bubbles (user right, assistant left) + input bar + mic
- `ui/chat/ChatViewModel.kt` — Sends via CommandQueueProcessor, observes conversation Flow
- `ui/memory/MemoryScreen.kt` — Search bar + results list
- `ui/memory/MemoryViewModel.kt` — Sends search query via /command endpoint
- `ui/settings/SettingsScreen.kt` — Connection, Sync, Security, About sections
- `ui/settings/SettingsViewModel.kt` — URL/sync/connection management
- `ui/onboarding/BootstrapScreen.kt` — First-run setup UI
- `ui/onboarding/BootstrapViewModel.kt` — Test connection + bootstrap authentication
- `ui/navigation/JarvisNavGraph.kt` — Updated: real screens, bootstrap route, voice passthrough
- `MainActivity.kt` — Updated: VoiceEngine injection, service startup, voice intent handling

## Key design decisions
- **Voice locale**: TextToSpeech set to `Locale.UK` matching the desktop British butler persona
- **Command response polling**: VoiceEngine polls CommandQueueDao every 500ms (max 30s) for desktop response
- **Database encryption**: SQLCipher passphrase derived from the signing key stored in EncryptedSharedPreferences
- **Offline-first**: All commands queue locally then flush on sync; foreground service retries with exponential backoff
- **Bottom nav hidden on bootstrap**: BottomNavBar only shown after onboarding completes

## Verification: 16/16 checks passed
All plan requirements verified present in the codebase.
