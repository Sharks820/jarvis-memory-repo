---
phase: 01-memory-revolution-and-architecture
plan: 02
subsystem: database
tags: [sqlite, fts5, sqlite-vec, hybrid-search, rrf, tiered-storage, wal-mode, semantic-search]

# Dependency graph
requires:
  - phase: 01-01
    provides: Command Bus architecture, EmbeddingService, memory package structure
provides:
  - MemoryEngine class with SQLite + FTS5 + sqlite-vec CRUD
  - Hybrid search with Reciprocal Rank Fusion (RRF) + recency decay
  - TierManager with hot/warm/cold classification
  - Content-hash deduplication via UNIQUE constraint
  - WAL mode with write-lock serialization for concurrent access
affects: [01-03-PLAN, phase-2, phase-3, phase-7, phase-8]

# Tech tracking
tech-stack:
  added: [sentence-transformers, sqlite-vec]
  patterns: [wal-mode-concurrent-access, fts5-keyword-search, sqlite-vec-knn-search, reciprocal-rank-fusion, exponential-recency-decay, graceful-degradation-pattern, write-lock-serialization]

key-files:
  created:
    - engine/src/jarvis_engine/memory/engine.py
    - engine/src/jarvis_engine/memory/search.py
    - engine/src/jarvis_engine/memory/tiers.py
    - engine/tests/test_memory_engine.py
  modified:
    - engine/src/jarvis_engine/memory/__init__.py
    - engine/pyproject.toml

key-decisions:
  - "Changed FTS5 from contentless mode (content='') to regular mode because contentless returns NULL for stored columns"
  - "sqlite-vec graceful degradation: engine sets _vec_available=False and falls back to FTS5-only search when extension fails to load"
  - "Hybrid search uses RRF with k=60 constant and exponential recency decay with 168-hour (7-day) half-life"
  - "TierManager thresholds: HOT=48h, WARM_MAX=90d, HIGH_CONFIDENCE=0.85, HIGH_ACCESS=3"
  - "Vec0 virtual table created separately from executescript to handle extension errors gracefully"

patterns-established:
  - "Graceful degradation: try/except on extension load, set flag, skip in search -- never crash"
  - "Write-lock serialization: all write operations under threading.Lock for concurrent access safety"
  - "Reciprocal Rank Fusion: 1/(k+rank+1) score combination from multiple retrieval sources"
  - "Tier classification: recency > access_count > confidence > age for tier assignment"
  - "Mock embedding pattern: deterministic sin-based vectors for testing without loading real model"

requirements-completed: [ARCH-05, MEM-01, MEM-02, MEM-03, MEM-05]

# Metrics
duration: 10min
completed: 2026-02-23
---

# Phase 01 Plan 02: SQLite Memory Engine Summary

**SQLite + FTS5 + sqlite-vec memory engine with hybrid RRF search, three-tier storage, WAL concurrent access, and 15 new tests (145 total passing)**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-02-23T01:26:42Z
- **Completed:** 2026-02-23T01:36:21Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Built MemoryEngine class with SQLite WAL mode, FTS5 full-text search, and sqlite-vec KNN vector search with graceful degradation
- Implemented hybrid search combining FTS5 keyword + sqlite-vec semantic + exponential recency decay via Reciprocal Rank Fusion (RRF)
- Created TierManager with hot/warm/cold classification based on recency (48h), access count (>3), confidence (>=0.85), and age (>90d)
- Added 15 comprehensive tests covering CRUD, FTS5, vec search, tier classification, hybrid search ranking, and deduplication
- All 145 tests pass (130 existing + 15 new, 1 skipped)

## Task Commits

Each task was committed atomically:

1. **Task 1: MemoryEngine + TierManager + dependencies** - `b91dfe1` (feat)
2. **Task 2: Hybrid search + comprehensive tests** - `b6b9c76` + `0923fa4` (feat)

## Files Created/Modified
- `engine/src/jarvis_engine/memory/engine.py` - MemoryEngine class with SQLite + FTS5 + sqlite-vec CRUD, WAL mode, write-lock
- `engine/src/jarvis_engine/memory/search.py` - hybrid_search() with RRF + recency decay, _recency_weight() helper
- `engine/src/jarvis_engine/memory/tiers.py` - TierManager with Tier enum (HOT/WARM/COLD) and run_tier_maintenance()
- `engine/src/jarvis_engine/memory/__init__.py` - Updated exports: MemoryEngine, TierManager, Tier, EmbeddingService, hybrid_search
- `engine/pyproject.toml` - Added sentence-transformers>=5.0.0 and sqlite-vec>=0.1.6 dependencies
- `engine/tests/test_memory_engine.py` - 15 tests: 8 MemoryEngine, 4 TierManager, 3 HybridSearch

## Decisions Made
- **FTS5 regular mode instead of contentless:** Plan specified `content='', contentless_delete=1` but FTS5 contentless mode returns NULL for stored columns on SELECT. Changed to regular FTS5 table that stores its own copy of record_id and summary, enabling record_id retrieval from search results.
- **Graceful degradation pattern:** sqlite-vec load failure sets `_vec_available = False` and logs a warning instead of crashing. All vec search methods check this flag and return empty results when unavailable.
- **Separate vec0 creation:** Vec0 virtual table created via cursor.execute() not executescript() because sqlite-vec extension errors need to be caught individually without aborting the entire schema creation.
- **RRF constant k=60:** Standard value from the original RRF paper. Higher values give more equal weighting between retrieval sources.
- **Recency half-life 168 hours (7 days):** Exponential decay `exp(-age_hours / 168)` gives recent records a meaningful boost that fades naturally over a week.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FTS5 contentless mode returns NULL columns**
- **Found during:** Task 2 (test_search_fts_returns_matching_records)
- **Issue:** FTS5 with `content='', contentless_delete=1` does not store column values -- SELECT returns NULL for record_id
- **Fix:** Changed to regular FTS5 table `USING fts5(record_id, summary)` without content='' parameter
- **Files modified:** engine/src/jarvis_engine/memory/engine.py
- **Verification:** test_search_fts_returns_matching_records passes with correct record_id values
- **Committed in:** 0923fa4

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Essential fix for FTS5 search to work correctly. No scope creep.

## Issues Encountered
- FTS5 contentless mode (`content=''`) is a known SQLite limitation -- it only stores the index, not the column values. The plan specified contentless mode for space efficiency, but it prevents record_id retrieval. Regular FTS5 mode uses slightly more disk space but is functionally correct.
- Hybrid search test required careful embedding seed selection to ensure "both" record (matching keyword + embedding) gets higher RRF score than single-signal matches.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- MemoryEngine is ready for Plan 03 (ingestion pipeline) to insert records with embeddings
- hybrid_search is ready for query-time retrieval in the brain context pipeline
- TierManager ready for nightly maintenance runs via daemon
- EmbeddingService (from Plan 01) can now be wired into MemoryEngine constructor

## Self-Check: PASSED

- All 6 created/modified files exist on disk
- Commit b91dfe1 (Task 1) found in git log
- Commit b6b9c76 (Task 2a) found in git log
- Commit 0923fa4 (Task 2b - FTS5 fix) found in git log
- All 145 tests pass (130 existing + 15 new, 1 skipped)
- MemoryEngine imports and creates DB with 0 records
- WAL journal mode verified active
- sqlite-vec extension loads successfully

---
*Phase: 01-memory-revolution-and-architecture*
*Completed: 2026-02-23*
