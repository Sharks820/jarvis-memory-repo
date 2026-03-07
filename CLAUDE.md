# CLAUDE.md

## Project Overview

Jarvis is a local-first personal AI assistant for Conner. Two components:
- **Desktop Engine** (Python, `engine/`): The brain — memory, intelligence routing, knowledge graph, proactive engine, voice, connectors
- **Android App** (Kotlin, `android/`): The body — voice, calls, notifications, location, camera, daily UI

Both milestones are complete: v1.0 Desktop Engine (phases 1-9) and v2.0 Android App (phases 10-13).

## Quick Start

```bash
# Activate venv and run tests
.venv/Scripts/activate
python -m pytest engine/tests/ -x -q    # ~4400 tests, ~2min

# Run the engine
jarvis-engine daemon-run                 # Daemon mode (primary)
jarvis-engine ops-brief                  # Daily briefing
jarvis-engine voice-run                  # Voice assistant

# Start all services (daemon + mobile API + widget)
powershell scripts/start-jarvis-services.ps1
```

## Architecture

- **CQRS command bus** (`engine/src/jarvis_engine/app.py`): 70+ commands, handler registration
- **CLI entrypoint**: `engine/src/jarvis_engine/main.py` (~3000 lines, all CLI commands)
- **Memory**: SQLite + FTS5 + sqlite-vec (`engine/src/jarvis_engine/memory/`)
- **Knowledge graph**: NetworkX + SQLite with fact locks and contradiction detection (`engine/src/jarvis_engine/knowledge/`)
- **Intelligence gateway**: Ollama (local) + Anthropic + Groq/Kimi + Gemini (`engine/src/jarvis_engine/gateway/`)
- **Mobile API**: HMAC-SHA256 signed HTTP on port 8787 (`engine/src/jarvis_engine/mobile_api.py`)
- **Android**: Jetpack Compose + Room/SQLCipher + Hilt DI + Retrofit2 (`android/`)

## Key Patterns

- **All 35 engine modules are actively used** — verified Feb 2026. Do not delete any without re-auditing imports.
- **Handler pattern**: Handlers in `engine/src/jarvis_engine/handlers/` use lazy imports of domain modules
- **Hilt DI in Android services**: `CallScreeningService` and `NotificationListenerService` use `@EntryPoint` + `EntryPointAccessors` pattern (NOT `@AndroidEntryPoint`)
- **Room DB at version 11** with 16 entities and 10 explicit `Migration` objects (1->2 through 10->11, NEVER use `fallbackToDestructiveMigration`)
- **HMAC timestamps must be integers** — `Math.floor(Date.now() / 1000)`, not floats
- **Phone numbers masked in logs** — show only last 4 digits for PII protection

## Critical Decisions

- Phone is sensor/interface layer, desktop is brain (all LLM processing on desktop)
- Offline-first: Room DB command queue caches when desktop unreachable, auto-flushes on reconnect
- SQLCipher passphrase derived from signing key via EncryptedSharedPreferences
- Privacy keywords in queries force local Ollama routing regardless of complexity
- Notification channels: URGENT (bypasses DND), IMPORTANT, ROUTINE, BACKGROUND
- Accelerometer-based driving detection (avoids Google Play Services dependency)
- Context detection runs every 2 minutes in foreground service sync loop
- Nudge adaptive suppression: >= 80% ignore rate over 20 samples auto-suppresses

## File Layout

```
engine/src/jarvis_engine/       # Python desktop engine (35+ modules)
  memory/                       # SQLite + FTS5 + sqlite-vec
  knowledge/                    # Fact graph with locks and contradictions
  gateway/                      # LLM routing (Ollama/Anthropic/Groq/Gemini)
  learning/                     # Conversation learning + cross-branch reasoning
  harvesting/                   # Multi-provider knowledge harvesting
  proactive/                    # Triggers, notifications, cost tracking, self-test
  security/                     # Threat detection, forensic logging, containment, AI defense
  sync/                         # Changelog-based encrypted mobile-desktop sync
  handlers/                     # CQRS command handlers (8 handler files)
  commands/                     # Command dataclasses
engine/tests/                   # ~4400 tests
android/app/src/main/java/com/jarvis/assistant/
  data/                         # Room entities, DAOs, database (v11, 16 entities)
  di/                           # Hilt AppModule (15 DAOs)
  feature/                      # Domain features (callscreen, scheduling, prescription, etc.)
  service/                      # JarvisService foreground sync loop
  ui/                           # Compose screens + ViewModels
mobile/                         # Quick Panel HTML + Android ingest client
scripts/                        # PowerShell launchers, installers, maintenance
.planning/                      # GSD workflow state, phase plans, runtime data
```

## Testing

```bash
python -m pytest engine/tests/ -x -q     # Run all (fast, ~100s)
python -m pytest engine/tests/test_smoke.py -v   # Smoke tests (261+ tests, anti-regression)
python -m pytest engine/tests/test_main.py -x -q   # CLI tests only
python -m pytest engine/tests/ -k "memory" -q       # Memory tests only
```

1 test skipped (live Ollama). All others should pass.

**Quality gates (run before every merge):**
```bash
ruff check engine/src && ruff format --check engine/src
bandit -r engine/src -ll -x engine/src/jarvis_engine/security/honeypot.py
cd engine && PYTHONPATH=src python -m pytest tests/ -x -q --cov=jarvis_engine --cov-fail-under=50
cd engine && PYTHONPATH=src python -m pytest tests/test_smoke.py -v
```

## Security

- `.planning/security/` contains signing keys and tokens — gitignored, never commit
- `.planning/brain/` contains memory data — gitignored
- Owner guard with device trust (galaxy_s25_primary registered)
- HMAC-SHA256 with nonce replay protection (120s window) on mobile API
- Fernet encryption with PBKDF2HMAC for sync payloads
- DPAPI encryption for token, signing_key, and master_password in widget config
- Master password required for sensitive Android operations (prescriptions, finance, documents)
- **Security module** (`engine/src/jarvis_engine/security/`): 17 modules, 482 tests
  - Threat detection (8 rule types), forensic hash-chain logging, IP auto-escalation blocklist
  - 3-layer prompt injection firewall (regex + structural + semantic)
  - Attack pattern memory (learns forever), output scanner (manipulation + exfil detection)
  - Identity monitor, honeypot endpoints, session hijack detection
  - 5-level autonomous containment (throttle → full kill), graduated alert chain
  - Adaptive defense (auto-rule generation), memory provenance (trust levels + quarantine)
  - CQRS defense commands (security status, threat report, export forensics, etc.)

## Common Gotchas

- `adapters.py` contains media adapters (Image/Video/3D), NOT intelligence routing — that's in `gateway/`
- CalendarContract must use `Instances` URI (not `Events`) to detect recurring events
- ConcurrentHashMap values (MutableList) are NOT thread-safe — wrap with `Collections.synchronizedList()`
- `SensorManager.registerListener()` from Dispatchers.IO needs `Handler(Looper.getMainLooper())`
- Context detection saves/restores user's original ringer mode to avoid overriding preferences
- Spam DB sync throttled to 10-minute intervals within the 30s sync loop
- OCR text truncated to 5000 chars for desktop sync (practical /command endpoint limits)

## Planning & GSD Workflow

- Source of truth: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`
- Phase plans: `.planning/phases/{NN}-{name}/{NN}-{PP}-PLAN.md`
- Read STATE.md before making changes
- Update STATE.md after meaningful changes
- Prefer small, verifiable commits by phase
