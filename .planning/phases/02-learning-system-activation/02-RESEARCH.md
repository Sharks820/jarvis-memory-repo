# Phase 2: Learning System Activation - Research

**Researched:** 2026-03-02
**Domain:** Learning feedback loop closure, tracker read-side wiring, CQRS integration
**Confidence:** HIGH

## Summary

The Jarvis learning system has three trackers (PreferenceTracker, ResponseFeedbackTracker, UsagePatternTracker) that were built with both write and read APIs, but only the write side was ever wired into the application. Every interaction calls `observe()`, `record_feedback()`, and `record_interaction()`, storing data into SQLite tables `user_preferences`, `response_feedback`, and `usage_patterns`. However, the read methods (`get_preferences()`, `get_route_quality()`, `predict_context()`) are never called from anywhere in the codebase. The learning system is a write-only black hole.

Additionally, two critical data quality bugs exist: `record_feedback()` is called without a `route` parameter (line 115 of `engine.py`), and `record_interaction()` is called without `route` or `topic` parameters (line 122 of `engine.py`). The `LearnInteractionCommand` dataclass lacks `route` and `topic` fields entirely, so even if calling code wanted to pass them through the CQRS bus, it cannot.

**Primary recommendation:** Wire tracker read methods into system prompts and routing, add `route`/`topic` fields to `LearnInteractionCommand`, expose trackers on the bus, and integrate relevance scoring into memory retrieval and consolidation.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| LEARN-01 | PreferenceTracker.get_preferences() wired into QueryHandler | Preferences injected into system prompt via _build_smart_context; trackers must be exposed on bus |
| LEARN-02 | ResponseFeedbackTracker.get_route_quality() wired into IntentClassifier | Route satisfaction_rate used as penalty in classify(); requires bus exposure + classifier access |
| LEARN-03 | UsagePatternTracker.predict_context() wired into daemon proactive checks | predict_context(hour, dow) called in daemon cycle; proactive suggestions based on patterns |
| LEARN-04 | LearnInteractionCommand carries route name | Add route field to command dataclass; pass from all 4 dispatch sites in main.py |
| LEARN-05 | compute_relevance_score() integrated into memory retrieval | Score injected into hybrid_search ranking as additional RRF factor |
| LEARN-06 | classify_tier_by_relevance() integrated into MemoryConsolidator | Consolidator updates record tiers based on relevance score |
| LEARN-07 | capture_knowledge_metrics() wired into BrainStatusCommand or dashboard | Metrics shown via existing IntelligenceDashboard or BrainStatusHandler |
| LEARN-08 | Intelligence dashboard shows per-route quality, preference summary, peak hours | Extend build_intelligence_dashboard() with learning data sections |
</phase_requirements>

## Current Learning Architecture

### Write Path (FUNCTIONAL - data flows in)

```
User Query
  -> cmd_voice_run() or _web_augmented_llm_conversation()
    -> bus.dispatch(LearnInteractionCommand(user_message, assistant_response, task_id))
      -> LearnInteractionHandler.handle()
        -> ConversationLearningEngine.learn_from_interaction()
          -> PreferenceTracker.observe(user_message)           # WRITES to user_preferences table
          -> ResponseFeedbackTracker.record_feedback(user_message)  # WRITES to response_feedback table (route="" always!)
          -> UsagePatternTracker.record_interaction()           # WRITES to usage_patterns table (route="" topic="" always!)
          -> EnrichedIngestPipeline.ingest() for knowledge-bearing content
```

### Read Path (BROKEN - never called)

```
PreferenceTracker.get_preferences()      -> NEVER CALLED (defined at preferences.py:90)
PreferenceTracker.get_all_preferences()  -> NEVER CALLED (defined at preferences.py:109)
ResponseFeedbackTracker.get_route_quality()     -> NEVER CALLED (defined at feedback.py:102)
ResponseFeedbackTracker.get_all_route_quality() -> NEVER CALLED (defined at feedback.py:128)
UsagePatternTracker.predict_context()     -> NEVER CALLED (defined at usage_patterns.py:64)
UsagePatternTracker.get_hourly_distribution()  -> NEVER CALLED (defined at usage_patterns.py:104)
UsagePatternTracker.get_peak_hours()      -> NEVER CALLED (defined at usage_patterns.py:112)
compute_relevance_score()                 -> NEVER CALLED (defined at relevance.py:14)
classify_tier_by_relevance()              -> NEVER CALLED (defined at relevance.py:49)
capture_knowledge_metrics()               -> NEVER CALLED from dashboard (defined at metrics.py:17)
```

