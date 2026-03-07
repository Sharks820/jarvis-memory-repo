# Jarvis — Local-First Personal AI Assistant

> A private, always-learning AI platform built for serious daily use.  
> Desktop Python engine + Android native client.  Privacy-first, offline-capable, continuously improving.

---

## Table of Contents

1. [What Is Jarvis](#1-what-is-jarvis)
2. [System Requirements](#2-system-requirements)
3. [Quick Start](#3-quick-start)
4. [Architecture Overview](#4-architecture-overview)
5. [Running Jarvis](#5-running-jarvis)
   - [Daemon (Always-On)](#51-daemon-always-on)
   - [Voice Assistant](#52-voice-assistant)
   - [Daily Briefing & Ops](#53-daily-briefing--ops)
   - [Runtime Controls](#54-runtime-controls)
   - [Gaming Mode](#55-gaming-mode)
6. [Memory & Knowledge](#6-memory--knowledge)
7. [Learning & Missions](#7-learning--missions)
8. [Task Execution (Ops Pack)](#8-task-execution-ops-pack)
9. [Mobile Integration](#9-mobile-integration)
   - [Starting the Mobile API](#91-starting-the-mobile-api)
   - [Quick Panel & Chat](#92-quick-panel--chat)
   - [Desktop Widget](#93-desktop-widget)
10. [Security & Owner Guard](#10-security--owner-guard)
11. [Identity & Persona](#11-identity--persona)
12. [Voice Commands Reference](#12-voice-commands-reference)
13. [Developer Workflow](#13-developer-workflow)
    - [Quality Gates](#131-quality-gates)
    - [Tests](#132-tests)
    - [Maintenance Scripts](#133-maintenance-scripts)
14. [Documentation Index](#14-documentation-index)
15. [Project Planning](#15-project-planning)

---

## 1. What Is Jarvis

Jarvis is a **private, local-first AI assistant** for Conner.  Two components work together:

| Component | Language | Role |
|---|---|---|
| **Desktop Engine** (`engine/`) | Python | Brain — memory, intelligence routing, knowledge graph, proactive alerts, voice, task execution |
| **Android App** (`android/`) | Kotlin | Body — voice interface, call screening, notifications, location awareness, daily UI |

**Core guarantees:**
- **Learns from every interaction.** Memory is persistent, structured, and tiered.
- **Never sends private data to the cloud** unless explicitly permitted. Health, finance, contacts, and calendar always stay local.
- **Offline-capable.** Android queues commands when the desktop is unreachable and flushes automatically on reconnect.
- **Security-first.** 17-module security layer with prompt injection firewall (including base64/hex/URL-encoded attack detection), output scanning, forensic logging, and autonomous containment.

**Current version:** v5.0 — Reliability, Continuity, and Autonomous Learning  
**Test baseline:** 4,600+ tests | CI gates: lint → security → coverage → smoke

---

## 2. System Requirements

| Requirement | Minimum |
|---|---|
| OS | Windows 10/11 (desktop engine), Android 10+ (app) |
| Python | 3.11 or 3.12 |
| RAM | 8 GB (16 GB recommended for large models) |
| GPU | Optional — Ollama will use CPU if no GPU available |
| Ollama | Latest (`ollama.ai`) — required for local LLM |
| Android | Galaxy S25 Ultra (primary), any Android 10+ device supported |

---

## 3. Quick Start

```powershell
# 1. Clone and bootstrap (first time only)
cd C:\Users\Conner\jarvis-memory-repo
.\scripts\bootstrap.ps1

# 2. Verify installation
.venv\Scripts\jarvis-engine.exe status

# 3. Start all services (daemon + mobile API + widget)
.\scripts\start-jarvis-services.ps1

# 4. Run tests to confirm baseline
.venv\Scripts\activate
cd engine
$env:PYTHONPATH = "src"
pytest tests/ -q --cov=jarvis_engine --cov-fail-under=50
```

---

## 4. Architecture Overview

```
engine/src/jarvis_engine/
  app.py              CQRS command bus (70+ commands)
  main.py             CLI entrypoint (~3000 lines)
  gateway/            Intelligence routing: Ollama ↔ Anthropic ↔ Groq ↔ Gemini
  memory/             SQLite + FTS5 + sqlite-vec memory engine
  knowledge/          NetworkX knowledge graph with fact locks + contradiction detection
  learning/           Feedback loop, preference tracking, cross-branch reasoning
  security/           17-module security layer (firewall, containment, forensic log)
  proactive/          Triggers, alerts, nudges, self-test
  harvesting/         Multi-provider knowledge harvesting
  sync/               Changelog-based encrypted mobile↔desktop sync
  handlers/           CQRS handlers (lazy-import pattern)

android/app/src/main/java/com/jarvis/assistant/
  data/               Room v11 (16 entities, 10 explicit migrations, SQLCipher)
  di/                 Hilt AppModule (15 DAOs)
  feature/            Call screening, scheduling, prescriptions, documents
  service/            JarvisService foreground sync loop (runs every 2 min)
  ui/                 Jetpack Compose + ViewModels

mobile/               Quick Panel HTML + Android ingest client
scripts/              PowerShell launchers, installers, nightly maintenance
.planning/            STATE.md, ROADMAP.md, phase plans, runtime data
```

**Key design decisions:**
- Phone = sensor/interface layer; desktop = brain (all LLM processing on desktop)
- HMAC-SHA256 on all mobile API requests (timestamps must be integers)
- SQLCipher passphrase derived from signing key via `EncryptedSharedPreferences`
- Privacy keywords → forced local Ollama routing, never cloud
- Android Room DB is at **version 11** — never use `fallbackToDestructiveMigration`

---

## 5. Running Jarvis

### 5.1 Daemon (Always-On)

```powershell
# Install as a scheduled startup task
.\scripts\install-jarvis-startup.ps1 -IntervalSeconds 120 -IdleIntervalSeconds 900 -IdleAfterSeconds 300 -StartNow

# Run manually in current terminal
.\scripts\start-jarvis-daemon.ps1 -IntervalSeconds 120 -IdleIntervalSeconds 900 -IdleAfterSeconds 300

# Remove startup task
.\scripts\uninstall-jarvis-startup.ps1
```

The daemon automatically runs pending missions, syncs with mobile, runs proactive alerts, and performs nightly maintenance unless `--skip-missions` is set.

### 5.2 Voice Assistant

```powershell
cd engine
$env:PYTHONPATH = "src"

# Live voice input (requires microphone)
python -m jarvis_engine.main voice-run --execute --speak

# Voice input with speaker verification (required for privileged commands)
python -m jarvis_engine.main voice-run --execute --speak --voice-user conner --voice-auth-wav .\samples\conner_live.wav

# Pre-specified text (no mic needed)
python -m jarvis_engine.main voice-run --text "Jarvis, sync my calendar and inbox" --execute --speak

# List available TTS voices
python -m jarvis_engine.main voice-list

# Speak a specific message
python -m jarvis_engine.main voice-say --text "Good morning. Your top priorities are ready." --profile jarvis_like

# Enroll / verify speaker
python -m jarvis_engine.main voice-enroll --user-id conner --wav .\samples\conner_enroll.wav --replace
python -m jarvis_engine.main voice-verify --user-id conner --wav .\samples\conner_check.wav --threshold 0.82
```

> **Security note:** `--execute` on privileged commands requires `--voice-auth-wav`.

### 5.3 Daily Briefing & Ops

```powershell
cd engine && $env:PYTHONPATH = "src"

# Morning briefing
python -m jarvis_engine.main ops-brief --snapshot-path ..\planning\ops_snapshot.json

# Sync ops snapshot data
python -m jarvis_engine.main ops-sync

# Run full autopilot pipeline
python -m jarvis_engine.main ops-autopilot

# Export pending actions to JSON
python -m jarvis_engine.main ops-export-actions --snapshot-path ..\planning\ops_snapshot.json

# Run generated actions (dry-run by default)
python -m jarvis_engine.main automation-run --actions-path ..\planning\actions.generated.json

# Intelligence dashboard
python -m jarvis_engine.main intelligence-dashboard
```

### 5.4 Runtime Controls

Emergency and maintenance controls — all take effect immediately:

```powershell
cd engine && $env:PYTHONPATH = "src"

python -m jarvis_engine.main runtime-control --pause --reason "maintenance"
python -m jarvis_engine.main runtime-control --resume
python -m jarvis_engine.main runtime-control --safe-on --reason "no execution"
python -m jarvis_engine.main runtime-control --safe-off
python -m jarvis_engine.main runtime-control --reset
```

### 5.5 Gaming Mode

Pauses the daemon autopilot workload during gaming sessions:

```powershell
cd engine && $env:PYTHONPATH = "src"

python -m jarvis_engine.main gaming-mode --enable --reason "gaming session"
python -m jarvis_engine.main gaming-mode --disable
python -m jarvis_engine.main gaming-mode                             # status
python -m jarvis_engine.main gaming-mode --auto-detect on            # auto-pause on game launch
```

Optional process watchlist for auto-detect: `.planning/gaming_processes.json`

---

## 6. Memory & Knowledge

```powershell
cd engine && $env:PYTHONPATH = "src"

# Memory status and search
python -m jarvis_engine.main brain-status
python -m jarvis_engine.main brain-context --query "gaming pause and resume behavior"
python -m jarvis_engine.main brain-regression         # duplicate ratio, conflicts, entropy

# Ingest content directly
python -m jarvis_engine.main ingest --source user --kind semantic --task-id t1 --content "example memory"

# Snapshots and maintenance
python -m jarvis_engine.main memory-snapshot --create --note "manual checkpoint"
python -m jarvis_engine.main memory-snapshot --verify-path .\.planning\brain\snapshots\brain-snapshot-YYYYMMDD-HHMMSS.zip
python -m jarvis_engine.main memory-maintenance --keep-recent 1800 --snapshot-note nightly
python -m jarvis_engine.main brain-compact --keep-recent 1800

# Web research → auto-ingest
python -m jarvis_engine.main web-research --query "best Samsung spam call blocking setup"

# Mobile↔desktop sync
python -m jarvis_engine.main mobile-desktop-sync
python -m jarvis_engine.main self-heal --force-maintenance
```

**Memory pipeline notes:**
- `auto-ingest` dedupes, redacts sensitive tokens, and writes to both event memory and branch-indexed long-term brain memory
- `brain-context` returns compact, relevance-ranked packets to avoid context clutter
- `brain-regression` reports duplicate ratio, unresolved conflicts, and branch entropy

---

## 7. Learning & Missions

Auto-research + verified ingestion missions:

```powershell
cd engine && $env:PYTHONPATH = "src"

# Create a learning mission
python -m jarvis_engine.main mission-create \
  --topic "Unity 6.3 game architecture" \
  --objective "Learn production-ready systems" \
  --source google --source reddit --source official_docs

# Check mission status
python -m jarvis_engine.main mission-status --last 5

# Run a specific mission manually
python -m jarvis_engine.main mission-run --id m-YYYYMMDDHHMMSS
```

The daemon auto-runs pending missions on each cycle unless `--skip-missions` is set.

**Growth tracking:**
```powershell
python -m jarvis_engine.main growth-eval --model qwen3:latest --think off
python -m jarvis_engine.main growth-eval --model qwen3:latest --think off --accept-thinking
python -m jarvis_engine.main growth-report --last 10
python -m jarvis_engine.main growth-audit --run-index -1
```

History file: `.planning/capability_history.jsonl`

---

## 8. Task Execution (Ops Pack)

Multimodal task routing with code, image, video, and 3D model generation:

```powershell
cd engine && $env:PYTHONPATH = "src"

# Code generation
python -m jarvis_engine.main run-task \
  --type code \
  --prompt "Write a robust Python retry decorator." \
  --execute --quality-profile max_quality \
  --output-path ..\generated\retry_decorator.py

# Image generation
python -m jarvis_engine.main run-task \
  --type image \
  --prompt "Concept art of a clean AI workstation" \
  --execute --quality-profile max_quality

# Video generation
python -m jarvis_engine.main run-task \
  --type video \
  --prompt "30-second product teaser" \
  --execute --approve-privileged --quality-profile max_quality

# 3D model generation
python -m jarvis_engine.main run-task \
  --type model3d \
  --prompt "Low poly desk drone" \
  --execute --approve-privileged
```

**Quality profiles:** `fast` | `balanced` | `max_quality`  
**Fallback chain:** Override with `JARVIS_CODE_MODEL_FALLBACKS` (comma-separated model IDs)

---

## 9. Mobile Integration

### 9.1 Starting the Mobile API

```powershell
cd engine
$env:JARVIS_MOBILE_TOKEN = "set-a-long-random-token"
$env:JARVIS_MOBILE_SIGNING_KEY = "set-a-different-long-random-key"
$env:PYTHONPATH = "src"
python -m jarvis_engine.main serve-mobile --host 127.0.0.1 --port 8787
```

**API endpoints (all require HMAC-SHA256 authentication):**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Status check |
| `GET` | `/dashboard` | Intelligence ranking + ETA projections |
| `GET` | `/settings` | Runtime + gaming settings |
| `POST` | `/settings` | Update runtime settings (pause, safe mode, gaming) |
| `POST` | `/command` | Run plain-language command (no CLI syntax needed) |
| `GET` | `/quick` | Compact quick panel UI |

**`POST /settings` fields:** `daemon_paused`, `safe_mode`, `gaming_enabled`, `gaming_auto_detect`, `reason`, `reset`  
**Device trust:** Include `X-Jarvis-Device-Id` header. First-time bootstrap: add `X-Jarvis-Master-Password`.

See `docs/MOBILE_SETUP.md` for full signed-request format and network setup.  
Galaxy S25 guide: `docs/SAMSUNG_GALAXY_S25_SETUP.md`

### 9.2 Quick Panel & Chat

```powershell
# Open browser-based quick panel (chat + voice)
.\scripts\open-jarvis-quick-panel.ps1 -BindHost 127.0.0.1 -Port 8787

# Install desktop hotkey (default Ctrl+Alt+J)
.\scripts\install-jarvis-quick-access.ps1 -BindHost 127.0.0.1 -Port 8787
```

### 9.3 Desktop Widget

```powershell
# Launch native desktop widget
.\scripts\start-jarvis-widget.ps1

# Install widget hotkey (default Ctrl+Alt+K)
.\scripts\install-jarvis-widget-shortcut.ps1
```

**Phone action queue:**
```powershell
cd engine && $env:PYTHONPATH = "src"
python -m jarvis_engine.main phone-action --action send_sms --number +14155551234 --message "Running late."
python -m jarvis_engine.main phone-action --action place_call --number +14155551234
python -m jarvis_engine.main phone-spam-guard --call-log-path ..\planning\phone_call_log.json
```

---

## 10. Security & Owner Guard

### Owner Guard

```powershell
cd engine && $env:PYTHONPATH = "src"

python -m jarvis_engine.main owner-guard --enable --owner-user conner
python -m jarvis_engine.main owner-guard --set-master-password "<your_master_password>"
python -m jarvis_engine.main owner-guard --trust-device galaxy_s25_primary
python -m jarvis_engine.main owner-guard          # status
```

### Security Architecture

The security layer has 17 modules across 5 tiers:

| Tier | Modules | Function |
|---|---|---|
| **Detection** | threat_detector, attack_memory, ip_tracker | Pattern matching + learning threat history |
| **Firewall** | injection_firewall, output_scanner | 3-layer prompt injection detection (patterns + structural + semantic); base64/hex/URL-encoded attack detection; output exfil scanning |
| **Identity** | identity_monitor, identity_shield, owner_session | Voice auth, device trust, session integrity |
| **Containment** | containment, adaptive_defense, scope_enforcer | Autonomous 5-level containment (throttle → kill); auto-generated defense rules |
| **Forensics** | forensic_logger, alert_chain, action_auditor | Hash-chain tamper-evident logs, graduated alert escalation |

> **Injection firewall:** Detects attacks in plain text, base64-encoded, hex-encoded, and URL-percent-encoded payloads. The base64 check decodes first (threshold: 16 chars minimum) before inspecting keywords — this is intentionally sensitive because real attack strings are often short.

**Voice security guide:** `docs/VOICE_SECURITY_SETUP.md`  
**Spam defense guide:** `docs/SAMSUNG_SPAM_CALL_DEFENSE.md`

---

## 11. Identity & Persona

```powershell
cd engine && $env:PYTHONPATH = "src"

# Configure persona
python -m jarvis_engine.main persona-config \
  --enable --mode jarvis_british \
  --style brilliant_secret_agent \
  --humor-level 2

# View current persona
python -m jarvis_engine.main persona-config
```

Default voice: **en-GB-ThomasNeural** (Edge TTS British male).  
Note: exact MCU/actor voice cloning is not guaranteed in the local stack; `jarvis_like` selects the closest available local voice profile.

---

## 12. Voice Commands Reference

Jarvis understands natural language. Examples:

| Category | Example |
|---|---|
| **Mode control** | "Jarvis, enable gaming mode" / "disable gaming mode" |
| **Sync** | "Jarvis, sync mobile desktop" |
| **Health** | "Jarvis, self heal" *(requires voice auth)* |
| **Information** | "Jarvis, what is the weather in Dallas" |
| **Research** | "Jarvis, search the web for Unity 6.3 performance best practices" |
| **Monitoring** | "Jarvis, gaming mode status" |

All voice commands follow the prefix `"Jarvis, ..."`. Commands that modify system state require `--voice-auth-wav` via CLI or biometric confirmation in the Android app.

---

## 13. Developer Workflow

### 13.1 Quality Gates

All must pass before merging to `main`:

```bash
# 1. Lint
ruff check engine/src && ruff format --check engine/src

# 2. Security scan (no HIGH severity findings allowed)
bandit -r engine/src -ll -x engine/src/jarvis_engine/security/honeypot.py

# 3. Tests with coverage (≥50% threshold, runs on Python 3.11 and 3.12 in CI)
cd engine && PYTHONPATH=src python -m pytest tests/ -x -q \
  --cov=jarvis_engine --cov-fail-under=50

# 4. Smoke tests (261+ must pass — covers all 137 public modules + critical behaviors)
cd engine && PYTHONPATH=src python -m pytest tests/test_smoke.py -v
```

CI is defined in `.github/workflows/ci.yml`.  
A daily smoke run runs at 06:00 UTC via `.github/workflows/smoke-test.yml`.

### 13.2 Tests

```bash
# Full suite
cd engine && PYTHONPATH=src python -m pytest tests/ -q

# Targeted by area
PYTHONPATH=src python -m pytest tests/test_smoke.py -v          # smoke / regression
PYTHONPATH=src python -m pytest tests/test_security_hardening.py -v  # security
PYTHONPATH=src python -m pytest tests/ -k "memory" -q           # memory subsystem
PYTHONPATH=src python -m pytest tests/ -k "gateway" -q          # gateway / routing

# With coverage report
PYTHONPATH=src python -m pytest tests/ --cov=jarvis_engine --cov-report=html
```

**Smoke test structure** (`engine/tests/test_smoke.py`, 261 tests):
- Sections 1–13: Module imports, MemoryStore, ActivityFeed, CommandBus, API contracts, Config, Policy, TaskOrchestrator, Security, STT, WebFetch, Temporal, new modules
- Sections 14–22: MemoryEngine CRUD/FTS/tiers, KnowledgeGraph, Learning, IntentClassifier privacy routing, Proactive triggers, Security expanded (firewall/scanner/net policy, **encoded attack detection**), Voice pipeline, STT pipeline, Memory tiers
- Sections 23–25: Integration end-to-end, Performance thresholds, Property-based invariants (Hypothesis)

### 13.3 Maintenance Scripts

```powershell
# Backup current state
.\scripts\backup-state.ps1

# Evaluation gate
.\scripts\eval-gate.ps1

# Restore from backup (when needed)
.\scripts\restore-state.ps1 -ArchivePath C:\path\to\jarvis-state-YYYYMMDD-HHMMSS.zip

# Nightly maintenance (installs as scheduled task)
.\scripts\install-jarvis-nightly-maintenance.ps1 -Time "02:30"
.\scripts\run-jarvis-nightly-maintenance.ps1
```

---

## 14. Documentation Index

| Document | Location | Description |
|---|---|---|
| Architecture & quick-start | `CLAUDE.md` | Full architecture, gotchas, module ownership |
| Agent protocols | `AGENTS.md` | How bots work in this repo, quality gates, anti-patterns |
| Contributing guide | `CONTRIBUTING.md` | Branch naming, commit standards, test requirements, security policy |
| Project vision | `.planning/PROJECT.md` | Long-term direction |
| Requirements | `.planning/REQUIREMENTS.md` | Functional + non-functional specs |
| Roadmap | `.planning/ROADMAP.md` | Phase sequence and completion status |
| Current state | `.planning/STATE.md` | Active phase, blockers, last session notes |
| Mobile setup | `docs/MOBILE_SETUP.md` | API, signed requests, network config |
| Samsung S25 setup | `docs/SAMSUNG_GALAXY_S25_SETUP.md` | Device-specific setup guide |
| Voice security | `docs/VOICE_SECURITY_SETUP.md` | Biometric auth, voice guard setup |
| Spam defense | `docs/SAMSUNG_SPAM_CALL_DEFENSE.md` | Call screening and spam protection |
| Growth tracking | `docs/CAPABILITY_GROWTH_TRACKING.md` | How to measure and track intelligence growth |
| Memory core v2 | `docs/MEMORY_CORE_V2.md` | Deep-dive on memory architecture |
| Ops pack | `docs/JARVIS_OPS_PACK.md` | Multimodal task execution details |
| Fiction research | `docs/JARVIS_FICTION_RESEARCH.md` | MCU J.A.R.V.I.S. behavior mapping |

---

## 15. Project Planning

```
.planning/
  STATE.md              ← Read this first every session
  PROJECT.md            Long-term vision
  REQUIREMENTS.md       Functional / non-functional specs
  ROADMAP.md            v5.0 phase plan
  phases/               Per-phase implementation plans
  brain/                Memory data (gitignored)
  security/             Signing keys, tokens (gitignored)
  runtime/              Runtime state
  capability_history.jsonl  Growth eval history
```

**Resume prompt:** `"Open this repo, read .planning/STATE.md and CLAUDE.md, then continue v5.0 execution."`

---

*This repository is designed for long-term evolution. When making changes: read `STATE.md` first, run the quality gates, update `STATE.md` when done.*

