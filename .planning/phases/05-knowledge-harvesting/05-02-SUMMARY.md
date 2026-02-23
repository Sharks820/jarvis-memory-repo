---
phase: 05-knowledge-harvesting
plan: 02
subsystem: harvesting
tags: [session-ingestors, budget-manager, semantic-dedup, command-bus, sqlite, jsonl]

requires:
  - phase: 05-knowledge-harvesting/01
    provides: "HarvesterProvider base, MiniMax/Kimi/Gemini providers, KnowledgeHarvester orchestrator"
  - phase: 01-memory-revolution-and-architecture
    provides: "Command Bus, MemoryEngine, EnrichedIngestPipeline, EmbeddingService"
  - phase: 03-intelligence-routing
    provides: "CostTracker for shared SQLite cost database"
provides:
  - "ClaudeCodeIngestor and CodexIngestor for parsing local session JSONL files"
  - "BudgetManager with SQLite-backed per-provider daily/monthly spend and request limits"
  - "Semantic dedup (cosine > 0.92) and SHA-256 cross-provider exact dedup in harvester"
  - "HarvestTopicCommand, IngestSessionCommand, HarvestBudgetCommand Command Bus commands"
  - "harvest, ingest-session, harvest-budget CLI subcommands"
affects: [phase-06, phase-08, daily-briefing, daemon-loop]

tech-stack:
  added: []
  patterns:
    - "Session JSONL parsing with graceful error handling (FileNotFoundError, JSONDecodeError, PermissionError)"
    - "BudgetManager shares SQLite DB with CostTracker (WAL mode + threading.Lock)"
    - "Cross-provider semantic dedup via cosine similarity threshold with SHA-256 fallback"
    - "Lazy imports in handlers for session ingestors to avoid import-time dependencies"

key-files:
  created:
    - engine/src/jarvis_engine/harvesting/session_ingestors.py
    - engine/src/jarvis_engine/harvesting/budget.py
    - engine/src/jarvis_engine/commands/harvest_commands.py
    - engine/src/jarvis_engine/handlers/harvest_handlers.py
    - engine/tests/test_harvesting_sessions.py
    - engine/tests/test_harvesting_budget.py
  modified:
    - engine/src/jarvis_engine/harvesting/harvester.py
    - engine/src/jarvis_engine/harvesting/__init__.py
    - engine/src/jarvis_engine/commands/__init__.py
    - engine/src/jarvis_engine/handlers/__init__.py
    - engine/src/jarvis_engine/app.py
    - engine/src/jarvis_engine/main.py

key-decisions:
  - "BudgetManager uses same SQLite DB as CostTracker and MemoryEngine (WAL mode shared)"
  - "Semantic dedup threshold at cosine > 0.92 with SHA-256 fallback when no embed service"
  - "Session ingestors filter assistant messages >100 chars (short texts are tool outputs)"
  - "CLAUDE_CONFIG_DIR env var as override for Claude Code session base path"
  - "CODEX_HOME env var for Codex session base path with default ~/.codex"
  - "Default budgets: minimax/kimi $1/day $10/month, gemini 50 req/day, kimi_nvidia 100 req/day"
  - "Harvesting wired in app.py with try/except for graceful degradation (same as gateway)"
  - "Patch target for handler tests uses source module path due to lazy imports"

patterns-established:
  - "Budget enforcement pattern: can_spend() check before query, record_spend() after"
  - "Cross-provider dedup via seen_hashes set tracking SHA-256 of each provider's text"

requirements-completed: [HARV-03, HARV-04, HARV-06, HARV-07]

duration: 10min
completed: 2026-02-23
---

# Phase 5 Plan 2: Knowledge Harvesting Wave 2 Summary

**Session ingestors for Claude Code/Codex JSONL files, SQLite budget manager with per-provider limits, semantic dedup (cosine > 0.92), and full Command Bus + CLI wiring**

## Performance