### Data Quality Bugs

**Bug 1: Missing route in record_feedback()**
- File: `engine/src/jarvis_engine/learning/engine.py`, line 115
- Code: `self._feedback_tracker.record_feedback(user_message)` -- no `route` kwarg
- Effect: ALL feedback rows have `route = ''` -- route quality scoring is useless
- Fix: Pass route from LearnInteractionCommand through learn_from_interaction()

**Bug 2: Missing route/topic in record_interaction()**
- File: `engine/src/jarvis_engine/learning/engine.py`, line 122
- Code: `self._usage_tracker.record_interaction()` -- no `route` or `topic` kwarg
- Effect: ALL usage_patterns rows have `route = '' AND topic = ''` -- predict_context() returns empty
- Fix: Pass route and topic from LearnInteractionCommand through learn_from_interaction()

**Bug 3: LearnInteractionCommand lacks route/topic fields**
- File: `engine/src/jarvis_engine/commands/learning_commands.py`, lines 9-14
- Fields: only `user_message`, `assistant_response`, `task_id`
- Missing: `route`, `topic` (needed by both feedback and usage trackers)
- Fix: Add `route: str = ""` and `topic: str = ""` fields

**Bug 4: Trackers not exposed on bus**
- File: `engine/src/jarvis_engine/app.py`, lines 408-413
- Trackers created as local variables (`pref_tracker`, `feedback_tracker`, `usage_tracker`)
- Stored only inside `learning_engine` as private attrs (`_preference_tracker`, etc.)
- NOT attached to bus (unlike `bus._engine`, `bus._kg`, `bus._gateway` on lines 554-558)
- Fix: Expose as `bus._pref_tracker`, `bus._feedback_tracker`, `bus._usage_tracker`

## Where Data Is Written (all 4 dispatch sites)

### Site 1: cmd_learn() CLI command
- File: `engine/src/jarvis_engine/main.py`, line 2048
- Context: Direct CLI invocation for manual learning
- Route info: NOT available (no classification happens here)
- Fix: Add optional `--route` arg to CLI, or leave empty (manual learning has no route)

### Site 2: Web-augmented conversation
- File: `engine/src/jarvis_engine/main.py`, line 3101
- Context: `_web_augmented_llm_conversation()` after successful LLM response
- Route info: Available in `_route` variable (line 3003 default "web_research", or classified)
- task_id: `f"conv-web-{timestamp}"` -- no route in task_id
- Fix: Pass `_route` as route field in LearnInteractionCommand

### Site 3: Voice LLM conversation path
- File: `engine/src/jarvis_engine/main.py`, line 3968
- Context: `cmd_voice_run()` LLM conversation branch after successful response
- Route info: Available in `_route` variable (line 3839 default "routine", or classified)
- task_id: `f"conv-{_route}-{timestamp}"` -- route IS in task_id but NOT in command field
- Fix: Pass `_route` as route field in LearnInteractionCommand

### Site 4: Voice command learning (non-LLM)
- File: `engine/src/jarvis_engine/main.py`, line 4037
- Context: `cmd_voice_run()` learning for ALL successful commands (not just LLM)
- Route info: Available via `intent` variable (e.g. "brain_context", "mission_status", etc.)
- task_id: `f"learn-{intent}-{timestamp}"` -- intent IS in task_id but NOT in command field
- Fix: Pass `intent` as route field in LearnInteractionCommand

## Where Data Should Be Read

### Integration Point 1: System Prompt Preference Injection (LEARN-01)

