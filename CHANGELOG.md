# Changelog

All notable changes to the Jarvis engine are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — PR #18 (branch: `copilot/organize-collaboration-strategy`)

### Added — Collaboration & Quality Infrastructure
- `CONTRIBUTING.md` — branch naming conventions, bot/human workflow, commit standards, PR process, security policy, test quality requirements, performance benchmark guidelines, type annotation rules
- `WORKFLOW.md` — branch lifecycle, parallel-bot isolation rules, merge strategy, hotfix process, release protocol
- `AGENTS.md` — quality gate requirements, prohibited patterns, module ownership, per-agent protocols
- `.github/CODEOWNERS` — all paths default to `@Sharks820`; security + gateway have explicit owner
- `.github/PULL_REQUEST_TEMPLATE.md` — standardized PR checklist for all contributors
- `.github/workflows/ci.yml` — 6-gate CI: lint → mypy → bandit → pip-audit → pytest+coverage → smoke; multi-Python matrix (3.11, 3.12)
- `.github/workflows/pr-review.yml` — automated ruff + bandit + mypy comment on every PR
- `.github/workflows/smoke-test.yml` — daily + on-push smoke validation for all 137 public modules
- `.github/workflows/benchmark.yml` — performance regression tracking with PR comment table; thresholds for all 7 critical subsystems
- `engine/tests/test_smoke.py` — expanded from 13 sections / 102 modules to **25 sections / 137 modules / ~130 tests**:
  - Section 14: MemoryEngine CRUD + FTS search + tier management (10 tests)
  - Section 15: KnowledgeGraph fact/edge/query/NetworkX (7 tests)
  - Section 16: Learning subsystem — feedback detection, preference tracking, conversation engine (8 tests)
  - Section 17: IntentClassifier — routing, privacy keyword enforcement (5 tests)
  - Section 18: Proactive triggers + alert queue enqueue/drain/dedup (6 tests)
  - Section 19: Security expanded — output scanner, containment, injection firewall, net policy (10 tests)
  - Section 20: Voice pipeline text processing — URL shortening, response escaping (6 tests)
  - Section 21: STT pipeline — TranscriptionResult fields, confidence constants, postprocessing (7 tests)
  - Section 22: Memory tier classification — HOT/WARM/COLD/ARCHIVE logic (5 tests)
  - Section 23: Integration smoke — memory→KG pipeline, bus→handler roundtrip, cross-subsystem (5 tests)
  - Section 24: Performance smoke — 6 timing assertions for critical paths (6 tests)
  - Section 25: Property-based smoke — 6 Hypothesis invariants covering arbitrary string inputs (6 tests)

---

## [2.0.0-bugfix] — 2026-03-06 (commit: `95e0940`)

### Fixed (22 bugs from Opus+Codex joint audit)

#### CRITICAL
- **Calendar triggers never fired** — field name mismatch: trigger code used `"start_time"` but snapshot data used `"time"` (`proactive/triggers.py`)

#### HIGH
- **CorrectionDetector rollback guard** — missing try/finally left DB in dirty state on exception (`learning/correction_detector.py`)
- **Containment deadlock** — `ContainmentEngine.contain()` acquired `_lock` twice in the same thread under some race conditions (`security/containment.py`)
- **Quarantine handler empty** — `defense_handlers.py` quarantine handler returned without actually quarantining the record
- **Auth login encapsulation** — raw credential comparison in `owner_session.py` replaced with PBKDF2 helper
- **Raw SQLite PRAGMAs** — `PRAGMA key=` statements exposed in logs; replaced with parameterized form
- **Voice pipeline asymmetric locking** — voice_pipeline acquired `_record_lock` on enter but released `_play_lock` on exit
- **DNS rebinding + global socket timeout** — `network_defense.py` missing global timeout; local DNS not validated against rebinding

#### MEDIUM (12)
- Heartbeat false positives on slow CI machines (threshold made configurable)
- Z-score bias in anomaly detector (used population std instead of sample std)
- Containment `recover()` scope leaked across threads
- Archive tier never set (threshold comparison was inverted)
- `contradictions.py` db_lock not released on early return
- Embedding computed outside write_lock window (race condition)
- `load_jsonl_tail` O(n) scan replaced with O(log n) binary search
- Widget HTTP fallback silently swallowed exceptions
- Alert list serialized as string instead of JSON array
- RDAP cache not LRU-bounded (unbounded growth)
- `brain_status` lock scope narrowed to reduce contention
- Desktop widget HTTP error handling

#### LOW (2)
- Dead `_confidence_retry` function removed from `stt.py`
- Float HMAC timestamp fixed: `Math.floor(Date.now() / 1000)` enforced in mobile clients

---

## [2.0.0-refactor] — 2026-03-06 (commits: `d8ef5a3` through `556104d`, 16 batches)

### Changed — Desloppify Sprint (batches 12–24)

Complete elimination of code health debt accumulated during the v2.0 feature sprint.

