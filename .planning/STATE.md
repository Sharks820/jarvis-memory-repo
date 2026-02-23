# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-22)

**Core value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.
**Current focus:** Phase 4 -- Connectors and Daily Intelligence (COMPLETE)

## Current Position

Phase: 4 of 9 (Connectors and Daily Intelligence)
Plan: 2 of 2 in current phase (04-02 complete -- phase complete)
Status: Phase 04 complete -- Email triage + narrative daily briefing with LLM synthesis
Last activity: 2026-02-23 -- Completed email triage and narrative daily briefing

Progress: [█████▓░░░░] 50%

## Performance Metrics

**Velocity:**
- Total plans completed: 9
- Average duration: ~11min
- Total execution time: 1.67 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3/3 | 65min | 22min |
| 02 | 2/2 | 14min | 7min |
| 03 | 2/2 | 10min | 5min |
| 04 | 2/2 | 11min | 5.5min |

**Recent Trend:**
- Last 5 plans: 02-02 (8min), 03-01 (4min), 03-02 (6min), 04-01 (5min), 04-02 (6min)
- Trend: Accelerating

*Updated after each plan completion*

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
- [01-01]: cmd_serve_mobile kept inline for monkeypatch compatibility with existing tests
- [01-01]: All command dataclasses are frozen; result dataclasses are mutable
- [01-01]: Handlers use lazy imports inside handle() to avoid circular dependencies
- [01-02]: Graceful degradation when sqlite-vec unavailable -- FTS5-only search fallback
- [01-02]: FTS5 regular mode (not contentless) because contentless returns NULL for stored columns on SELECT
- [01-02]: RRF k=60 with 168-hour recency decay half-life for hybrid search
- [01-02]: Content-hash dedup is per-chunk, not per-document
- [01-03]: Per-chunk content_hash (SHA-256 of chunk text, not whole document) for UNIQUE constraint correctness
- [01-03]: 32 hex char record IDs to avoid collisions (Codex: 16 is too short)
- [01-03]: Dual-path handler strategy: MemoryEngine when SQLite DB exists, adapter shim fallback
- [01-03]: Resumable migration via checkpoint file every 50 records
- [01-03]: Credential redaction patterns in pipeline sanitize step
- [02-01]: NetworkX 3.4.2 used (latest available) -- research specified >=3.6.1 which doesn't exist; all required APIs available
- [02-01]: Fact extraction is a side-effect of ingestion wrapped in try/except -- KG failures never block record storage
- [02-01]: KnowledgeGraph uses MemoryEngine._write_lock for thread-safe writes; reads are lock-free via WAL
- [02-01]: Edge dedup relies on SQLite UNIQUE constraint (source_id, target_id, relation) with INSERT OR IGNORE
- [02-02]: Auto-lock triggers after add_fact outside write_lock (lock_fact acquires its own lock to avoid deadlock)
- [02-02]: ContradictionManager stores resolution history in node's history JSON array, capped at 50 entries
- [02-02]: accept_new resolution unlocks the node so new value needs re-confirmation to lock again
- [02-02]: Empty graph WL hash uses deterministic SHA-256 of "empty_knowledge_graph"
- [02-02]: Knowledge handlers accept kg=None for graceful degradation when SQLite DB unavailable
- [03-01]: GatewayResponse is non-frozen dataclass for mutability during fallback flow
- [03-01]: Provider resolution uses model prefix startswith() matching (claude-* -> Anthropic, else Ollama)
- [03-01]: Fallback model configurable via JARVIS_LOCAL_MODEL env var, defaults to qwen3:14b
- [03-01]: CostTracker uses WAL mode + threading.Lock (same pattern as MemoryEngine)
- [03-01]: SDK imports (anthropic, ollama) in models.py not __init__.py for lazy loading
- [03-02]: Privacy keywords always force local routing regardless of embedding similarity (privacy-safe default)
- [03-02]: Low-confidence queries (below 0.35 cosine threshold) default to local Ollama, not cloud
- [03-02]: IntentClassifier reads JARVIS_LOCAL_MODEL env var at classify() time for flexible local model
- [03-02]: RouteHandler dual-path: query-based via IntentClassifier when query provided, legacy risk/complexity via ModelRouter otherwise
- [03-02]: QueryHandler uses lazy imports for gateway.models to avoid import-time SDK dependency
- [03-02]: Gateway wiring in create_app() wrapped in try/except for graceful degradation
- [04-01]: Lazy import icalendar inside _parse_ics() with fallback to line-by-line parser for graceful degradation
- [04-01]: UTC-based date range for recurring event expansion (midnight-to-midnight UTC)
- [04-01]: Task source selection via JARVIS_TASK_SOURCE env var with default json, todoist, and google_tasks options
- [04-01]: Google Tasks returns empty list with TODO comment -- requires OAuth2, deferred to future phase
- [04-02]: Multi-signal triage checks subject keywords first, then sender patterns -- any match returns high
- [04-02]: Keep _email_importance() as backward-compatible wrapper delegating to _triage_email()
- [04-02]: Data summary condensed to ~1500 tokens with truncation (10 events, 10 tasks, 10 emails, 8 meds, 8 bills)
- [04-02]: LLM narrative via gateway.complete() with route_reason='daily_briefing_narrative' for cost tracking
- [04-02]: OpsBriefHandler gateway parameter defaults to None for backward compatibility

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: sentence-transformers pulls PyTorch (~2GB). Use CPU-only torch to keep it ~200MB. First install will be large.
- [Phase 1]: 14 requirements in one phase is heavy. Plan decomposition (3 plans) must be carefully scoped.

## Session Continuity

Last session: 2026-02-23
Stopped at: Completed 04-02-PLAN.md (Email Triage & Narrative Daily Briefing) -- Phase 04 complete
Resume file: None