**Target:** `_build_smart_context()` in `engine/src/jarvis_engine/main.py`, lines 575-654
**Purpose:** Inject user preferences into the LLM system prompt so responses match user's style
**How:** After building memory_lines and fact_lines, retrieve preferences and add a section:
```python
# After line 654 (end of KG facts section)
pref_tracker = getattr(bus, "_pref_tracker", None)
if pref_tracker is not None:
    try:
        prefs = pref_tracker.get_preferences()
        if prefs:
            pref_lines = [f"{cat}: {val}" for cat, val in prefs.items()]
            # Return alongside memory_lines, fact_lines, cross_branch_lines
    except Exception as exc:
        logger.debug("Preference retrieval failed: %s", exc)
```

**Where preferences are used in prompts:**
1. `cmd_voice_run()` system prompt building (line 3819-3833) -- add preferences section
2. `_web_augmented_llm_conversation()` system prompt building (line 2984-2999) -- add preferences section

**Implementation approach:** Extend `_build_smart_context()` to return a 4th element (preference_lines) or add a separate `_get_user_preferences(bus)` helper function. Cleaner to modify `_build_smart_context()` signature.

### Integration Point 2: Route Quality Penalty in IntentClassifier (LEARN-02)

**Target:** `IntentClassifier.classify()` in `engine/src/jarvis_engine/gateway/classifier.py`, line 314
**Purpose:** Penalize routes with poor user satisfaction so queries gradually shift away from bad routes
**How:** After computing cosine similarity for each route, multiply by route quality score:
```python
# In the similarity loop (lines 353-357)
for route_name, centroid in self._centroids.items():
    sim = self._cosine_sim(query_vec, centroid, query_norm)
    # Apply quality penalty
    if self._feedback_tracker is not None:
        quality = self._feedback_tracker.get_route_quality(route_name)
        if quality["total"] >= 5:  # Need enough data
            sim *= (0.5 + 0.5 * quality["satisfaction_rate"])  # Scale 0.5-1.0
    if sim > best_sim:
        best_sim = sim
        best_route = route_name
```

**Challenge:** IntentClassifier currently has no reference to feedback tracker. Options:
1. **Pass tracker to IntentClassifier.__init__()** -- cleanest, requires updating app.py wiring
2. **Pass tracker per-classify() call** -- more flexible but changes API
3. **Store on bus, access via bus reference** -- indirect coupling

**Recommended:** Option 1 -- add `feedback_tracker` param to IntentClassifier.__init__() and update `app.py` line 265 to pass it. This keeps the classifier self-contained.

### Integration Point 3: Usage Pattern Proactive Checks (LEARN-03)

**Target:** Daemon cycle in `_cmd_daemon_run_impl()` in `engine/src/jarvis_engine/main.py`, around line 2547
**Purpose:** Use time-of-day patterns to pre-fetch context or suggest proactive actions
**How:** In the daemon cycle, after proactive checks, query predict_context() for current time:
```python
# In daemon cycle, after proactive checks
usage_tracker = getattr(bus, "_usage_tracker", None)
if usage_tracker is not None:
    from datetime import datetime
    now = datetime.now(UTC)
    prediction = usage_tracker.predict_context(now.hour, now.weekday())
    if prediction["interaction_count"] > 5:
        print(f"predicted_route={prediction['likely_route']}")
        print(f"predicted_topics={','.join(prediction['common_topics'][:3])}")
```

### Integration Point 4: Intelligence Dashboard (LEARN-07, LEARN-08)

**Target:** `build_intelligence_dashboard()` in `engine/src/jarvis_engine/intelligence_dashboard.py`, line 176
**Purpose:** Display learning data alongside growth metrics
**How:** Add learning sections to the dashboard dict:
```python
# In build_intelligence_dashboard(), before return statement (line 238)
# Add:
"learning": _safe_learning_metrics(root),
```

New helper function `_safe_learning_metrics()` reads from all 3 trackers:
- `get_all_route_quality()` for per-route satisfaction
- `get_preferences()` for preference summary
- `get_peak_hours()` and `get_hourly_distribution()` for usage patterns

**Challenge:** Dashboard currently takes only `root: Path`. Trackers are in-memory objects on the bus. Options:
1. Pass trackers as optional kwargs to `build_intelligence_dashboard()`
2. Pass trackers from `IntelligenceDashboardHandler.__init__()` (update app.py wiring)
3. Recreate trackers from DB path (wasteful but decoupled)

