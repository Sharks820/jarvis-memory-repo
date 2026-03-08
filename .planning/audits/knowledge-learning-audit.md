# Knowledge Graph & Learning Subsystem Deep Audit

**Date**: 2026-03-07  
**Scope**: `engine/src/jarvis_engine/knowledge/`, `engine/src/jarvis_engine/learning/`, `engine/src/jarvis_engine/harvesting/`  
**Auditor**: Automated deep scan  

---

## 1. Knowledge Graph Architecture Analysis

### 1.1 Core Design (graph.py — 31KB, 8 files total)

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| KnowledgeGraph | graph.py | ~780 | SQLite-backed graph with NetworkX computation bridge |
| FactExtractor | facts.py | ~130 | Regex-based fact triple extraction (6 domain patterns) |
| LLMFactExtractor | llm_extractor.py | ~260 | LLM-powered fact extraction with privacy routing |
| FactLockManager | locks.py | ~140 | Auto-lock (conf≥0.9 + 3 sources) and owner locks |
| ContradictionManager | contradictions.py | ~420 | Quarantine + 3-way resolution (accept/keep/merge) |
| EntityResolver | entity_resolver.py | ~530 | Near-duplicate detection (string + vector) and merge |
| RegressionChecker | regression.py | ~390 | WL-hash snapshots, backup/restore, node-level diff |
| KGManagerBase | _base.py | ~60 | Shared DB + lock wiring base class |

**Architecture quality: 8.5/10** — Clean separation of concerns, proper threading with `_write_lock` / `_db_lock`, generation-based caching for NetworkX bridge.

### 1.2 Storage Model

- **SQLite tables**: `kg_nodes` (with confidence, locked, sources, history JSON), `kg_edges` (source→target with relation), `kg_contradictions`
- **FTS5 index**: `fts_kg_nodes` for keyword search with automatic backfill
- **sqlite-vec index**: `vec_kg_nodes` for semantic KNN search when embedding service available
- **Indexes**: type, locked, edges (source/target/relation), unique edge constraint

**Schema quality: 9/10** — Comprehensive schema with proper indexes, FTS5+vector dual search, and soft-retraction (confidence=0) instead of hard deletes.

### 1.3 Thread Safety

- All writes through `_write_lock` (non-reentrant `threading.Lock`)
- All reads through `_db_lock` to prevent cursor interleaving
- Embeddings computed BEFORE acquiring write lock (excellent — avoids holding lock during slow model calls)
- NetworkX cache invalidated atomically via `_mutation_counter`

**Thread safety: 9/10** — Well-designed lock ordering prevents deadlocks. Only minor concern: `check_and_auto_lock` acquires `_write_lock` after `add_fact` already released it, creating a brief TOCTOU window (mitigated by re-reading inside the lock).

---

## 2. Learning System Architecture Analysis

### 2.1 Component Map

| Component | File | Lines | Purpose | Quality |
|-----------|------|-------|---------|---------|
| ConversationLearningEngine | engine.py | ~270 | Orchestrator — routes messages to all trackers | 8/10 |
| PreferenceTracker | preferences.py | ~120 | Keyword-based preference detection + scoring | 6/10 |
| ResponseFeedbackTracker | feedback.py | ~180 | Implicit feedback detection (correction/satisfaction signals) | 7/10 |
| UsagePatternTracker | usage_patterns.py | ~120 | Time-of-day/day-of-week pattern mining | 7/10 |
| CorrectionDetector | correction_detector.py | ~320 | Regex correction patterns + KG fact updates | 8/10 |
| MemoryConsolidator | consolidator.py | ~430 | Episodic→semantic clustering via LLM | 8/10 |
| CrossBranchReasoning | cross_branch.py | ~210 | Keyword-based cross-domain edge creation | 7/10 |
| TemporalClassifier | temporal.py | ~120 | Fact lifetime classification (permanent/time-sensitive) | 7/10 |
| RelevanceScorer | relevance.py | ~55 | BM25-inspired frequency+recency+connection scoring | 7/10 |
| MetricsCapture | metrics.py | ~100 | Snapshot of knowledge growth state | 7/10 |