- **Duration:** 10 min
- **Started:** 2026-02-23T06:54:29Z
- **Completed:** 2026-02-23T07:04:45Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments
- ClaudeCodeIngestor and CodexIngestor parse session JSONL files with graceful error handling for missing dirs, malformed JSON, and permission errors
- BudgetManager enforces per-provider daily/monthly USD cost and request count limits via SQLite (shared DB with CostTracker)
- Semantic near-duplicate detection (cosine > 0.92) prevents multi-provider knowledge pollution, with SHA-256 fallback
- harvest, ingest-session, and harvest-budget CLI commands wired through Command Bus with typed commands/results
- 29 new tests (12 session + 17 budget/handler), 321 total passing with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Session ingestors, budget manager, and semantic dedup** - `ef0e5c6` (feat)
2. **Task 2: Command Bus wiring, CLI commands, and tests** - `b37dc19` (feat)

## Files Created/Modified
- `engine/src/jarvis_engine/harvesting/session_ingestors.py` - ClaudeCodeIngestor and CodexIngestor for JSONL session file parsing
- `engine/src/jarvis_engine/harvesting/budget.py` - BudgetManager with SQLite-backed per-provider daily/monthly spend limits
- `engine/src/jarvis_engine/harvesting/harvester.py` - Added budget checks, spend recording, and semantic dedup
- `engine/src/jarvis_engine/harvesting/__init__.py` - Added new exports (ClaudeCodeIngestor, CodexIngestor, BudgetManager)
- `engine/src/jarvis_engine/commands/harvest_commands.py` - HarvestTopicCommand, IngestSessionCommand, HarvestBudgetCommand
- `engine/src/jarvis_engine/handlers/harvest_handlers.py` - HarvestHandler, IngestSessionHandler, HarvestBudgetHandler
- `engine/src/jarvis_engine/commands/__init__.py` - Added harvest command exports
- `engine/src/jarvis_engine/handlers/__init__.py` - Added harvest handler exports
- `engine/src/jarvis_engine/app.py` - Wired harvesting subsystem into DI composition root
- `engine/src/jarvis_engine/main.py` - Added harvest, ingest-session, harvest-budget CLI subcommands
- `engine/tests/test_harvesting_sessions.py` - 12 tests for session ingestors, handler, and semantic dedup
- `engine/tests/test_harvesting_budget.py` - 17 tests for budget manager limits, spend tracking, and handlers

## Decisions Made
- BudgetManager shares the same SQLite database as CostTracker and MemoryEngine (WAL mode + threading.Lock pattern)
- Semantic dedup uses cosine > 0.92 threshold via embedding similarity, falls back to SHA-256 exact match when no embed service
- Session ingestors filter for assistant messages with >100 chars (shorter texts are tool outputs, not knowledge)
- CLAUDE_CONFIG_DIR env var overrides the default ~/.claude session base path
- Default budgets: minimax/kimi at $1/day and $10/month, gemini at 50 requests/day (free tier), kimi_nvidia at 100 requests/day (free tier)
- Harvesting wiring in app.py uses try/except for graceful degradation (same pattern as gateway)
- Handler test patching targets source module path because session ingestors use lazy imports

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test mock patch target for IngestSessionHandler**
- **Found during:** Task 2 (test writing)
- **Issue:** Patching `jarvis_engine.handlers.harvest_handlers.ClaudeCodeIngestor` failed because the handler uses lazy imports inside `handle()`, not module-level imports
- **Fix:** Changed patch target to `jarvis_engine.harvesting.session_ingestors.ClaudeCodeIngestor` (the source module)
- **Files modified:** engine/tests/test_harvesting_sessions.py
- **Verification:** Test passes after fix
- **Committed in:** b37dc19 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Minor test fix, no scope creep.

## Issues Encountered
None beyond the test patch target fix documented above.

## User Setup Required
None - no external service configuration required. Harvesting providers read API keys from environment variables (set up in Wave 1).

## Next Phase Readiness
- Knowledge harvesting pipeline is complete: providers, session ingestors, budget enforcement, dedup, Command Bus, and CLI
- Phase 5 fully done -- ready for Phase 6 (next in roadmap)
- Budget limits can be tuned via `harvest-budget --action set` CLI command
- Session ingestion can be triggered via `ingest-session --source claude` or `--source codex`

## Self-Check: PASSED

- All 7 created/modified files verified present on disk
- Commit ef0e5c6 (Task 1) verified in git log
- Commit b37dc19 (Task 2) verified in git log
- 321 tests passing (29 new, 0 regressions)
- CLI subcommands harvest, ingest-session, harvest-budget all show --help output

---
*Phase: 05-knowledge-harvesting*
*Completed: 2026-02-23*