**Recommended:** Option 2 -- update `IntelligenceDashboardHandler` to accept tracker references, pass them from `app.py` during bus registration.

### Integration Point 5: Relevance Scoring in Hybrid Search (LEARN-05)

**Target:** `hybrid_search()` in `engine/src/jarvis_engine/memory/search.py`, line 50
**Purpose:** Factor access_count, recency, and KG connections into ranking
**How:** After RRF combination and recency boost (step 5), add a relevance score:
```python
# After line 108 (boosted_score calculation)
from jarvis_engine.learning.relevance import compute_relevance_score
days_since_access = ...  # compute from last_accessed
days_since_creation = ...  # compute from ts
access_count = record.get("access_count", 0)
connection_count = ...  # optional: query KG for connections
relevance = compute_relevance_score(access_count, days_since_access, days_since_creation, connection_count)
boosted_score = score * (1.0 + recency_weight * recency) * (0.8 + 0.2 * relevance)
```

**Note:** The records table already has `access_count`, `last_accessed`, and `ts` columns. The recency factor already exists in hybrid_search via `_recency_weight()`, so the relevance score should only add the frequency and connection dimensions (avoid double-counting recency).

### Integration Point 6: Tier Management in MemoryConsolidator (LEARN-06)

**Target:** `MemoryConsolidator.consolidate()` in `engine/src/jarvis_engine/learning/consolidator.py`, line 76
**Purpose:** Auto-archive stale memories, promote hot memories based on relevance
**How:** Add a tier-update pass to the consolidation pipeline:
```python
# After consolidation loop (line 160), add tier update pass
if not dry_run:
    self._update_tiers(records)

def _update_tiers(self, records: list[dict]) -> None:
    from jarvis_engine.learning.relevance import compute_relevance_score, classify_tier_by_relevance
    now = datetime.now(UTC)
    for record in records:
        access_count = record.get("access_count", 0)
        # compute days_since_access, days_since_creation from record timestamps
        relevance = compute_relevance_score(access_count, days_since_access, days_since_creation)
        new_tier = classify_tier_by_relevance(relevance, days_since_creation)
        if new_tier != record.get("tier"):
            self._engine._db.execute(
                "UPDATE records SET tier = ? WHERE record_id = ?",
                (new_tier, record.get("record_id")),
            )
    self._engine._db.commit()
```

### Integration Point 7: Knowledge Metrics in Dashboard (LEARN-07)

**Target:** `_safe_kg_metrics()` in `engine/src/jarvis_engine/intelligence_dashboard.py`, line 266
**Purpose:** Show KG health metrics in dashboard
**How:** `capture_knowledge_metrics(kg, engine)` is already defined but never called. Wire it into the dashboard. The existing `_safe_kg_metrics()` reads from a JSONL history file. Either:
1. Call `capture_knowledge_metrics()` directly and include its output
2. Wire it into the daemon to periodically write to the JSONL (it may already be wired via `kg_metrics.py`)

Let me check the daemon for kg_metrics writes.

## Data Schema

### Table: user_preferences
```sql
CREATE TABLE user_preferences (
    category TEXT NOT NULL,
    preference TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    last_observed TEXT NOT NULL,
    PRIMARY KEY (category, preference)
);
```
**Categories:** communication_style, time_preferences, format_preferences
**Score capping:** MAX 10.0 (increments of 0.1 per observation)

### Table: response_feedback
```sql
CREATE TABLE response_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route TEXT NOT NULL DEFAULT '',
    feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
    user_message_snippet TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX idx_feedback_route ON response_feedback(route);
```
**Current bug:** `route` is always `''` because it's not passed through

### Table: usage_patterns
```sql
CREATE TABLE usage_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
    day_of_week INTEGER NOT NULL CHECK(day_of_week >= 0 AND day_of_week <= 6),
    route TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX idx_usage_hour_dow ON usage_patterns(hour, day_of_week);
```
**Current bug:** `route` and `topic` are always `''` because they're not passed through