### 2.2 Learning Pipeline Flow

```
User Message → ConversationLearningEngine.learn_from_interaction()
  ├── CorrectionDetector.detect_correction() → apply to KG
  ├── PreferenceTracker.observe() → update preference scores
  ├── ResponseFeedbackTracker.record_feedback() → route quality tracking
  ├── UsagePatternTracker.record_interaction() → temporal pattern mining
  └── EnrichedIngestPipeline.ingest() → memory storage + fact extraction
        ├── FactExtractor.extract() (regex)
        ├── LLMFactExtractor.extract_facts() (if gateway available)
        ├── KnowledgeGraph.add_fact() → auto-lock check
        └── create_cross_branch_edges() → inter-domain connections
```

**Pipeline quality: 8/10** — Good orchestration with proper error isolation (each tracker wrapped in try/except). All learning happens inline during `learn_from_interaction`.

---

## 3. Harvesting System Architecture Analysis

### 3.1 Component Map

| Component | File | Lines | Purpose | Quality |
|-----------|------|-------|---------|---------|
| KnowledgeHarvester | harvester.py | ~280 | Multi-provider orchestration + dedup | 8/10 |
| HarvesterProvider | providers.py | ~230 | OpenAI-compatible base + MiniMax/Kimi/Gemini | 8/10 |
| BudgetManager | budget.py | ~250 | Per-provider daily/monthly spend limits | 9/10 |
| ClaudeCodeIngestor | session_ingestors.py | ~100 | Claude Code JSONL session ingestion | 7/10 |
| CodexIngestor | session_ingestors.py | ~100 | OpenAI Codex session ingestion | 7/10 |

### 3.2 Providers

- **MiniMax** (MiniMax-M2.5): $0.30/$1.20 per Mtok, daily $1/monthly $10 budget
- **Kimi** (kimi-k2.5 via Moonshot): $0.60/$2.50 per Mtok, daily $1/monthly $10 budget
- **Kimi NVIDIA** (kimi-k2.5 via NIM): Free tier, 100 requests/day
- **Gemini** (gemini-2.5-flash): Free tier, 50 requests/day

**Harvesting quality: 8/10** — Good multi-provider strategy with budget enforcement, semantic dedup (cosine > 0.92), and SHA-256 exact dedup. Harvested content ingested at lower confidence (0.50) to distinguish from user-provided info.

---

## 4. Per-Component Quality Scores