| Batch | Commit | Focus |
|-------|--------|-------|
| 12 | `d8ef5a3` | Error consistency, convention alignment across 19 files |
| 13 | `50bb13d` | Test health — 35 new assertions; `defense_commands` relocated to `commands/` |
| 14 | `f3c5c90` | Duplication removal, shared base classes — net −19 lines |
| 15 | `9e27dc0` | Error contracts, 3 new test files, encapsulation improvements |
| 16 | `3bd5cf4` | Log redaction for PII, conftest fixtures, 41 files touched |
| 17 | `2c2663f` | AI debt removal, property docstrings culled — net −32 lines |
| 18 | `6836601` | `error=` → `message=` uniform rename across 17 handler sites |
| extract | `c562aa8` | `main.py` −4,000 lines: extracted `_bus.py`, `daemon_loop.py`, `voice_pipeline.py` |
| 19 | `ba7b0e3` | Reverse import fix, deterministic test timing |
| argparse | `eaaf5a1` | 355-line `if/elif` dispatch → table-driven `set_defaults` — net −238 lines |
| 20 | `c69bfcd` | Re-export elimination, 20 files — net −236 lines |
| 21 | `3d99941` | `TYPE_CHECKING`, `ConversationState`, rate-limiter dedup |
| dispatch | `a94d627` | `_dispatch()` helper extracted from command bus |
| 22 | `27e772b` | SQL constants, narrow excepts, test fixture |
| 23 | `344a9c1` | 12 except-narrowings in 7 files |
| 24 | `556104d` | 16 except-narrowings + ~30 `type: ignore` removals |

**Net result:** 4,475 tests passing (up from 4,412 at batch 12 start), `main.py` reduced from ~7,000 to ~3,000 lines.

---

## [2.0.0] — 2026-03-05 (Merge PR #17, commit: `4d50751`)

### Added — v2.0 Android App (Phases 10–13)
- Android app: Jetpack Compose + Room/SQLCipher v11 (16 entities, 10 migrations)
- `JarvisService` foreground sync loop (2-minute context detection cycle)
- `CallScreeningService` + `NotificationListenerService` with `@EntryPoint` Hilt DI
- HMAC-SHA256 mobile API (`mobile_api.py`) on port 8787 with nonce replay protection
- Mobile sync: changelog-based encrypted payload (Fernet + PBKDF2HMAC)
- Accelerometer-based driving detection (no Google Play Services dependency)
- Nudge adaptive suppression: ≥80% ignore rate over 20 samples auto-suppresses
- Prescription, finance, and document management with master password gate
- `ops_autopilot.py`, `auto_ingest.py` extracted as standalone modules
- `sync/` subsystem: `changelog.py`, `transport.py`, `engine.py`, `auto_sync.py`

---

## [1.0.0] — 2026-03-01 (Phases 1–9)

### Added — v1.0 Desktop Engine

#### Core Infrastructure
- CQRS command bus with 70+ registered commands and handler dispatch table
- SQLite + FTS5 + sqlite-vec memory engine (`memory/engine.py`)
- Three-tier memory hierarchy: HOT / WARM / COLD / ARCHIVE (`memory/tiers.py`)
- WAL-mode SQLite with dual-lock pattern (write_lock + db_lock)

#### Intelligence Gateway
- `IntentClassifier` — embedding-based routing to Ollama / Anthropic / Groq / Gemini
- Privacy keyword detection forcing local-only routing
- Fallback chain: primary → secondary → local emergency
- Cost tracking + budget enforcement per provider

#### Knowledge Graph
- SQLite-persistent knowledge graph with NetworkX computation layer (`knowledge/graph.py`)
- Fact lock manager (prevents contradiction injection)
- Contradiction detection and quarantine (`knowledge/contradictions.py`)
- Entity resolution + canonical identity tracking (`knowledge/entity_resolver.py`)
- LLM-assisted fact extraction (`knowledge/llm_extractor.py`)

#### Learning System
- `ConversationLearningEngine` — knowledge extraction from every interaction
- `ResponseFeedbackTracker` — implicit satisfaction/correction signal detection
- `PreferenceTracker` — communication style, format, and time preference learning
- `UsagePatternTracker` — activity-hour and topic frequency analysis
- Cross-branch reasoning integration (`learning/cross_branch.py`)

#### Security System (17 modules)
- 3-layer prompt injection firewall (regex + structural + semantic)
- Output scanner: credential leakage, path disclosure, manipulation patterns
- Attack memory: perpetual learning from threat history
- 5-level autonomous containment (throttle → block → isolate → lockdown → kill)
- Forensic hash-chain logger with tamper detection
- Identity shield + session hijack detection
- Adaptive defense: auto-rule generation from attack patterns
- Memory provenance: trust levels + quarantine for untrusted facts
- Honeypot endpoints for attacker fingerprinting
- IP auto-escalation blocklist + RDAP intelligence

#### STT / Voice
- 4-tier STT fallback: Parakeet TDT (6.05% WER) → Deepgram → Groq Whisper → faster-whisper
- VAD-gated pipeline with configurable silence threshold
- STT post-processing: filler word removal, command normalization
- Voice auth (`voice_auth.py`) with speaker verification
- Wake word detection (`wakeword.py`) with configurable sensitivity
- TTS output via `edge-tts`

#### Proactive Engine
- Time-aware trigger rules (medication, calendar, tasks, news)
- Alert queue with mobile polling + deduplication window
- Notification priority levels: URGENT / IMPORTANT / ROUTINE / BACKGROUND
- Cost tracking per proactive operation
- Self-test framework for proactive rule validation

#### CLI (1,500+ lines, `main.py`)
- `jarvis-engine daemon-run` — production daemon with hot-reload
- `jarvis-engine ops-brief` — daily intelligence briefing
- `jarvis-engine voice-run` — interactive voice mode
- 60+ sub-commands for memory, knowledge, security, tasks, and diagnostics

---

*For the full git log see: `git log --oneline`*