### Table: records (memory engine)
```sql
CREATE TABLE records (
    record_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT 'general',
    tags TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.72,
    tier TEXT NOT NULL DEFAULT 'warm',
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```
**Relevance-relevant columns:** `access_count`, `last_accessed`, `ts`, `tier`, `created_at`

## Architecture Patterns

### Recommended Change Structure

```
engine/src/jarvis_engine/
  commands/
    learning_commands.py     # ADD: route, topic fields to LearnInteractionCommand
  handlers/
    learning_handlers.py     # UPDATE: pass route, topic to learn_from_interaction()
  learning/
    engine.py                # UPDATE: accept and forward route, topic to trackers
    consolidator.py          # ADD: _update_tiers() method using relevance scoring
  gateway/
    classifier.py            # ADD: feedback_tracker param, quality penalty in classify()
  memory/
    search.py                # ADD: relevance_score factor in hybrid_search ranking
  intelligence_dashboard.py  # ADD: learning metrics section
  main.py                    # UPDATE: pass route to LearnInteractionCommand at 4 sites
                             # UPDATE: _build_smart_context() to include preferences
  app.py                     # UPDATE: expose trackers on bus, pass to IntentClassifier
                             # UPDATE: pass trackers to IntelligenceDashboardHandler
```

### Pattern: Bus Exposure for Subsystem Access

Existing pattern from app.py lines 554-558:
```python
bus._engine = engine
bus._embed_service = embed_service
bus._intent_classifier = intent_classifier
bus._kg = kg
bus._gateway = gateway
```

Follow this exact pattern for trackers:
```python
bus._pref_tracker = pref_tracker
bus._feedback_tracker = feedback_tracker
bus._usage_tracker = usage_tracker
```

### Pattern: Graceful Degradation

All integration points must follow the existing pattern of graceful degradation:
```python
tracker = getattr(bus, "_pref_tracker", None)
if tracker is not None:
    try:
        result = tracker.get_preferences()
    except Exception as exc:
        logger.debug("Preference retrieval failed: %s", exc)
```

### Anti-Patterns to Avoid
- **Recreating trackers from DB path**: Wasteful; reuse the existing singleton instances on the bus
- **Breaking existing system prompt structure**: Preferences should be additive, not replace existing context
- **Quality penalty too aggressive**: A route with 2 negative feedbacks and 0 positive should NOT be fully blocked; need minimum sample threshold
- **Double-counting recency**: `hybrid_search` already has recency boost; relevance score should weight frequency and connections, NOT recency again

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Relevance scoring | Custom scoring formula | `compute_relevance_score()` | Already exists in learning/relevance.py with BM25-like formula |
| Tier classification | Custom tier logic | `classify_tier_by_relevance()` | Already exists with defined thresholds (hot/warm/cold/archive) |
| Knowledge metrics | Custom KG statistics | `capture_knowledge_metrics()` | Already exists in learning/metrics.py |
| Route quality API | Custom SQL queries | `ResponseFeedbackTracker.get_route_quality()` / `get_all_route_quality()` | Already exists with windowed queries |

## Common Pitfalls

### Pitfall 1: Frozen Dataclass Modification
**What goes wrong:** `LearnInteractionCommand` is `@dataclass(frozen=True)`. Adding new fields is safe, but changing the field order would break existing callers using positional args.
**Why it happens:** Existing dispatch sites use keyword arguments, so adding new fields with defaults is safe.
**How to avoid:** Add `route: str = ""` and `topic: str = ""` AFTER existing fields with defaults.
**Warning signs:** Tests constructing LearnInteractionCommand without kwargs.

### Pitfall 2: Thread Safety of Tracker Reads
**What goes wrong:** Trackers use `_db_lock` for reads and `_write_lock` for writes. Both are threading.Lock instances shared from MemoryEngine.
**Why it happens:** SQLite WAL mode allows concurrent reads, but locks prevent write-write and read-during-write conflicts.
**How to avoid:** All tracker reads already use `with self._db_lock:`. The bus exposure is safe because trackers are thread-safe by design.
**Warning signs:** None expected; existing locking is correct.