| Component | Score | Strengths | Weaknesses |
|-----------|-------|-----------|------------|
| **KnowledgeGraph** | 9/10 | Dual FTS5+vec search, lock-free embed, generation cache | No graph traversal reasoning |
| **FactExtractor (regex)** | 6/10 | Fast, free, 6 domain patterns | Very limited coverage, only 6 patterns, cap of 10 facts per content |
| **LLMFactExtractor** | 8/10 | 10 categories, few-shot examples, privacy routing | No confidence calibration per LLM, no extraction validation |
| **FactLockManager** | 9/10 | Auto-lock thresholds, owner override, TOCTOU-safe | No partial lock (field-level) |
| **ContradictionManager** | 9/10 | 3-way resolution, history tracking, vec embedding updates | No auto-resolution heuristics, always requires manual owner decision |
| **EntityResolver** | 8/10 | Vector+string dual mode, O(N*K) scaling, merge history | No transitive merge chains, threshold fixed at 0.85 |
| **RegressionChecker** | 8/10 | WL hash, node-level diff, SQLite backup API | No automatic rollback trigger, max 10 backups may be low |
| **ConversationLearningEngine** | 8/10 | Good orchestration, smart knowledge-bearing filter | No learning from multi-turn context |
| **PreferenceTracker** | 5/10 | Simple keyword matching, score capping | Very limited patterns (3 categories), no temporal decay, no negative preferences |
| **ResponseFeedbackTracker** | 7/10 | Route-level quality metrics, explicit feedback API | No feedback-to-action loop (doesn't change routing behavior) |
| **UsagePatternTracker** | 7/10 | Hour+DOW pattern mining, peak hour detection | No prediction confidence, no proactive trigger integration |
| **CorrectionDetector** | 8/10 | 8 regex patterns, KG fact updates, superseded edges | No multi-turn correction context, no ambiguity resolution |
| **MemoryConsolidator** | 8/10 | Cosine clustering, LLM summarization, tier updates | No incremental consolidation, full re-cluster on each run |
| **CrossBranchReasoning** | 6/10 | Keyword-based cross-domain edges | Only 1-hop, no semantic matching, very crude keyword overlap |
| **TemporalClassifier** | 7/10 | Permanent/time-sensitive/expired classification | Heuristic-only, no learning from actual expiration patterns |
| **RelevanceScorer** | 7/10 | BM25-inspired, connection bonus | No personalization, static weights |
| **KnowledgeHarvester** | 8/10 | Multi-provider, budget, semantic dedup | No topic discovery intelligence, no fact verification |
| **BudgetManager** | 9/10 | Daily+monthly limits, request count limits, atomic checks | Well-designed, minimal issues |

**Overall Average: 7.5/10**

---

## 5. Learning Effectiveness Assessment

### 5.1 Does the system actually learn from conversations?

**YES** — via `ConversationLearningEngine.learn_from_interaction()`:
- User messages ingested as episodic memory
- Facts extracted (regex + LLM) and stored in KG
- Corrections detected and applied
- Preferences observed and scored
- Feedback recorded per route

**Gap**: Learning is single-turn only. No multi-turn conversation context analysis. If the user reveals information over 3 messages, only individual messages are analyzed — no cross-message reasoning.

### 5.2 Are preferences actually used to personalize responses?

**PARTIALLY** — Preferences are:
- ✅ Tracked in `user_preferences` table with category/score
- ✅ Injected into voice context (`voice_context.py:233`)
- ✅ Shown in intelligence dashboard
- ❌ **NOT** used to modify LLM system prompts dynamically
- ❌ **NOT** used to select response format (lists vs prose vs code)
- ❌ **NOT** used to adjust response verbosity

The preference detection itself is very limited: only 3 categories (communication_style, time_preferences, format_preferences) with simple keyword matching. No negative preferences ("I don't like..."), no preference inference from behavior.

### 5.3 Is the feedback loop closed?

**PARTIALLY** — Feedback is:
- ✅ Detected implicitly (9 correction signals, 9 satisfaction signals)
- ✅ Recorded per route with satisfaction rate
- ✅ Explicit feedback API for mobile client
- ❌ **NOT** used to adjust model routing (e.g., if Ollama consistently gets negative feedback, switch to cloud)
- ❌ **NOT** used to modify system prompts
- ❌ **NOT** triggering re-learning when satisfaction drops

### 5.4 Is there active learning?

**NO** — The system does not:
- Ask questions to fill knowledge gaps
- Identify missing information in the KG
- Propose "Would you like me to learn about X?"
- Request confirmation of uncertain facts

### 5.5 Does it detect preference changes over time?

**NO** — Preferences use monotonically increasing scores with a cap (10.0). There is no:
- Temporal decay (old preferences still dominate)
- Change detection (score went from 8 → 2 = user changed their mind)
- Conflicting preference resolution (prefers "concise" but also "elaborate")

### 5.6 Can it generalize from specific interactions?

**PARTIALLY** — via `UsagePatternTracker.predict_context()`:
- ✅ Can predict likely route/topics by time of day
- ❌ **NOT** actually triggering proactive behavior (predict_context is available but not wired to proactive engine triggers)
- ❌ No behavioral generalization ("user always asks X after Y" → proactively offer X)

---

## 6. Anti-Regression Analysis

### 6.1 How regression.py detects knowledge loss

1. **WL Graph Hash**: Weisfeiler-Lehman hash (3 iterations, 16-byte digest) captures structural fingerprint
2. **Count Monitoring**: Tracks node count, edge count, locked count between snapshots
3. **Severity Levels**: node_loss=fail, edge_loss=fail, locked_fact_loss=critical, hash_change_without_growth=warn
4. **Node-Level Diff**: Identifies exactly which nodes were added/removed/modified

### 6.2 Tests verifying fact survival

- ✅ 20+ tests in `test_knowledge_regression.py` covering all regression types
- ✅ 15+ tests in `test_knowledge_locks.py` covering lock enforcement
- ✅ Locked fact loss is correctly classified as "critical"
- ❌ No integration test that simulates a system update and verifies fact preservation
- ❌ No canary facts (known-important facts checked on every startup)

### 6.3 Fact importance scoring

**YES** — via `confidence` field (0.0-1.0):
- Auto-lock at confidence ≥ 0.9 with ≥ 3 distinct sources
- Owner can manually lock any fact
- Locked facts cannot be modified (writes quarantined as contradictions)
- Relevance scoring combines frequency, recency, and KG connectedness

### 6.4 Can facts be locked against modification?

**YES** — Two mechanisms:
1. **Auto-lock**: confidence ≥ 0.9 AND ≥ 3 sources → automatic lock
2. **Owner lock**: Manual `owner_confirm_lock()` bypasses thresholds
3. **Protection**: Locked facts reject label changes, quarantine as contradictions
4. **Unlock**: Explicit `unlock_fact()` required to modify locked facts

### 6.5 Knowledge change versioning

**PARTIAL**:
- ✅ `history` JSON field on kg_nodes stores change log (capped at 50 entries)
- ✅ `kg_merge_history` table records entity resolution merges
- ✅ `kg_contradictions` table preserves all conflict history
- ✅ SQLite backup via `backup_graph()` with auto-prune (max 10)
- ❌ No WAL-style incremental versioning
- ❌ No ability to "rewind" to a specific point in time (only full backup restore)

---

## 7. Knowledge Graph Quality Assessment

### 7.1 Graph density and connectivity

- Cross-branch edges created automatically at ingest time via keyword overlap
- Entity resolution merges transfer all edges, preventing orphaning
- **Gap**: No periodic connectivity audit. Orphan nodes (0 edges) can accumulate without detection or cleanup.

### 7.2 Orphan node detection

**MISSING** — No code exists to:
- Find nodes with zero edges
- Flag or clean up isolated facts
- Report graph connectivity metrics (components, average degree)

### 7.3 Relationship type richness

Currently observed edge relations:
- Domain-specific: `takes`, `has_event`, `prefers`, `family_relation`, `located_at`, `earns` (from regex)
- LLM-extracted: `takes_medication`, `spouse_of`, `works_at`, `child_of`, `has_appointment`, `lives_in`, `job_title`, `practices`, `studying`, `socializes_with`
- System: `cross_branch_related`, `superseded`

**Gap**: No relation taxonomy or ontology. Relations are free-text strings, leading to inconsistency (e.g., `located_at` vs `lives_in` for the same concept).

### 7.4 Query efficiency

- FTS5 for keyword search: O(log n) via inverted index
- sqlite-vec for semantic search: O(n) KNN (could be improved with IVF or HNSW)
- LIKE fallback when FTS5 returns no results: O(n) full scan
- NetworkX reconstruction cached with generation-based invalidation

**Query efficiency: 7/10** — Good for current scale. May need index upgrades (IVF-Flat or HNSW) for >100K facts.

### 7.5 Is the graph used for reasoning or just storage?

**MOSTLY STORAGE** with limited reasoning:
- ✅ Cross-branch queries traverse 1-hop neighbors to find connections
- ✅ Entity resolution uses graph structure (edge count) for merge decisions
- ✅ WL hash captures structural fingerprint for regression detection
- ❌ **NO** multi-hop traversal (A→B→C inference)
- ❌ **NO** path finding between entities
- ❌ **NO** transitive relationship inference
- ❌ **NO** graph-based question answering
- ❌ **NO** subgraph extraction for context augmentation

### 7.6 Can it infer indirect relationships?

**NO** — There is no code for:
- Transitive closure (A knows B, B knows C → A might know C)
- Path queries (shortest path between entities)
- Community detection (clusters of related entities)
- Influence propagation (confidence spreading through edges)

---

## 8. Comparison Against Gold Standards

### 8.1 vs. Personal Knowledge Graphs (Roam, Obsidian, Apple Intelligence)

| Feature | Jarvis | Roam/Obsidian | Apple Intelligence |
|---------|--------|---------------|-------------------|
| Auto-extraction from conversations | ✅ | ❌ (manual) | ✅ (limited) |
| Bidirectional linking | ✅ (directed edges) | ✅ (bidirectional) | ❌ |
| Full-text search | ✅ (FTS5) | ✅ | ✅ |
| Semantic search | ✅ (sqlite-vec) | ❌ | ✅ |
| Contradiction detection | ✅ | ❌ | ❌ |
| Fact locking | ✅ | ❌ | ❌ |
| Graph visualization | ❌ | ✅ | ❌ |
| User-editable knowledge | ❌ | ✅ | ❌ |
| Multi-hop reasoning | ❌ | ❌ | ❌ |
| Temporal awareness | ✅ (basic) | ❌ | ✅ |

**Jarvis advantage**: Automatic extraction + contradiction detection + fact locking is unique.  
**Jarvis gap**: No user-facing knowledge browser/editor, no graph visualization.

### 8.2 vs. AI Memory Systems (mem0, Zep, Letta)

| Feature | Jarvis | mem0 | Zep | Letta |
|---------|--------|------|-----|-------|
| Entity extraction | ✅ (regex+LLM) | ✅ (LLM) | ✅ (LLM) | ✅ (LLM) |
| Preference learning | ✅ (basic) | ✅ | ✅ | ✅ |
| Contradiction handling | ✅ (quarantine+resolve) | ✅ (replace) | ❌ | ❌ |
| Fact confidence scoring | ✅ (0-1 with auto-lock) | ❌ | ❌ | ❌ |
| Anti-regression | ✅ (WL hash + backup) | ❌ | ❌ | ❌ |
| Memory consolidation | ✅ (LLM summarization) | ❌ | ✅ | ✅ |
| Graph-based reasoning | ❌ | ❌ | ❌ | ❌ |
| Multi-user support | ❌ (single user) | ✅ | ✅ | ✅ |
| Active learning | ❌ | ❌ | ❌ | ❌ |
| Cross-session continuity | ✅ | ✅ | ✅ | ✅ |

**Jarvis advantage**: Strongest fact integrity guarantees (locks, contradictions, regression detection) in the field. No competitor has this level of anti-regression protection.  
**Jarvis gap**: No active learning, limited preference learning, no graph reasoning.

### 8.3 What's missing for true personalized AI?

1. **Closed feedback loop**: Feedback detected but not acted upon
2. **Active learning**: System never asks questions to fill knowledge gaps
3. **Behavioral modeling**: No deep user behavior patterns beyond time-of-day
4. **Preference evolution**: No temporal dynamics in preference tracking
5. **Multi-hop reasoning**: Graph exists but isn't used for inference
6. **Context-aware retrieval**: No session-level memory (what was discussed earlier today)
7. **Goal modeling**: No tracking of user's goals and progress toward them
8. **Emotional intelligence**: No sentiment tracking over time

---

## 9. Specific Code Fixes

### FIX-1: PreferenceTracker needs temporal decay (preferences.py)

**Problem**: Preferences only grow. A user who preferred "concise" 6 months ago but now prefers "verbose" still has "concise" winning because of accumulated score.

**Fix**: Add time-decayed scoring. Apply exponential decay based on `last_observed`:
```python
# In get_preferences(), apply decay before ranking
import math
from datetime import datetime
days_since = (now - last_observed).total_seconds() / 86400
decayed_score = score * math.exp(-0.023 * days_since)  # 30-day half-life
```

### FIX-2: PreferenceTracker needs negative preferences (preferences.py)

**Problem**: No way to detect "I don't like X" or "stop doing X".

**Fix**: Add negative signal patterns:
```python
NEGATIVE_PATTERNS = {
    "communication_style": {
        "verbose": ["too much detail", "too long", "shorter please"],
        "concise": ["too brief", "need more detail", "elaborate please"],
    },
}
```
Record with score decrease (-0.2 per negative signal).

### FIX-3: Feedback should influence routing (feedback.py → gateway/)

**Problem**: Route satisfaction rates are computed but never used.

**Fix**: In `ModelGateway.complete()`, check route quality before routing. If satisfaction_rate < 0.4 for a route, bias toward alternative models.

### FIX-4: CrossBranchReasoning needs semantic matching (cross_branch.py)

**Problem**: Uses only keyword overlap for cross-branch edges. "My wife Sarah" and "Sarah's appointment" won't link unless they share a 4+ char keyword.

**Fix**: Use embedding similarity instead of keyword overlap:
```python
# In create_cross_branch_edges, after getting new fact embedding:
similar_facts = kg.query_relevant_facts_semantic(label, limit=5)
for similar in similar_facts:
    if _extract_branch(similar["node_id"]) != source_branch:
        kg.add_edge(new_fact_id, similar["node_id"], "cross_branch_related", ...)
```

### FIX-5: Add orphan node detection (graph.py)

**Problem**: Nodes with zero edges accumulate silently.

**Fix**: Add `find_orphans()` method:
```python
def find_orphans(self, min_age_days: int = 7) -> list[dict]:
    """Find nodes with zero edges older than min_age_days."""
    with self._db_lock:
        return self._db.execute("""
            SELECT n.* FROM kg_nodes n
            LEFT JOIN kg_edges e1 ON n.node_id = e1.source_id
            LEFT JOIN kg_edges e2 ON n.node_id = e2.target_id
            WHERE e1.source_id IS NULL AND e2.target_id IS NULL
              AND n.created_at < datetime('now', ?)
        """, (f"-{min_age_days} days",)).fetchall()
```

### FIX-6: Add relation normalization (graph.py or new file)

**Problem**: Free-text relations cause duplication (`located_at` vs `lives_in` vs `lives_at`).

**Fix**: Create a relation synonym map and normalize at ingest time:
```python
RELATION_SYNONYMS = {
    "lives_in": "located_at",
    "lives_at": "located_at",
    "resides_in": "located_at",
    "works_for": "works_at",
    "employed_at": "works_at",
    ...
}
```

### FIX-7: FactExtractor regex cap is too low (facts.py)

**Problem**: `facts[:10]` caps extraction at 10 facts per content, which is reasonable for short messages but too low for ingesting long documents or harvested knowledge.

**Fix**: Make the cap configurable:
```python
def extract(self, text: str, source: str = "", branch: str = "", max_facts: int = 10) -> list[FactTriple]:
    ...
    return facts[:max_facts]
```

### FIX-8: ContradictionManager should support auto-resolution heuristics (contradictions.py)

**Problem**: All contradictions require manual owner review. For non-critical facts, the system should be able to auto-resolve based on confidence and recency.

**Fix**: Add `auto_resolve_simple()`:
```python
def auto_resolve_simple(self, max_resolve: int = 10) -> int:
    """Auto-resolve non-locked contradictions where incoming confidence is significantly higher."""
    pending = self.list_pending(limit=max_resolve)
    resolved = 0
    for c in pending:
        if c["incoming_confidence"] > c["existing_confidence"] + 0.2:
            self.resolve(c["contradiction_id"], "accept_new")
            resolved += 1
    return resolved
```

---

## 10. Upgrade Roadmap

### Phase 1: Close the Feedback Loop (Priority: CRITICAL)

1. **Wire feedback to routing** — Use `get_route_quality()` satisfaction rates to bias model selection in `ModelGateway`
2. **Wire preferences to system prompts** — Inject detected format/style preferences into LLM system prompts
3. **Add preference decay** — Exponential time decay on preference scores
4. **Add negative preference detection** — Parse "I don't like X" patterns
5. **Auto-resolve simple contradictions** — Confidence-based auto-resolution for non-locked facts

**Estimated effort**: 3-4 days  
**Impact**: Transforms passive data collection into active personalization

### Phase 2: Multi-Hop Graph Reasoning (Priority: HIGH)

1. **Add path queries** — `find_path(entity_a, entity_b, max_hops=3)` using NetworkX shortest_path
2. **Add transitive inference** — If A→B and B→C, create weak edge A→C (confidence = min(AB, BC) * 0.5)
3. **Add subgraph extraction** — Given a query, extract the relevant 2-hop neighborhood for context injection
4. **Add community detection** — Identify clusters of related facts for better consolidation grouping
5. **Add graph-based QA** — "How is X related to Y?" answerable via graph traversal alone

**Estimated effort**: 4-5 days  
**Impact**: Transforms the KG from storage into an actual reasoning engine

### Phase 3: Active Learning (Priority: HIGH)

1. **Knowledge gap detection** — Identify entities with few edges or low-confidence facts
2. **Proactive questions** — "I notice you mentioned Sarah but I don't know her relation to you — can you tell me?"
3. **Confirmation requests** — "I believe you work at Acme Corp — is that still accurate?"
4. **Usage-pattern-driven proactive offers** — Wire `predict_context()` output to proactive engine triggers
5. **Harvest discovery integration** — Auto-harvest topics related to user's known interests

**Estimated effort**: 5-6 days  
**Impact**: System gets smarter autonomously instead of passively waiting for input

### Phase 4: Advanced Learning (Priority: MEDIUM)

1. **Multi-turn conversation context** — Analyze conversation windows (last 5 messages) for implicit facts
2. **Behavioral generalization** — Detect patterns like "user always asks about weather after waking up"
3. **Goal tracking** — Infer user goals from conversation patterns and track progress
4. **Sentiment timeline** — Track emotional tone per topic over time
5. **Relation ontology** — Define a fixed taxonomy of relation types with synonym normalization
6. **Orphan node cleanup** — Periodic detection and pruning of disconnected facts

**Estimated effort**: 6-8 days  
**Impact**: Deep personalization that makes Jarvis feel like it truly "knows" the user

### Phase 5: Scale & Robustness (Priority: MEDIUM)

1. **IVF or HNSW index** — Replace brute-force KNN with approximate NN for >100K facts
2. **Incremental consolidation** — Only process records added since last consolidation run
3. **Canary facts** — Define critical facts checked on every startup for regression protection
4. **Confidence calibration** — Different LLMs produce different confidence levels; normalize them
5. **Fact provenance chain** — Full audit trail: where did this fact come from, through what transformations?

**Estimated effort**: 5-6 days  
**Impact**: Production-grade reliability and performance at scale

---

## Summary

**Strengths** (what Jarvis does better than any competitor):
- Fact integrity (locks + contradictions + regression detection) is best-in-class
- Hybrid search (FTS5 + vector) covers both keyword and semantic queries
- Multi-provider harvesting with budget management is unique
- Automatic fact extraction (regex + LLM) with privacy routing is solid

**Critical Gaps**:
1. **Feedback loop is open** — data collected but not acted upon
2. **No graph reasoning** — the graph is just storage, not an inference engine
3. **No active learning** — system never asks questions or proposes knowledge
4. **Preferences are too simple** — 3 categories, no decay, no negatives
5. **Cross-branch reasoning is crude** — keyword-only, no semantic matching

**Bottom Line**: The foundation is exceptionally solid (8/10 architecture). The gap is in the *intelligence layer* on top — turning collected data into actual personalization and reasoning. The system collects knowledge well but doesn't *use* it well enough yet.
