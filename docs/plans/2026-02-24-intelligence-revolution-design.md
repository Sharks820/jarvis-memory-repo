# Jarvis v3.0 Intelligence Revolution Design

## Goal
Transform Jarvis from a regex-pattern knowledge system into an LLM-powered learning engine that extracts arbitrary facts, learns from corrections, consolidates memories over time, resolves entity duplicates, reasons semantically across knowledge branches, and provides full visibility into every decision.

## Components

### 1. LLM Fact Extraction (`knowledge/llm_extractor.py`)
Replaces 6 rigid regex patterns with LLM-powered structured extraction.

**Input:** Raw conversation text (user or assistant message)
**Output:** List of `ExtractedFact(entity, relationship, value, confidence, category, source_text)`
**Categories:** health, finance, family, preferences, schedule, location, work, hobby, education, social
**Privacy:** Privacy keywords in source text force local Ollama; general text uses cloud gateway.
**Integration:** Called from `IngestionPipeline._extract_facts()` after record storage.
**Prompt strategy:** Few-shot with 10 diverse examples covering all categories. JSON structured output.

### 2. Correction Learning (`learning/correction_detector.py`)
Detects when users correct Jarvis and updates the knowledge graph.

**Detection patterns:** "No, actually...", "That's wrong...", "I meant...", "Not X, it's Y", "You're confusing...", negation + correction in same message.
**Process:** Extract old_claim and new_claim -> search KG for matching facts (embedding similarity) -> update fact value + boost confidence -> mark old value as superseded with audit trail.
**Integration:** Called from `ConversationLearningEngine.learn_from_interaction()` on user messages.

### 3. Memory Consolidation (`learning/consolidator.py`)
Periodically merges fragmentary episodic memories into authoritative facts.

**Grouping:** Cluster related records by entity/topic using embedding similarity (threshold 0.75).
**Summarization:** Send each cluster to LLM with prompt: "Consolidate these memories into a single authoritative fact statement."
**Output:** New semantic record with higher confidence, tagged `["consolidated"]`.
**Originals:** Archived with `consolidated_into` reference, not deleted.
**Schedule:** Every 50 daemon cycles or on explicit `brain-compact` command.

### 4. Entity Resolution (`knowledge/entity_resolver.py`)
Identifies and merges duplicate KG nodes that refer to the same real-world entity.

**Detection:** Embedding similarity > 0.85 between node labels, plus fuzzy string matching (Levenshtein ratio > 0.8).
**Merge strategy:** Keep the most specific label as canonical. Transfer all edges from merged nodes. Record merge history for audit.
**Example:** "Dr. Smith", "my doctor", "Dr. Sarah Smith" -> canonical "Dr. Sarah Smith" with all relationships preserved.

### 5. Semantic Cross-Branch Reasoning (upgrade `cross_branch.py`)
Replace keyword LIKE matching with embedding-based semantic similarity.

**Current:** SQL `LIKE '%keyword%'` -- misses "running" <-> "cardio" connection.
**New:** Embed each node label, compute pairwise cosine similarity across branches, create edges where sim > 0.70.
**Optimization:** Only compute for nodes modified since last run (incremental).

### 6. Activity Feed (`activity_feed.py`)
Ring buffer logging every significant Jarvis decision for full transparency.

**Events:** llm_routing, fact_extracted, correction_applied, consolidation_run, regression_check, daemon_cycle_start/end, proactive_trigger, harvest_completed, web_research, error
**Storage:** SQLite table `activity_log` with timestamp, category, summary, details_json.
**API:** `GET /activity?limit=50&category=llm_routing` -- new endpoint.
**UI:** New "Activity" section in desktop widget and quick panel.

### 7. Enhanced Anti-Regression
Continuous monitoring with automated remediation.

**Daemon integration:** Run regression check every 10 cycles.
**Auto-backup:** Snapshot KG state before any bulk operation (consolidation, entity resolution, harvest ingest).
**Node-level diff:** Track which specific nodes/edges were added/removed/modified.
**Branch coverage:** Expand golden tasks from 5 to 18 (2 per branch for all 9 branches).
**Remediation:** On regression detection, auto-restore from last good backup + alert user via activity feed.

## Execution Order

```
Phase 14A: LLM Fact Extraction + Activity Feed (foundation)
Phase 14B: Correction Learning + Entity Resolution
Phase 14C: Memory Consolidation + Semantic Cross-Branch
Phase 14D: Enhanced Anti-Regression + Dashboard Integration
```

## Success Criteria
- Facts extracted from ANY conversation topic (not just 6 patterns)
- User corrections update the KG within the same interaction
- Memory count stabilizes as consolidation merges duplicates
- Entity graph has zero obvious duplicates
- Activity feed shows every LLM routing decision with reasoning
- Regression detected and auto-remediated within 1 daemon cycle
- All existing 496 tests continue to pass
- 30+ new tests covering all new components