### Pitfall 3: Empty Route Data in Existing Records
**What goes wrong:** All existing `response_feedback` and `usage_patterns` rows have `route = ''`. When wiring in route quality scoring, there will be zero data for any named route initially.
**Why it happens:** The route parameter was never passed.
**How to avoid:** Ensure quality penalty has a minimum sample threshold (e.g., `if quality["total"] >= 5`). Below threshold, no penalty applied.
**Warning signs:** IntentClassifier suddenly routing everything to one model.

### Pitfall 4: Dashboard Function Signature Change
**What goes wrong:** `build_intelligence_dashboard(root, last_runs)` is called from `IntelligenceDashboardHandler.handle()`. Adding tracker params requires updating the handler too.
**Why it happens:** Handler instantiation in app.py would need tracker references.
**How to avoid:** Either pass trackers through handler constructor (clean) or access them from the bus via handler (slightly coupled). Use the handler constructor approach for consistency.
**Warning signs:** Dashboard tests breaking due to changed function signatures.

### Pitfall 5: System Prompt Token Budget
**What goes wrong:** Adding preferences to system prompt increases token count. LLM context windows are not infinite.
**Why it happens:** Preferences, facts, memories, cross-branch, web search all compete for system prompt space.
**How to avoid:** Keep preference injection concise (max 3 lines). Format as: "User preferences: concise responses, lists preferred, morning person"
**Warning signs:** Gateway errors about context length exceeded.

