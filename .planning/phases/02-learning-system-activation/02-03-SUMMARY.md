# Plan 02-03 Summary: Relevance Scoring, Tier Management, Dashboard Learning Metrics

## Status: COMPLETE

## Requirements Covered
- LEARN-05: Relevance scoring in hybrid_search
- LEARN-06: Tier management in MemoryConsolidator
- LEARN-07: Knowledge snapshot in intelligence dashboard
- LEARN-08: Learning metrics in intelligence dashboard

## Changes Made

### Task 1: Relevance scoring + tier management
- **search.py**: Added frequency-based relevance boost to hybrid_search scoring loop
  - Formula: `boosted_score *= (0.9 + 0.2 * min(log1p(access_count)/log1p(10), 1.0))`
  - Range: 0.9x (zero access) to 1.1x (10+ accesses)
  - Avoids recency double-count (recency already handled by _recency_weight)
- **consolidator.py**: Added `_update_tiers()` method to MemoryConsolidator
  - Classifies records via `compute_relevance_score` + `classify_tier_by_relevance`
  - Runs after main consolidation loop (non-dry-run only)
  - Updates tier column in DB for changed records

### Task 2: Dashboard learning metrics
- **intelligence_dashboard.py**: Added `_safe_learning_metrics()` and `_safe_knowledge_snapshot()` helpers
  - `_safe_learning_metrics()`: Collects route_quality, preferences, peak_hours from trackers
  - `_safe_knowledge_snapshot()`: Calls `capture_knowledge_metrics()` for live KG data
  - Both return empty dicts on failure (graceful degradation)
  - `build_intelligence_dashboard()` now accepts optional tracker/kg/engine params
- **ops_handlers.py**: Updated `IntelligenceDashboardHandler` to accept and forward tracker params
- **app.py**: Moved dashboard handler registration after learning subsystem init
  - Success path: passes all trackers + kg + engine
  - Fallback path: registers without trackers

### Task 3: Tests
- **test_learning_memory_dashboard.py**: 10 new tests
  - 3 hybrid_search frequency boost tests (math, ordering, missing access_count)
  - 3 consolidator tier update tests (hot, archive, no change)
  - 4 dashboard tests (learning section, knowledge snapshot, no trackers, tracker errors)
- **test_ops_handlers.py**: Updated 2 existing assertions for new handler kwargs

## Test Results
- 10 new tests + 4233 existing = **4243 passed, 3 skipped, 0 failures**
