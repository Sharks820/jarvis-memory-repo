# Milestones

## v1.0 — Desktop Engine (Complete)

**Completed:** 2026-02-23
**Phases:** 1–9 (18 plans executed)
**Tests:** 473 passing
**Duration:** ~3 hours total execution

### What Shipped

- SQLite + FTS5 + sqlite-vec memory engine with semantic search
- Knowledge graph with fact extraction, contradiction detection, fact locks
- Intelligence routing (Ollama + Anthropic) with intent-based complexity routing
- Real calendar (ICS), email (IMAP), task (Todoist) connectors + daily briefing
- Multi-source knowledge harvesting (MiniMax, Kimi, Gemini, Claude Code, Codex)
- Persona layer with British butler personality + contextual tone
- Speech-to-text via faster-whisper
- Continuous learning engine with cross-branch reasoning + golden task eval
- Changelog-based mobile-desktop sync with encrypted transport
- Proactive intelligence with wake word detection + adaptive nudges
- Cost tracking with budget controls + adversarial self-testing
- HMAC-signed mobile API with owner guard + device trust

### Key Decisions

- SQLite for everything (memory, knowledge graph, cost tracking, sync changelog)
- Local embeddings via sentence-transformers (nomic-embed-text-v1.5)
- CQRS command bus architecture (70+ commands)
- Fernet encryption with PBKDF2HMAC for sync payloads

### Last Phase Number

Phase 9 (last plan: 09-02)

## v2.0 — Native Android App (Complete)

**Completed:** 2026-02-25
**Phases:** 10–13 (11 plans executed)
**Tests:** 3880 passing (combined Python + Kotlin)

### What Shipped

- Native Kotlin Android app for Samsung Galaxy S25 Ultra
- Jetpack Compose + Material 3 + Material You dynamic theming
- Room + SQLCipher encrypted local database (v10, 16 entities)
- HMAC-SHA256 signed API client with offline command queue
- Call screening (CallScreeningService) with local spam database
- Intelligent scheduling from notification parsing (NotificationListenerService)
- Prescription management with EXACT_ALARM reminders
- Proactive notifications (4 channels: URGENT/IMPORTANT/ROUTINE/BACKGROUND)
- Contextual silence (meeting/driving/sleep detection via accelerometer)
- Relationship memory with pre-call context
- Financial watchdog (bank SMS/email parsing)
- Habit tracker with adaptive nudge suppression
- Commute intelligence with parking memory
- Document scanner with ML Kit OCR + encrypted sync

### Last Phase Number

Phase 13

## v3.0 — Hardening & Security (Complete)

**Completed:** 2026-03-01
**Phases:** 2 (4-CLI Scan Gauntlet + Security Deep Hardening)
**Tests:** 4136 passing, 5 skipped, 0 failures

### What Shipped

- 16-round 4-CLI scan gauntlet (Opus, Codex, Gemini, Kimi) — all clean
- ~30 real bugs fixed across 120+ findings evaluated
- 7-pillar security architecture: SecurityOrchestrator, Owner Session Auth, Bot Governance, Threat Intelligence, Legal Offensive Response, Home Network Defense, Identity Protection
- 10 new security source files, 13 new test files, ~256 new tests
- Owner session with Argon2id/PBKDF2, 30min idle timeout
- Transparency dashboard at GET /security/dashboard
- 8 CQRS defense command handlers
- 14 Opus-recommended performance optimizations (O(1) dispatch, frozenset paths, batch DELETE, ThreadPoolExecutor)
- 4-CLI LLM integration (Claude Code, Codex, Gemini CLI, Kimi CLI) with intent-based routing
- Autonomous mission system (auto-generate, retry, relaxed verification)

### Key Decisions

- 4 CLI-based LLMs wired into ModelGateway alongside API providers
- IntentClassifier routes: math→codex-cli, complex→claude-cli, creative→gemini-cli, routine→kimi-k2, private→Ollama
- CFAA-compliant offensive response only (evidence + reporting, no hack-back)
- All security module imports try/except wrapped for graceful degradation

### Last Phase Number

Phase 2 (v3.0 numbering)