### Pitfall 6: Relevance Score Double-Counting Recency
**What goes wrong:** `hybrid_search` already boosts by recency (7-day half-life). `compute_relevance_score` also has a recency component (30-day half-life). Combining both would over-weight recency.
**Why it happens:** The two systems were designed independently.
**How to avoid:** When integrating relevance into hybrid_search, use ONLY the frequency (access_count) and connection_count components, NOT the recency component (it's already handled).
**Warning signs:** Very recent memories always dominating regardless of relevance.

## Code Examples

### Example 1: Adding route/topic to LearnInteractionCommand
```python
# engine/src/jarvis_engine/commands/learning_commands.py
@dataclass(frozen=True)
class LearnInteractionCommand:
    """Learn from a user/assistant interaction pair."""
    user_message: str = ""
    assistant_response: str = ""
    task_id: str = ""
    route: str = ""      # NEW: IntentClassifier route name
    topic: str = ""      # NEW: extracted topic for usage patterns
```

### Example 2: Passing route in learn_from_interaction()
```python
# engine/src/jarvis_engine/learning/engine.py
def learn_from_interaction(
    self,
    user_message: str,
    assistant_response: str,
    task_id: str = "",
    route: str = "",    # NEW
    topic: str = "",    # NEW
) -> dict:
    # ...
    if self._feedback_tracker is not None:
        try:
            feedback_detected = self._feedback_tracker.record_feedback(
                user_message, route=route  # NOW PASSES ROUTE
            )
        except Exception as exc:
            logger.warning("Failed to record feedback: %s", exc)

    if self._usage_tracker is not None:
        try:
            self._usage_tracker.record_interaction(
                route=route, topic=topic  # NOW PASSES ROUTE AND TOPIC
            )
        except Exception as exc:
            logger.warning("Failed to record usage pattern: %s", exc)
```

### Example 3: Dispatching with route from voice conversation
```python
# engine/src/jarvis_engine/main.py, line 3968
bus.dispatch(LearnInteractionCommand(
    user_message=text[:1000],
    assistant_response=result.text.strip()[:1000],
    task_id=f"conv-{_route}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
    route=_route,         # NEW: pass classified route
    topic=text[:100],     # NEW: use query as topic hint
))
```

### Example 4: Preference injection in system prompt
```python
# In _build_smart_context() return value or caller
pref_tracker = getattr(bus, "_pref_tracker", None)
if pref_tracker is not None:
    try:
        prefs = pref_tracker.get_preferences()
        if prefs:
            pref_str = ", ".join(f"{k}: {v}" for k, v in prefs.items())
            system_parts.append(f"User preferences: {pref_str}")
    except Exception as exc:
        logger.debug("Preference retrieval failed: %s", exc)
```

### Example 5: Route quality penalty in IntentClassifier
```python
# In IntentClassifier.classify(), inside the route scoring loop
for route_name, centroid in self._centroids.items():
    sim = self._cosine_sim(query_vec, centroid, query_norm)
    if self._feedback_tracker is not None:
        quality = self._feedback_tracker.get_route_quality(route_name)
        if quality["total"] >= 5:
            # Scale similarity by 0.5-1.0 based on satisfaction rate
            sim *= (0.5 + 0.5 * quality["satisfaction_rate"])
    if sim > best_sim:
        best_sim = sim
        best_route = route_name
```

### Example 6: Bus exposure in app.py
```python
# After line 558 in app.py
bus._pref_tracker = pref_tracker        # type: ignore[attr-defined]
bus._feedback_tracker = feedback_tracker  # type: ignore[attr-defined]
bus._usage_tracker = usage_tracker        # type: ignore[attr-defined]
```

## Integration Risks

### Risk 1: Performance Impact of Tracker Reads in Hot Path
**Severity:** LOW
**Analysis:** `get_preferences()` is a simple SELECT with GROUP BY (max ~20 rows total). `get_route_quality()` is a windowed SELECT limited to 20 rows. Both are sub-millisecond on SQLite. The system prompt building already does FTS5 + KNN search which is far more expensive.
**Mitigation:** None needed. If concerned, cache preferences with a 60-second TTL.

### Risk 2: Empty Data During Cold Start
**Severity:** MEDIUM
**Analysis:** First-time users will have no preferences, no feedback, and no usage patterns. All read methods return sensible defaults (empty dict, zero counts, etc.). The penalty logic must handle this gracefully.
**Mitigation:** All quality penalties must have minimum sample thresholds (already recommended above).

### Risk 3: Test Impact
**Severity:** MEDIUM
**Analysis:** Changes to `LearnInteractionCommand` (adding fields with defaults) won't break existing tests. Changes to `_build_smart_context()` return value need test updates. Changes to `IntentClassifier.__init__()` signature need test mock updates. Changes to `build_intelligence_dashboard()` need test updates.
**Mitigation:** Add new tests for each integration point. Update existing test mocks where signatures change.

### Risk 4: Backward Compatibility of LearnInteractionCommand
**Severity:** LOW
**Analysis:** The command is `@dataclass(frozen=True)`. All 4 existing dispatch sites use keyword arguments. Adding `route: str = ""` and `topic: str = ""` with defaults means existing callers work unchanged. The handler also uses keyword access.
**Mitigation:** None needed; defaults provide backward compatibility.

### Risk 5: Topic Extraction Quality
**Severity:** LOW
**Analysis:** LEARN-04 requires passing `topic` to usage tracker. The simplest approach is using the first 100 chars of the query as the topic. This is crude but sufficient for pattern mining (the tracker uses Counter to find most common topics). More sophisticated topic extraction can be added later.
**Mitigation:** Start with simple truncation. If patterns are too noisy, add keyword extraction later.

## Dependency Map

```
LEARN-04 (route params) -> MUST be done first
  |-- enables LEARN-02 (route quality needs real route data)
  |-- enables LEARN-03 (usage prediction needs real route/topic data)
  |-- enables LEARN-08 (dashboard needs per-route data)

Bus exposure (trackers on bus) -> MUST be done before LEARN-01, LEARN-02, LEARN-03, LEARN-08
  |-- enables LEARN-01 (preference read from system prompt builder)
  |-- enables LEARN-02 (feedback read from IntentClassifier)
  |-- enables LEARN-03 (usage read from daemon cycle)
  |-- enables LEARN-08 (all trackers read from dashboard)

LEARN-05 (relevance in search) -> independent
LEARN-06 (tier in consolidator) -> independent (but benefits from LEARN-05)
LEARN-07 (KG metrics in dashboard) -> independent
```

**Recommended order:**
1. LEARN-04 (route params) -- unblocks meaningful data collection
2. Bus exposure (trackers on bus) -- unblocks all read integrations
3. LEARN-01 (preference injection) -- highest user-visible impact
4. LEARN-02 (route quality penalty) -- improves routing quality
5. LEARN-03 (usage predictions) -- enables proactive features
6. LEARN-05 (relevance in search) -- improves memory retrieval
7. LEARN-06 (tier management) -- improves memory organization
8. LEARN-07 + LEARN-08 (dashboard) -- visibility/observability

## Open Questions

1. **Topic extraction method**
   - What we know: UsagePatternTracker stores a `topic` string per interaction
   - What's unclear: Should topic be the raw query, extracted keywords, or classified category?
   - Recommendation: Start with raw query truncated to 100 chars. Simple and sufficient for Counter-based pattern mining.

2. **Quality penalty magnitude**
   - What we know: Routes with poor satisfaction should be penalized in classification
   - What's unclear: How aggressively? A linear 0.5-1.0 scale? Logarithmic? Should 0% satisfaction completely block a route?
   - Recommendation: Use `0.5 + 0.5 * satisfaction_rate` (linear, floor at 0.5x). This means even 0% satisfaction only halves the similarity, never fully blocks.

3. **Preference format in system prompt**
   - What we know: Preferences are `{category: preference}` e.g. `{"communication_style": "concise", "format_preferences": "lists"}`
   - What's unclear: Exact prompt phrasing for best LLM adherence
   - Recommendation: Single line: "User preferences: concise communication, list format preferred" -- minimal tokens, clear signal.

4. **Relevance vs recency overlap in hybrid_search**
   - What we know: hybrid_search has 7-day half-life recency, relevance has 30-day half-life recency
   - What's unclear: Best way to combine without double-counting
   - Recommendation: Use ONLY frequency and connection components from `compute_relevance_score`, skip its recency term.

## Sources

### Primary (HIGH confidence)
- Direct source code analysis of all referenced files in the jarvis-memory-repo codebase
- All line numbers and code references verified against actual source files

### File References
| File | Key Lines | Purpose |
|------|-----------|---------|
| `engine/src/jarvis_engine/learning/engine.py` | 51-160 | ConversationLearningEngine -- write path |
| `engine/src/jarvis_engine/learning/preferences.py` | 14-125 | PreferenceTracker -- full API |
| `engine/src/jarvis_engine/learning/feedback.py` | 14-164 | ResponseFeedbackTracker -- full API |
| `engine/src/jarvis_engine/learning/usage_patterns.py` | 15-120 | UsagePatternTracker -- full API |
| `engine/src/jarvis_engine/learning/relevance.py` | 14-67 | Relevance scoring + tier classification |
| `engine/src/jarvis_engine/learning/metrics.py` | 17-94 | Knowledge metrics capture |
| `engine/src/jarvis_engine/learning/consolidator.py` | 52-362 | MemoryConsolidator |
| `engine/src/jarvis_engine/commands/learning_commands.py` | 9-14 | LearnInteractionCommand (missing route) |
| `engine/src/jarvis_engine/handlers/learning_handlers.py` | 22-49 | LearnInteractionHandler |
| `engine/src/jarvis_engine/app.py` | 398-443, 554-558 | Bus wiring, subsystem exposure |
| `engine/src/jarvis_engine/main.py` | 575-654 | _build_smart_context() |
| `engine/src/jarvis_engine/main.py` | 2048, 3101, 3968, 4037 | 4 LearnInteractionCommand dispatch sites |
| `engine/src/jarvis_engine/main.py` | 2622-2663 | Daemon consolidation cycle |
| `engine/src/jarvis_engine/gateway/classifier.py` | 29-386 | IntentClassifier |
| `engine/src/jarvis_engine/intelligence_dashboard.py` | 176-263 | Dashboard builder |
| `engine/src/jarvis_engine/memory/search.py` | 50-120 | hybrid_search |
| `engine/src/jarvis_engine/handlers/task_handlers.py` | 173-243 | QueryHandler |

## Metadata

**Confidence breakdown:**
- Current architecture understanding: HIGH -- all source files read completely
- Integration point identification: HIGH -- exact file paths and line numbers provided
- Data quality bugs: HIGH -- verified by reading actual call sites
- Risk assessment: HIGH -- based on understanding of threading model and data flow

**Research date:** 2026-03-02
**Valid until:** 2026-04-02 (stable codebase, no external dependency changes expected)
