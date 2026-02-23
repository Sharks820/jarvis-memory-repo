---
phase: 01-memory-revolution-and-architecture
plan: 03
subsystem: memory-pipeline
tags: [ingestion-pipeline, chunking, embedding, branch-classification, cosine-similarity, migration, jsonl-to-sqlite, dual-path-handlers, sha256-dedup]

# Dependency graph
requires:
  - phase: 01-01
    provides: Command Bus architecture, EmbeddingService, adapter-shim handlers, memory package structure
  - phase: 01-02
    provides: MemoryEngine with SQLite + FTS5 + sqlite-vec, hybrid_search, TierManager, content-hash dedup
provides:
  - EnrichedIngestPipeline with sanitize, dedup, chunk, embed, classify, store
  - BranchClassifier using embedding cosine similarity against 9 branch centroids
  - JSONL-to-SQLite migration script with count verification and resumable checkpoints
  - Dual-path memory handlers (MemoryEngine when SQLite exists, adapter shim fallback)
  - migrate-memory CLI command and MigrateMemoryCommand
affects: [phase-2, phase-3, phase-7, phase-8]

# Tech tracking
tech-stack:
  added: []
  patterns: [enriched-ingestion-pipeline, semantic-branch-classification, per-chunk-content-hash, dual-path-handler, resumable-migration-checkpoint, credential-redaction]

key-files:
  created:
    - engine/src/jarvis_engine/memory/ingest.py
    - engine/src/jarvis_engine/memory/classify.py
    - engine/src/jarvis_engine/memory/migration.py
    - engine/tests/test_memory_ingest.py
    - engine/tests/test_memory_migration.py
  modified:
    - engine/src/jarvis_engine/memory/engine.py
    - engine/src/jarvis_engine/memory/__init__.py
    - engine/src/jarvis_engine/handlers/memory_handlers.py
    - engine/src/jarvis_engine/handlers/system_handlers.py
    - engine/src/jarvis_engine/handlers/__init__.py
    - engine/src/jarvis_engine/commands/system_commands.py
    - engine/src/jarvis_engine/commands/__init__.py
    - engine/src/jarvis_engine/app.py
    - engine/src/jarvis_engine/main.py

key-decisions:
  - "Per-chunk content_hash (SHA-256 of chunk text, not whole document) to ensure all chunks insert via UNIQUE constraint"
  - "32 hex char record IDs (Codex finding: 16 is too short for collision avoidance)"
  - "Dual-path handler strategy: MemoryEngine injected when SQLite DB exists, adapter shim fallback preserves all existing tests"
  - "Credential redaction patterns in pipeline sanitize step (password, token, api_key, secret, signing_key)"
  - "Resumable migration via checkpoint file every 50 records for crash recovery"
  - "BranchClassifier uses lazy-computed centroids -- only computed on first classify() call"

patterns-established:
  - "Enriched pipeline: sanitize -> dedup -> chunk -> embed -> classify -> store"
  - "Per-chunk content hash: CRITICAL for chunked content dedup correctness"
  - "Dual-path handler: constructor accepts optional engine/pipeline, falls back to adapter shim if None"
  - "DI composition root detects SQLite DB and injects MemoryEngine into handlers"
  - "Mock embedding service: deterministic sin-based vectors seeded from text hash for testing"

requirements-completed: [MEM-04, MEM-06, MEM-07, MEM-08]

# Metrics
duration: 10min
completed: 2026-02-23
---

# Phase 01 Plan 03: Enriched Ingestion Pipeline and Migration Summary

**Enriched ingestion pipeline with SHA-256 chunked dedup, semantic branch classification via cosine similarity, and JSONL-to-SQLite migration with count verification -- 165 tests passing**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-02-23T01:43:49Z
- **Completed:** 2026-02-23T01:53:22Z
- **Tasks:** 2
- **Files modified:** 14

## Accomplishments
- Built EnrichedIngestPipeline: sanitize (credential redaction) -> SHA-256 dedup -> chunk (>2000 chars at sentence boundaries) -> embed -> classify branch semantically -> store in SQLite
- Created BranchClassifier using embedding cosine similarity against 9 branch centroids (ops, coding, health, finance, security, learning, family, communications, gaming) with 0.3 threshold
- Built JSONL-to-SQLite migration script with count verification (inserted + skipped + errors == source_count), resumable via checkpoint files
- Upgraded memory handlers from pure adapter shims to dual-path: MemoryEngine when SQLite DB exists, adapter shim fallback when not
- Added migrate-memory CLI command, MigrateMemoryCommand + MigrateMemoryHandler
- All 165 tests pass (145 existing + 13 ingestion + 7 migration, 1 skipped)

