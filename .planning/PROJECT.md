# Jarvis — Limitless Personal AI Assistant

## What This Is

A local-first, always-learning personal AI assistant inspired by Iron Man's J.A.R.V.I.S., built for Conner. The desktop Python engine is the brain — memory store, intelligence routing, proactive engine, heavy computation. The Samsung Galaxy S25 Ultra native Kotlin app is the primary daily interface — voice, calls, location, notifications, camera. Together they form a complete AI assistant that manages day-to-day life, learns from every interaction, and becomes smarter over time.

## Core Value

Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## Current Milestone: v2.0 Native Android App

**Goal:** Build a native Kotlin Android app that transforms the Samsung Galaxy S25 Ultra from a web-panel-only interface into a full-featured smart mobile companion for the Jarvis desktop brain.

**Target features:**
- Voice assistant with on-device STT/TTS
- Call screening and spam defense
- Intelligent scheduling from notification parsing
- Prescription management with alarm reminders
- Proactive notifications from desktop engine
- Material Design 3 dashboard (home, chat, memory, settings)
- Contextual silence (meeting/driving/sleep detection)
- Relationship memory and social context
- Financial watchdog (bank SMS/email parsing)
- Habit tracker with adaptive nudges
- Commute intelligence (traffic + parking memory)
- Document scanner with encrypted OCR storage

## Requirements

### Validated (v1.0 Desktop Engine — Shipped)

- ✓ SQLite + FTS5 + sqlite-vec memory engine with semantic search — v1.0
- ✓ Knowledge graph with fact extraction, contradiction detection, fact locks — v1.0
- ✓ Intelligence routing: Ollama + Anthropic with intent-based complexity routing — v1.0
- ✓ Real calendar (ICS), email (IMAP), task (Todoist) connectors + daily briefing — v1.0
- ✓ Multi-source knowledge harvesting (MiniMax, Kimi, Gemini, Claude Code, Codex) — v1.0
- ✓ Persona layer with British butler personality + contextual tone — v1.0
- ✓ Speech-to-text via faster-whisper — v1.0
- ✓ Continuous learning engine with cross-branch reasoning + golden task eval — v1.0
- ✓ Changelog-based mobile-desktop sync with encrypted transport — v1.0
- ✓ Proactive intelligence with wake word detection + adaptive nudges — v1.0
- ✓ Cost tracking with budget controls + adversarial self-testing — v1.0
- ✓ HMAC-signed mobile API with owner guard + device trust — v1.0
- ✓ CQRS command bus architecture (70+ commands) — v1.0
- ✓ Edge-TTS British male neural voice (en-GB-ThomasNeural) — v1.0
- ✓ Daemon mode with idle detection and gaming auto-pause — v1.0
- ✓ 473 passing tests — v1.0

### Active (v2.0 Android App)

See: .planning/REQUIREMENTS.md

### Out of Scope

- Cloud-hosted deployment — local-first is non-negotiable for privacy and control
- Training custom LLMs from scratch — use best available models via API + local inference
- Multi-user support — this is Conner's personal assistant, single-owner by design
- iOS app — Samsung Galaxy S25 Ultra only
- Wear OS companion — phone app first, wearable later
- Widgets — Material You widgets deferred to v2.1

## Context

- **Runtime**: Windows 11 desktop PC (brain), Samsung Galaxy S25 Ultra (interface)
- **Desktop engine**: 50+ Python source files, 473 passing tests, CQRS command bus architecture
- **Mobile API**: HTTP server on port 8787, Bearer token + HMAC-SHA256 signing, LAN access at 192.168.50.156
- **Android target**: Android 15+ (API 35), Kotlin, Jetpack Compose, Material 3
- **Design doc**: `docs/plans/2026-02-23-jarvis-android-app-design.md`
- **Sync protocol**: Desktop already has /sync/pull and /sync/push endpoints with Fernet encryption
- **Intelligence**: Ollama (local), Anthropic Claude (cloud), intent classification already built

## Constraints

- **Privacy**: All core data stays local. Cloud APIs used for inference only, never for storage.
- **Platform**: Android 15+ (Samsung Galaxy S25 Ultra). Desktop brain is Windows 11.
- **Cost**: $0/month infrastructure — all on-device + existing desktop. Cloud LLM only when needed.
- **Network**: LAN (WiFi) for sync. Offline-first with command queuing.
- **Security**: Biometric lock, SQLCipher for Room DB, HMAC signing on all API calls, EncryptedSharedPreferences.
- **Language**: Kotlin with Jetpack Compose. No Java, no Flutter, no React Native.
- **Dependencies**: Minimize third-party. Prefer Android platform APIs (SpeechRecognizer, CallScreeningService, NotificationListenerService, ML Kit).

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SQLite + FTS5 for memory storage | No external DB server, great Python support, full-text search | ✓ Good |
| Local embeddings (sentence-transformers, nomic-embed-text-v1.5) | Privacy-first, no API calls for retrieval | ✓ Good |
| Claude Opus for complex reasoning, Ollama for routine | Best reasoning when needed, cost-efficient | ✓ Good |
| Edge-TTS for desktop voice output | High-quality British neural voices | ✓ Good |
| CQRS command bus architecture | Clean decomposition, 70+ commands | ✓ Good |
| Fernet encryption for sync payloads | PBKDF2HMAC 480K iterations, zlib compression | ✓ Good |
| Native Kotlin over cross-platform | Full access to Android platform APIs (CallScreening, NotificationListener) | — Pending |
| Jetpack Compose + Material 3 | Modern Android UI, Material You dynamic theming | — Pending |
| Room + SQLCipher for local Android DB | Encrypted local storage, offline-first | — Pending |
| Retrofit2 + OkHttp with HMAC interceptor | Standard Android networking, clean interceptor pattern | — Pending |
| Phone as sensor/interface, desktop as brain | Keeps heavy computation on PC, phone does sensing + display | — Pending |

---
*Last updated: 2026-02-23 after v2.0 milestone start*
