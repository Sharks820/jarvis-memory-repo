# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-22)

**Core value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.
**Current focus:** ALL PHASES COMPLETE

## Current Position

Phase: 9 of 9 (COMPLETE)
Plan: All plans executed
Status: All 9 phases complete. 471 tests passing. Full implementation delivered.
Last activity: 2026-02-23 -- Phase 9 complete (proactive intelligence, cost tracking, adversarial self-testing)

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 18
- Average duration: ~8min
- Total execution time: ~3 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3/3 | 65min | 22min |
| 02 | 2/2 | 14min | 7min |
| 03 | 2/2 | 10min | 5min |
| 04 | 2/2 | 11min | 5.5min |
| 05 | 2/2 | 16min | 8min |
| 06 | 2/2 | ~12min | ~6min |
| 07 | 2/2 | ~15min | ~7.5min |
| 08 | 1/1 | ~10min | ~10min |
| 09 | 2/2 | ~12min | ~6min |

**Recent Trend:**
- Last 5 plans: 07-02 (~7min), 08-01 (~10min), 09-01 (~6min), 09-02 (~6min)
- Trend: Stable ~6-10min/plan

*Updated after each plan completion*

## Test Suite Progress

| Phase | Tests Added | Running Total |
|-------|------------|---------------|
| 01 | 125 (baseline) | 125 |
| 02 | 45 | 170 |
| 03 | 25 | 195 |
| 04 | 44 | 239 |
| 05-01 | 13 | 252 |
| 05-02 | 29 | 281 |
| Bug fixes | 40 | 321 |
| 06 | 34 | 355 |
| 07-01 | 24 | 379 |
| 07-02 | 14 | 393 |
| 08 | 32 | 425 |
| 09 | 46 | 471 |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Phase 1 combines architecture decomposition (Command Bus) with memory revolution (SQLite + FTS5 + sqlite-vec) because they are tightly coupled -- the architecture creates the module structure into which the memory engine is built
- [Roadmap]: Using nomic-embed-text-v1.5 for embeddings (768-dim, 8192 token context) per stack research -- NOT all-MiniLM-L6-v2
- [Roadmap]: Changelog-based sync (Phase 8) instead of CRDTs -- simpler for two-device single-owner setup
- [Roadmap]: Knowledge graph uses NetworkX with SQLite persistence (not a separate graph DB)
- [01-01]: Fresh bus per _get_bus() call instead of singleton to respect test monkeypatching of repo_root
- [01-01]: Complex cmd_* functions use _impl callback pattern for handler delegation (avoids recursion)
- [01-01]: All command dataclasses are frozen; result dataclasses are mutable
- [01-01]: Handlers use lazy imports inside handle() to avoid circular dependencies
- [01-02]: Graceful degradation when sqlite-vec unavailable -- FTS5-only search fallback
- [01-02]: RRF k=60 with 168-hour recency decay half-life for hybrid search
- [01-03]: Per-chunk content_hash (SHA-256 of chunk text, not whole document) for UNIQUE constraint correctness
- [01-03]: Dual-path handler strategy: MemoryEngine when SQLite DB exists, adapter shim fallback
- [02-01]: Fact extraction is a side-effect of ingestion wrapped in try/except -- KG failures never block record storage
- [02-01]: KnowledgeGraph uses MemoryEngine._write_lock for thread-safe writes; reads are lock-free via WAL
- [02-02]: ContradictionManager stores resolution history in node's history JSON array, capped at 50 entries
- [02-02]: Knowledge handlers accept kg=None for graceful degradation when SQLite DB unavailable
- [03-01]: Provider resolution uses model prefix startswith() matching (claude-* -> Anthropic, else Ollama)
- [03-01]: CostTracker uses WAL mode + threading.Lock (same pattern as MemoryEngine)
- [03-02]: Privacy keywords always force local routing regardless of embedding similarity
- [03-02]: Gateway wiring in create_app() wrapped in try/except for graceful degradation
- [04-01]: Lazy import icalendar inside _parse_ics() with fallback to line-by-line parser
- [04-02]: LLM narrative via gateway.complete() with route_reason='daily_briefing_narrative'
- [05-01]: HarvestResult dataclass in harvester.py, imported by providers.py to avoid circular dependency
- [05-01]: GeminiProvider does NOT inherit HarvesterProvider (different SDK)
- [05-02]: BudgetManager uses same SQLite DB as CostTracker and MemoryEngine (WAL mode shared)
- [05-02]: Semantic dedup threshold at cosine > 0.92 with SHA-256 fallback
- [06-01]: TONE_PROFILES maps 9 branches to 4 tone profiles (professional, warm, light_humor, balanced)
- [06-01]: compose_persona_system_prompt builds LLM system prompt; compose_persona_reply stays for template acks
- [06-01]: PersonaComposeHandler routes through ModelGateway with route_reason="persona_reply"
- [06-02]: SpeechToText lazy-loads faster-whisper model; JARVIS_STT_MODEL env var overrides model_size
- [06-02]: sounddevice import is lazy (inside record_from_microphone function)
- [06-02]: VoiceListenCommand wired to bus, CLI voice-listen subcommand with optional --execute flag
- [07-01]: ConversationLearningEngine wraps EnrichedIngestPipeline with source="conversation:user" / "conversation:assistant"
- [07-01]: _is_knowledge_bearing() filters greetings <100 chars and messages <50 chars
- [07-01]: Temporal metadata: migrate_temporal_metadata() adds temporal_type + expires_at columns to kg_nodes
- [07-01]: Cross-branch edges created at confidence=0.4 via LIKE-based keyword overlap across branch prefixes
- [07-02]: Memory-recall golden tasks score: has_results(0.3) + branch_coverage(0.3) + keyword_coverage(0.4)
- [07-02]: capture_knowledge_metrics() uses direct SQL on engine._db + KG count methods
- [08-01]: SQLite triggers on records/kg_nodes/kg_edges with noise_field filtering for access_count/last_accessed
- [08-01]: SyncEngine field-level conflict resolution: DELETE wins over UPDATE, desktop wins ties
- [08-01]: Fernet encryption with PBKDF2HMAC 480K iterations, zlib compression before encryption
- [08-01]: Mobile API /sync/pull and /sync/push replace old /sync (301 redirect for backward compat)
- [09-01]: ProactiveEngine evaluates trigger rules with per-rule cooldowns (30-360 min)
- [09-01]: Notifier lazy-imports winotify with graceful logger.info fallback
- [09-01]: WakeWordDetector uses openwakeword+sounddevice, mic_lock for STT sharing
- [09-02]: CostTracker.local_vs_cloud_summary() groups by provider (ollama=local, else=cloud)
- [09-02]: AdversarialSelfTest regression detection: current < baseline * 0.8 flags regression
- [09-02]: Cost reduction trend: >2% change = improving/declining, else stable

### Pending Todos

None -- all phases complete.

### Blockers/Concerns

- [Phase 1]: sentence-transformers pulls PyTorch (~2GB). Use CPU-only torch to keep it ~200MB. First install will be large.

## Session Continuity

Last session: 2026-02-23
Stopped at: ALL PHASES COMPLETE. 471 tests passing. Full 9-phase implementation delivered.
Resume file: None
