# Jarvis — Limitless Personal AI Assistant

## What This Is

A local-first, always-learning personal AI assistant inspired by Iron Man's J.A.R.V.I.S., built for Conner. The desktop Python engine is the brain — memory store, intelligence routing, proactive engine, heavy computation. The Samsung Galaxy S25 Ultra native Kotlin app is the primary daily interface — voice, calls, location, notifications, camera. Together they form a complete AI assistant that manages day-to-day life, learns from every interaction, and becomes smarter over time.

## Core Value

Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## Current Milestone: v6.0 Jarvis Unity Agent

**Goal:** Build an autonomous development agent that can create complete Unity games from text/voice instructions — planning, coding, modeling, testing, and debugging autonomously with user approval at milestones.

**Target features:**
- Agent Core: TaskPlanner + StepExecutor + ReflectionLoop for autonomous multi-step task execution
- Pluggable Tool Layer: Unity, Blender, tripo.io, Shell, File, Web + dynamic tool registration
- Unity Editor Plugin (C#): JarvisEditorBridge with reflection-based JSON-RPC covering full Unity API
- Self-testing & debugging loop: compile → test → play mode → fix with 5-retry cap and error pattern learning
- Deep Unity 6.3 knowledge seeding: API reference, patterns, common errors, best practices in KG
- Smart approval: create=auto, destroy/spend=requires approval
- Real-time progress streaming to widget and Unity Editor panel
- Learn-as-you-go: successful patterns and error fixes accumulated in knowledge graph

**Target features:**
- Voice-to-text overhaul with best-in-class STT (Parakeet TDT / Deepgram Nova-3 / Silero VAD)
- Learning system activation — preferences, feedback, usage patterns actually used in responses
- Widget/Brain UI live-updating for all bot functions (mission cancel, learning, etc.)
- Activity feed in primary conversation display showing real-time bot activity
- Comprehensive platform stability scan and bug fixes
- Mobile app readiness verification — sync, reliability, learning integration

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

### Validated (v2.0 Android App — Shipped)

- ✓ Native Kotlin Android app with Jetpack Compose + Material 3 — v2.0
- ✓ Room + SQLCipher encrypted local database — v2.0
- ✓ Call screening, scheduling, prescriptions, notifications — v2.0
- ✓ Contextual silence, relationship memory, financial watchdog — v2.0
- ✓ Habit tracker, commute intelligence, document scanner — v2.0

### Validated (v3.0 Hardening — Shipped)

- ✓ 4-CLI scan gauntlet, all 4 CLIs clean — v3.0
- ✓ 7-pillar security architecture with SecurityOrchestrator — v3.0
- ✓ 4 CLI-based LLMs integrated into ModelGateway — v3.0
- ✓ Autonomous mission system with auto-generate and retry — v3.0
- ✓ 4136 tests passing — v3.0

### Active (v4.0 Intelligence & Voice)

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
- **Desktop engine**: 60+ Python source files, 4138 passing tests, CQRS command bus architecture
- **Mobile API**: HTTP server on port 8787, Bearer token + HMAC-SHA256 signing, LAN access at 192.168.50.156
- **Android target**: Android 15+ (API 35), Kotlin, Jetpack Compose, Material 3
- **Design doc**: `docs/plans/2026-02-23-jarvis-android-app-design.md`
- **Sync protocol**: Desktop already has /sync/pull and /sync/push endpoints with Fernet encryption
- **Intelligence**: Ollama (local), Kimi K2/Groq (primary cloud), Claude/Codex/Gemini/Kimi CLIs, intent-based routing

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
*Last updated: 2026-03-02 after v4.0 milestone start*