## Task Commits

Each task was committed atomically:

1. **Task 1: Enriched ingestion pipeline with chunking, embedding, and semantic classification** - `1a75db5` (feat)
2. **Task 2: JSONL-to-SQLite migration, migrate-memory CLI, dual-path handlers** - `d13be52` (feat)

## Files Created/Modified
- `engine/src/jarvis_engine/memory/classify.py` - BranchClassifier with 9 branch centroids and cosine similarity classification
- `engine/src/jarvis_engine/memory/ingest.py` - EnrichedIngestPipeline with sanitize, dedup, chunk, embed, classify, store
- `engine/src/jarvis_engine/memory/migration.py` - migrate_brain_records, migrate_facts, migrate_events, run_full_migration with resumable checkpoints
- `engine/src/jarvis_engine/memory/engine.py` - Added get_record_by_hash() method
- `engine/src/jarvis_engine/memory/__init__.py` - Added BranchClassifier, BRANCH_DESCRIPTIONS, EnrichedIngestPipeline exports
- `engine/src/jarvis_engine/handlers/memory_handlers.py` - Dual-path handlers: MemoryEngine or adapter shim
- `engine/src/jarvis_engine/handlers/system_handlers.py` - Added MigrateMemoryHandler
- `engine/src/jarvis_engine/handlers/__init__.py` - Added MigrateMemoryHandler export
- `engine/src/jarvis_engine/commands/system_commands.py` - Added MigrateMemoryCommand/Result
- `engine/src/jarvis_engine/commands/__init__.py` - Added MigrateMemoryCommand export
- `engine/src/jarvis_engine/app.py` - DI composition root detects SQLite DB and injects MemoryEngine into handlers
- `engine/src/jarvis_engine/main.py` - Added cmd_migrate_memory and migrate-memory subparser
- `engine/tests/test_memory_ingest.py` - 13 tests: pipeline ingestion, chunking, dedup, classification, sanitization, classifier
- `engine/tests/test_memory_migration.py` - 7 tests: count verification, malformed JSON, field preservation, embedding/classification, facts, full migration

## Decisions Made
- **Per-chunk content_hash:** SHA-256 of each chunk's text (not the whole document). If you hash the full document before chunking, all chunks share the same hash and the UNIQUE constraint prevents all but the first from inserting.
- **32 hex char record IDs:** Codex review found 16 hex chars is too short to avoid collisions at scale. Using 32 chars from SHA-256 truncation.
- **Dual-path handler strategy:** Handlers accept optional MemoryEngine/pipeline in constructor. If None (no SQLite DB), they fall back to the existing adapter shim behavior. This ensures ALL 145 existing tests pass without modification.
- **Credential redaction patterns:** Pipeline sanitize step redacts password, passwd, pwd, token, api_key, secret, signing_key, and bearer patterns before storage.
- **Resumable migration:** Checkpoint file saved every 50 records at `{db_path}.migration_checkpoint.json`. On restart, migration resumes from last checkpoint instead of re-processing.
- **Lazy centroids:** BranchClassifier computes branch centroids on first `classify()` call, not on construction, avoiding unnecessary embedding calls.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 01 (Memory Revolution and Architecture) is complete with all 3 plans executed
- All memory operations can now flow through SQLite with semantic search after running `migrate-memory`
- The enriched pipeline ensures all future records are chunked, embedded, classified, and searchable
- Dual-path handlers mean existing functionality is preserved until migration is run
- Ready for Phase 2: Knowledge Graph and deeper memory intelligence

## Self-Check: PASSED

- All 14 created/modified files exist on disk
- Commit 1a75db5 (Task 1) found in git log
- Commit d13be52 (Task 2) found in git log
- All 165 tests pass (1 skipped)
- EnrichedIngestPipeline imports successfully
- BranchClassifier with 9 branches imports successfully
- run_full_migration imports successfully

---
*Phase: 01-memory-revolution-and-architecture*
*Completed: 2026-02-23*
