# Phase 7: Continuous Learning and Self-Improvement - Research

**Researched:** 2026-02-23
**Domain:** Knowledge extraction from interactions, cross-branch reasoning, temporal metadata, golden task evaluation
**Confidence:** HIGH

## Summary

Phase 7 builds four capabilities on top of existing infrastructure: (1) a continuous learning engine that extracts knowledge from every interaction, (2) cross-branch reasoning that connects facts across the 9 life-domain branches, (3) temporal metadata that distinguishes permanent facts from time-sensitive ones, and (4) an enhanced golden task evaluation system that measures and proves Jarvis is getting smarter over time.

The critical insight from codebase analysis is that most building blocks already exist. The `EnrichedIngestPipeline` already calls `_extract_facts()` as a side-effect of ingestion. The `KnowledgeGraph` already has `add_fact()`, `add_edge()`, and `to_networkx()`. The `BranchClassifier` already classifies content into 9 branches. The `MemoryEngine` already provides `search_fts()`, `search_vec()`, and `hybrid_search()`. The `growth_tracker.py` already has `run_eval()`, `append_history()`, `validate_history_chain()`, and `summarize_history()`. The `intelligence_dashboard.py` already computes ETAs, slope, and achievements. What is missing is the **orchestration layer** that (a) captures conversational interactions and feeds them through the existing pipeline, (b) creates cross-branch edges in the KG and queries across branches, (c) adds temporal metadata columns to fact nodes, and (d) evolves the golden task system to include memory-based tasks that test whether Jarvis actually learned from interactions.

**Primary recommendation:** Compose existing building blocks (EnrichedIngestPipeline, KnowledgeGraph, MemoryEngine, growth_tracker) rather than creating new subsystems. The learning engine is a thin orchestrator that calls `pipeline.ingest()` on conversation turns. Cross-branch reasoning is a new query function that traverses KG edges across branch boundaries using NetworkX graph operations. Temporal metadata is a schema migration adding 2 columns to `kg_nodes`. Golden task evolution adds memory-recall tasks to the existing evaluation framework.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| GROW-01 | Continuous learning engine extracts knowledge from every interaction and permanently retains it | ConversationLearningEngine orchestrator that feeds conversation turns through EnrichedIngestPipeline + FactExtractor; existing dedup and fact extraction handle the rest |
| GROW-02 | Golden task evaluation system measures capability scores that demonstrably improve over time | Extend existing growth_tracker.py with memory-recall golden tasks; add knowledge-growth metrics alongside Ollama eval scores |
| KNOW-05 | Cross-branch fact relationships enable cross-domain reasoning queries | New cross_branch_query() function using NetworkX traversal across branch-labeled nodes + embedding search with multi-branch scope |
| KNOW-06 | Temporal metadata on facts distinguishes permanent knowledge from time-sensitive information | Schema migration adding `temporal_type` and `expires_at` columns to kg_nodes; classification heuristics for permanent vs time-sensitive |
</phase_requirements>

## Standard Stack

### Core (all already installed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sqlite3 | stdlib | All persistence -- records, KG nodes, KG edges, contradictions | Already the storage engine for everything |
| networkx | 3.4.2 | Graph traversal for cross-branch reasoning (shortest_path, subgraph, neighbors) | Already installed and used by KnowledgeGraph |
| sentence-transformers | installed | Embedding generation via nomic-embed-text-v1.5 | Already used by EmbeddingService |
| sqlite-vec | installed | Vector similarity search for semantic memory retrieval | Already used by MemoryEngine |

### Supporting (all already installed)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| hashlib | stdlib | Content dedup SHA-256 | Conversation turn dedup before ingestion |
| json | stdlib | Structured data serialization | Golden task definitions, temporal metadata |
| re | stdlib | Fact extraction patterns | Extended patterns for temporal detection |
| threading | stdlib | Write lock for concurrent access | All SQLite writes through existing _write_lock |
| datetime | stdlib | Temporal metadata, expiration checks | expires_at computation, staleness detection |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| NetworkX traversal for cross-branch | Raw SQL JOINs on kg_edges | SQL is faster for simple 2-hop queries but NetworkX enables arbitrary graph algorithms (shortest path, community detection); use NetworkX since it is already loaded |
| Regex-based temporal detection | LLM-based classification | LLM is more accurate but adds latency and cost per fact; regex heuristics are fast and sufficient for v1 |
| Custom eval framework | Existing growth_tracker.py | growth_tracker already does everything needed; extend rather than replace |

**Installation:**
```bash
# No new packages needed. All dependencies already installed.
```

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
    learning/                    # NEW -- continuous learning module
        __init__.py
        engine.py               # ConversationLearningEngine orchestrator
        temporal.py             # Temporal metadata classification and expiration
        cross_branch.py         # Cross-branch query engine
    knowledge/
        facts.py                # EXTEND -- add temporal extraction patterns
        graph.py                # EXTEND -- add branch-aware query methods
    growth_tracker.py           # EXTEND -- add memory-recall golden tasks
    handlers/
        learning_handlers.py    # NEW -- Command Bus handlers for learning commands
    commands/
        learning_commands.py    # NEW -- Command/Result dataclasses
```

### Pattern 1: Conversation Learning Pipeline (GROW-01)

**What:** A thin orchestrator that captures conversation turns and feeds them through the existing `EnrichedIngestPipeline` for automatic knowledge extraction.

**When to use:** After every meaningful interaction (query response, task completion, harvesting result).

**Architecture:**
```
User Conversation
    |
    v
ConversationLearningEngine.learn_from_interaction()
    |
    +--> Filter: is this content knowledge-bearing? (skip greetings, errors, tool outputs)
    |
    +--> EnrichedIngestPipeline.ingest(source="conversation", kind="episodic", ...)
    |        |
    |        +--> chunk -> embed -> classify branch -> store record
    |        +--> _extract_facts() -> KG nodes + edges (side-effect, already exists)
    |
    +--> Return: {records_created, facts_extracted, branch}
```

**Key design decisions:**
- Source is `"conversation"` to distinguish from `"user"`, `"task_outcome"`, or `"harvest:*"` sources
- Kind is `"episodic"` for raw conversation content, `"semantic"` for distilled facts
- Confidence is 0.72 (same as standard ingestion) for owner-provided content; lower (0.50) for Jarvis-generated content
- Content filtering: skip messages shorter than 50 chars, skip pure command invocations, skip tool output
- Dedup is already handled by SHA-256 content hashing in the pipeline

**Example:**
```python
# Source: existing EnrichedIngestPipeline pattern from memory/ingest.py
class ConversationLearningEngine:
    """Extracts and retains knowledge from every interaction."""

    def __init__(
        self,
        pipeline: EnrichedIngestPipeline,
        kg: KnowledgeGraph,
    ) -> None:
        self._pipeline = pipeline
        self._kg = kg

    def learn_from_interaction(
        self,
        user_message: str,
        assistant_response: str,
        task_id: str = "",
    ) -> dict:
        """Extract knowledge from a conversation turn.

        Ingests both user message and assistant response through the
        enriched pipeline. Facts are extracted as a side-effect.

        Returns dict with records_created count and facts summary.
        """
        records_created = 0

        # Ingest user message (higher confidence -- owner-provided)
        if self._is_knowledge_bearing(user_message):
            ids = self._pipeline.ingest(
                source="conversation:user",
                kind="episodic",
                task_id=task_id or "conversation",
                content=user_message,
                tags=["conversation", "user"],
            )
            records_created += len(ids)

        # Ingest assistant response (lower confidence -- generated)
        if self._is_knowledge_bearing(assistant_response):
            ids = self._pipeline.ingest(
                source="conversation:assistant",
                kind="semantic",
                task_id=task_id or "conversation",
                content=assistant_response,
                tags=["conversation", "assistant"],
            )
            records_created += len(ids)

        return {"records_created": records_created}

    def _is_knowledge_bearing(self, text: str) -> bool:
        """Filter out non-knowledge content."""
        if not text or len(text.strip()) < 50:
            return False
        # Skip pure commands and greetings
        lowered = text.strip().lower()
        skip_prefixes = ("jarvis ", "hey ", "ok ", "thanks", "thank you", "goodbye")
        if any(lowered.startswith(p) for p in skip_prefixes) and len(text) < 100:
            return False
        return True
```

### Pattern 2: Cross-Branch Reasoning (KNOW-05)

**What:** Query function that finds relationships between facts in different branches, enabling questions like "do any of my medications conflict with my gaming schedule?"

**When to use:** When a query references concepts from multiple life domains.

**Architecture -- two-phase approach:**

**Phase A: Cross-branch edge creation (at ingest time)**
When a fact is extracted and classified into a branch, check if related facts exist in other branches. Create `cross_branch` edges in the KG.

**Phase B: Cross-branch query execution (at query time)**
1. Embed the query
2. Search for relevant facts across ALL branches (not scoped to one branch)
3. For each hit, traverse KG edges to find connected facts in other branches
4. Return the union of directly-relevant facts + cross-branch connected facts

```python
# Source: composition of existing KnowledgeGraph.to_networkx() and MemoryEngine.search_vec()
def cross_branch_query(
    query: str,
    engine: MemoryEngine,
    kg: KnowledgeGraph,
    embed_service: EmbeddingService,
    k: int = 10,
) -> list[dict]:
    """Find facts across multiple branches that relate to the query.

    1. Embed query and search across all branches
    2. For each result, find KG neighbors in other branches
    3. Return combined results with cross-branch connections
    """
    query_embedding = embed_service.embed_query(query)

    # Step 1: Multi-branch semantic search
    from jarvis_engine.memory.search import hybrid_search
    results = hybrid_search(engine, query, query_embedding, k=k * 2)

    # Step 2: Find branches involved
    branches_hit = {r.get("branch", "general") for r in results}

    # Step 3: For each result, find cross-branch KG connections
    G = kg.to_networkx()
    cross_branch_facts = []

    for record in results[:k]:
        record_id = record.get("record_id", "")
        provenance_id = f"ingest:{record_id}"

        # Find facts extracted from this record
        if provenance_id in G:
            for neighbor in G.neighbors(provenance_id):
                node_data = G.nodes.get(neighbor, {})
                # Check outgoing edges to find connected facts
                for target in G.neighbors(neighbor):
                    target_data = G.nodes.get(target, {})
                    edge_data = G.edges.get((neighbor, target), {})
                    cross_branch_facts.append({
                        "source_fact": neighbor,
                        "relation": edge_data.get("relation", ""),
                        "target_fact": target,
                        "target_label": target_data.get("label", ""),
                    })

    return {
        "direct_results": results[:k],
        "cross_branch_connections": cross_branch_facts,
        "branches_involved": list(branches_hit),
    }
```

**Critical: branch-labeled edges.** The KG schema currently has no `branch` column on nodes. The simplest approach: derive branch from the node_id prefix (e.g., `health.medication.adderall` is in the health branch, `ops.schedule.meeting_monday` is in ops). The `FactExtractor` already uses prefixes like `health.medication`, `ops.schedule`, `family.member`, etc. Cross-branch queries can match on these prefixes.

### Pattern 3: Temporal Metadata (KNOW-06)

**What:** Schema extension that classifies facts as permanent vs time-sensitive and flags expired information.

**Schema migration:**
```sql
-- Add temporal columns to kg_nodes (idempotent)
ALTER TABLE kg_nodes ADD COLUMN temporal_type TEXT NOT NULL DEFAULT 'unknown';
-- Values: 'permanent', 'time_sensitive', 'unknown'

ALTER TABLE kg_nodes ADD COLUMN expires_at TEXT DEFAULT NULL;
-- ISO 8601 timestamp, NULL for permanent facts

CREATE INDEX IF NOT EXISTS idx_kg_nodes_temporal ON kg_nodes(temporal_type);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_expires ON kg_nodes(expires_at)
    WHERE expires_at IS NOT NULL;
```

**Classification heuristics:**
```python
PERMANENT_INDICATORS = [
    "family.member",       # Family relationships don't expire
    "preference",          # Preferences are stable
    "ops.location",        # Home address is stable
    "finance.income",      # Salary is relatively stable
]

TIME_SENSITIVE_INDICATORS = [
    "ops.schedule",        # Events have dates
    "health.medication",   # Prescriptions can change
    # Also: any fact mentioning specific dates, "today", "tomorrow",
    # "this week", "expires", "until", "due"
]

TEMPORAL_DATE_PATTERNS = [
    re.compile(r"\b(expires?|due|until|by|before)\s+", re.IGNORECASE),
    re.compile(r"\b(today|tomorrow|this week|next week|this month)\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),  # US date
]
```

**Expiration flagging (batch job during tier maintenance):**
```python
def flag_expired_facts(kg: KnowledgeGraph) -> int:
    """Flag facts past their expires_at as 'expired'. Run periodically."""
    now = datetime.now(UTC).isoformat()
    with kg.write_lock:
        cur = kg.db.execute(
            """UPDATE kg_nodes
               SET temporal_type = 'expired'
               WHERE expires_at IS NOT NULL
                 AND expires_at < ?
                 AND temporal_type != 'expired'""",
            (now,),
        )
        kg.db.commit()
        return cur.rowcount
```

### Pattern 4: Golden Task Evolution (GROW-02)

**What:** Extend the existing `growth_tracker.py` golden task framework with memory-recall tasks that test whether Jarvis actually retains knowledge from interactions.

**Current state:** The existing golden tasks are Ollama-evaluated prompts with must_include token matching. There are only 3 tasks. The system scores coverage percentage and tracks history with SHA-256 chain integrity.

**Evolution strategy:**
1. **Add memory-based golden tasks** that query the memory system and verify recall
2. **Add knowledge-count metrics** to each eval run (total facts, locked facts, branches covered)
3. **Composite score** = (golden_task_coverage * 0.5) + (memory_recall_accuracy * 0.3) + (knowledge_growth_rate * 0.2)

**Memory-recall task format:**
```json
{
    "id": "medication_recall",
    "type": "memory_recall",
    "query": "What medications does Conner take?",
    "must_find_branches": ["health"],
    "min_results": 1,
    "must_include_in_results": ["medication"]
}
```

**Scoring for memory-recall tasks:**
```python
def score_memory_recall(
    engine: MemoryEngine,
    embed_service: EmbeddingService,
    task: dict,
) -> float:
    """Score a memory-recall golden task.

    Returns coverage 0.0-1.0 based on:
    - Did search return results? (0.3)
    - Did results come from expected branches? (0.3)
    - Did results contain expected keywords? (0.4)
    """
    from jarvis_engine.memory.search import hybrid_search

    query = task["query"]
    query_embedding = embed_service.embed_query(query)
    results = hybrid_search(engine, query, query_embedding, k=10)

    score = 0.0

    # Has results?
    if len(results) >= task.get("min_results", 1):
        score += 0.3

    # Branch coverage
    result_branches = {r.get("branch", "") for r in results}
    expected_branches = set(task.get("must_find_branches", []))
    if expected_branches and expected_branches.issubset(result_branches):
        score += 0.3

    # Keyword coverage in result summaries
    must_include = task.get("must_include_in_results", [])
    if must_include:
        combined = " ".join(r.get("summary", "") for r in results).lower()
        matched = sum(1 for kw in must_include if kw.lower() in combined)
        score += 0.4 * (matched / len(must_include))

    return score
```

### Anti-Patterns to Avoid

- **Anti-pattern: Ingesting every keystroke.** Only ingest knowledge-bearing content. Short commands ("status", "help"), greetings, and error messages should be filtered out. The `_is_knowledge_bearing()` filter is critical.

- **Anti-pattern: Creating separate storage for "learned" knowledge.** ALL knowledge goes through the same `EnrichedIngestPipeline` -> `MemoryEngine` path. No parallel storage system. The `source` field (`conversation:user`, `conversation:assistant`) distinguishes origin.

- **Anti-pattern: Expensive graph operations on every query.** The `to_networkx()` call reconstructs the full graph from SQLite every time. For cross-branch queries, cache the NetworkX graph for a short TTL (e.g., 30 seconds) or use direct SQL queries on kg_edges for simple 2-hop lookups.

- **Anti-pattern: Blocking ingestion on fact extraction.** The existing pattern (try/except around _extract_facts) is correct. Never let fact extraction or temporal classification block the primary record storage path.

- **Anti-pattern: Complex composite scores that are hard to interpret.** Keep golden task scoring simple and auditable. Each component (Ollama eval, memory recall, knowledge growth) should be independently reportable before being combined.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Content deduplication | Custom dedup logic | Existing SHA-256 content_hash UNIQUE constraint in MemoryEngine | Already handles exact dedup; semantic dedup exists in harvester |
| Fact extraction | New NLP pipeline | Existing FactExtractor regex patterns (extend, don't replace) | Regex is fast, deterministic, and sufficient for structured domains |
| Graph traversal | BFS/DFS from scratch | NetworkX shortest_path, neighbors, subgraph | NetworkX is already loaded and well-tested |
| Evaluation history | Custom JSONL format | Existing growth_tracker append_history + SHA-256 chain | Already has tamper-resistant chain integrity |
| Embedding search | Custom vector search | Existing hybrid_search (FTS5 + sqlite-vec + RRF) | Already combines keyword + semantic + recency |
| Branch classification | New classifier | Existing BranchClassifier.classify() | Already classifies via embedding cosine similarity |
| Schema migration | Manual ALTER TABLE | Pattern from existing _ensure_schema / _init_kg_schema | Idempotent, safe, follows project convention |

**Key insight:** Phase 7 is 80% orchestration of existing building blocks and 20% schema extension. The biggest risk is over-engineering something that should be a thin composition layer.

## Common Pitfalls

### Pitfall 1: Stale NetworkX Graph Cache
**What goes wrong:** Caching the NetworkX DiGraph for performance but serving stale data after new facts are added.
**Why it happens:** `to_networkx()` reconstructs from SQLite on every call (by design -- see graph.py comment). If you add caching, inserts won't be visible until cache expires.
**How to avoid:** Either (a) don't cache and accept the overhead, or (b) use a short TTL (30s) and document that cross-branch queries may lag behind ingestion by up to 30 seconds. For v1, option (a) is safer since the graph will be small (hundreds to low thousands of nodes).
**Warning signs:** Cross-branch query returns stale results immediately after learning new facts.

### Pitfall 2: Runaway Ingestion from Verbose Conversations
**What goes wrong:** A long conversation generates hundreds of records, polluting the memory with low-value content.
**Why it happens:** No rate limiting or quality filtering on conversation ingestion.
**How to avoid:** (a) Minimum content length filter (50+ chars). (b) Rate limit: max 20 records per conversation session. (c) Source-based confidence: conversation:assistant at 0.50, conversation:user at 0.72. (d) Tier management will naturally demote low-access records to COLD.
**Warning signs:** Record count spikes after a single conversation; search results dominated by conversation fragments.

### Pitfall 3: ALTER TABLE on Existing Data
**What goes wrong:** SQLite ALTER TABLE ADD COLUMN with NOT NULL requires a DEFAULT. If default is wrong, existing rows get bad data.
**Why it happens:** SQLite doesn't support adding NOT NULL columns without defaults to tables with existing data.
**How to avoid:** Use `DEFAULT 'unknown'` for temporal_type. Run a backfill pass after migration to classify existing facts.
**Warning signs:** All existing facts showing as `temporal_type='unknown'` forever because backfill was forgotten.

### Pitfall 4: Circular Learning (Jarvis Learning from Itself)
**What goes wrong:** Jarvis ingests its own responses, which are based on its own memory, creating a feedback loop that reinforces errors.
**Why it happens:** Assistant responses are ingested at the same confidence as user-provided facts.
**How to avoid:** (a) Tag assistant-generated content with `source="conversation:assistant"` and lower confidence (0.50). (b) Never auto-lock facts from assistant sources. (c) Only user-provided content gets standard confidence (0.72).
**Warning signs:** Facts appearing in KG that were never stated by the user, only by Jarvis in responses.

### Pitfall 5: Golden Task Score Inflation
**What goes wrong:** Scores appear to improve but only because easier tasks were added.
**Why it happens:** Adding memory-recall tasks that are trivially satisfied inflates the composite score.
**How to avoid:** (a) Keep original Ollama eval tasks unchanged (baseline stability). (b) Report memory-recall scores separately. (c) Only combine into composite score after both components are stable. (d) Use the existing SHA-256 chain integrity to prove task prompts weren't modified.
**Warning signs:** Score jumps from 66% to 90% overnight without any actual learning.

### Pitfall 6: Branch Prefix Mismatch in Cross-Branch Queries
**What goes wrong:** Cross-branch query can't find connections because fact node IDs don't follow the branch-prefix convention.
**Why it happens:** The FactExtractor uses prefixes like `health.medication.*`, `ops.schedule.*`, etc. But not all facts follow this pattern. Custom facts added manually might have arbitrary IDs.
**How to avoid:** When creating cross-branch edges, use the `branch` column on the `records` table (which is always set by BranchClassifier), not the node_id prefix. Link KG nodes to their source records to inherit branch classification.
**Warning signs:** Cross-branch queries returning empty connections despite facts existing in multiple branches.

## Code Examples

### Example 1: Schema Migration for Temporal Metadata

```python
# Source: follows pattern from knowledge/graph.py _ensure_schema()
def migrate_temporal_metadata(db: sqlite3.Connection, write_lock: threading.Lock) -> None:
    """Add temporal columns to kg_nodes. Idempotent."""
    with write_lock:
        # Check if columns already exist
        cols = {row[1] for row in db.execute("PRAGMA table_info(kg_nodes)").fetchall()}

        if "temporal_type" not in cols:
            db.execute(
                "ALTER TABLE kg_nodes ADD COLUMN temporal_type TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "expires_at" not in cols:
            db.execute(
                "ALTER TABLE kg_nodes ADD COLUMN expires_at TEXT DEFAULT NULL"
            )

        # Create indexes (idempotent via IF NOT EXISTS)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_temporal ON kg_nodes(temporal_type)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_expires ON kg_nodes(expires_at) WHERE expires_at IS NOT NULL"
        )

        # Bump schema version
        db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (3)")
        db.commit()
```

### Example 2: Temporal Classification of Facts

```python
# Source: extends FactExtractor pattern from knowledge/facts.py
import re
from datetime import datetime, timedelta, UTC

PERMANENT_PREFIXES = {"family.member", "preference", "ops.location", "finance.income"}
TIME_SENSITIVE_PREFIXES = {"ops.schedule"}

_TEMPORAL_PATTERNS = [
    re.compile(r"\b(expires?|due|until|by|before|valid until)\b", re.IGNORECASE),
    re.compile(r"\b(today|tomorrow|this week|next week|this month|next month)\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]

def classify_temporal(node_id: str, label: str) -> tuple[str, str | None]:
    """Classify a fact as permanent or time-sensitive.

    Returns (temporal_type, expires_at) where expires_at is ISO string or None.
    """
    # Check prefix-based rules first
    for prefix in PERMANENT_PREFIXES:
        if node_id.startswith(prefix):
            return ("permanent", None)

    for prefix in TIME_SENSITIVE_PREFIXES:
        if node_id.startswith(prefix):
            # Default expiration: 30 days from now for schedule items
            expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            return ("time_sensitive", expires)

    # Check label content for temporal indicators
    for pattern in _TEMPORAL_PATTERNS:
        if pattern.search(label):
            expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            return ("time_sensitive", expires)

    return ("unknown", None)
```

### Example 3: Cross-Branch Edge Creation at Ingest Time

```python
# Source: extends EnrichedIngestPipeline._extract_facts() pattern
def create_cross_branch_edges(
    kg: KnowledgeGraph,
    new_fact_id: str,
    new_fact_branch_prefix: str,
    record_id: str,
) -> int:
    """After extracting a fact, check for related facts in other branches.

    Uses node_id prefix to determine branch. Creates 'cross_branch_related'
    edges between facts that share keywords in their labels.

    Returns number of cross-branch edges created.
    """
    # Get the new fact's label
    node = kg.get_node(new_fact_id)
    if node is None:
        return 0

    new_label = str(node.get("label", "")).lower()
    new_branch = new_fact_branch_prefix.split(".")[0] if "." in new_fact_branch_prefix else ""

    # Find potentially related nodes in other branches
    # Use SQL to search for label overlap (more efficient than loading full graph)
    keywords = [w for w in new_label.split() if len(w) > 3]
    if not keywords:
        return 0

    edges_created = 0
    for keyword in keywords[:5]:  # Cap keyword search
        rows = kg.db.execute(
            """SELECT node_id, label FROM kg_nodes
               WHERE label LIKE ? AND node_id NOT LIKE ?
               LIMIT 10""",
            (f"%{keyword}%", f"{new_branch}.%"),
        ).fetchall()

        for row in rows:
            target_id = row[0]
            if target_id == new_fact_id:
                continue
            was_new = kg.add_edge(
                source_id=new_fact_id,
                target_id=target_id,
                relation="cross_branch_related",
                confidence=0.4,
                source_record=record_id,
            )
            if was_new:
                edges_created += 1

    return edges_created
```

### Example 4: Memory-Recall Golden Task Evaluation

```python
# Source: extends growth_tracker.py pattern
from dataclasses import dataclass

@dataclass
class MemoryRecallTask:
    task_id: str
    query: str
    must_find_branches: list[str]
    min_results: int
    must_include_in_results: list[str]

@dataclass
class MemoryRecallResult:
    task_id: str
    query: str
    results_found: int
    branches_found: list[str]
    branch_coverage: float
    keyword_coverage: float
    overall_score: float

def evaluate_memory_recall(
    task: MemoryRecallTask,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
) -> MemoryRecallResult:
    """Evaluate a single memory-recall golden task."""
    from jarvis_engine.memory.search import hybrid_search

    query_embedding = embed_service.embed_query(task.query)
    results = hybrid_search(engine, task.query, query_embedding, k=10)

    # Score components
    has_results = 1.0 if len(results) >= task.min_results else (len(results) / max(task.min_results, 1))

    result_branches = {r.get("branch", "") for r in results}
    expected = set(task.must_find_branches)
    branch_coverage = len(expected & result_branches) / max(len(expected), 1) if expected else 1.0

    combined_text = " ".join(r.get("summary", "") for r in results).lower()
    must_include = task.must_include_in_results
    keyword_hits = sum(1 for kw in must_include if kw.lower() in combined_text)
    keyword_coverage = keyword_hits / max(len(must_include), 1) if must_include else 1.0

    overall = (has_results * 0.3) + (branch_coverage * 0.3) + (keyword_coverage * 0.4)

    return MemoryRecallResult(
        task_id=task.task_id,
        query=task.query,
        results_found=len(results),
        branches_found=list(result_branches),
        branch_coverage=branch_coverage,
        keyword_coverage=keyword_coverage,
        overall_score=overall,
    )
```

### Example 5: Knowledge Growth Metrics

```python
# Source: extends intelligence_dashboard.py pattern
def capture_knowledge_growth_metrics(
    kg: "KnowledgeGraph",
    engine: "MemoryEngine",
) -> dict:
    """Capture point-in-time knowledge growth metrics.

    Designed to be appended to growth history for trend analysis.
    """
    from jarvis_engine.knowledge.regression import RegressionChecker

    checker = RegressionChecker(kg)
    metrics = checker.capture_metrics()

    # Branch distribution
    branch_counts = {}
    rows = engine._db.execute(
        "SELECT branch, COUNT(*) FROM records GROUP BY branch"
    ).fetchall()
    for row in rows:
        branch_counts[row[0]] = row[1]

    # Temporal distribution
    temporal_counts = {}
    try:
        trows = kg.db.execute(
            "SELECT temporal_type, COUNT(*) FROM kg_nodes GROUP BY temporal_type"
        ).fetchall()
        for row in trows:
            temporal_counts[row[0]] = row[1]
    except Exception:
        pass  # temporal columns may not exist yet

    return {
        "total_records": engine.count_records(),
        "total_facts": metrics["node_count"],
        "total_edges": metrics["edge_count"],
        "locked_facts": metrics["locked_count"],
        "branches_populated": len(branch_counts),
        "branch_distribution": branch_counts,
        "temporal_distribution": temporal_counts,
        "graph_hash": metrics["graph_hash"],
        "captured_at": metrics["captured_at"],
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Keyword-based fact extraction only | Regex patterns + branch prefixes | Phase 2 (current) | Structured facts but limited to 6 pattern domains |
| Ollama-only golden task eval | Ollama eval + SHA-256 chain integrity | Phase 2 (current) | Tamper-resistant eval history but no memory-recall testing |
| No temporal awareness | Schema extension with temporal_type + expires_at | Phase 7 (this phase) | Facts have lifetime awareness |
| Single-branch queries | Cross-branch KG traversal + multi-branch search | Phase 7 (this phase) | Queries can reason across life domains |
| Manual knowledge capture only | Automatic conversation learning + harvesting | Phase 5 (harvesting) + Phase 7 (conversations) | Knowledge accumulates passively from every interaction |

**What already works well and should not be modified:**
- `EnrichedIngestPipeline` -- the entire ingest-embed-classify-store-extract pipeline is solid
- `KnowledgeGraph.add_fact()` -- lock-aware, dedup-aware, contradiction-quarantining
- `growth_tracker.py` -- SHA-256 chain integrity, history append, summarize
- `hybrid_search()` -- RRF combining FTS5 + semantic + recency
- `BranchClassifier` -- cosine similarity classification into 9 branches

## Open Questions

1. **How should conversation learning be triggered?**
   - What we know: The `QueryHandler` (task_handlers.py) processes queries via the gateway. This is the natural hook point.
   - What's unclear: Should learning happen synchronously (blocking the response) or asynchronously (background thread)? Synchronous is simpler but adds latency.
   - Recommendation: Synchronous for v1. The pipeline is fast (embed + insert < 100ms for short content). Move to async only if latency becomes noticeable.

2. **Should cross-branch edge creation happen at ingest time or query time?**
   - What we know: Ingest-time is proactive (edges exist before queries), query-time is lazy (only creates edges when needed).
   - What's unclear: How expensive is ingest-time cross-branch matching? With thousands of nodes, the LIKE queries could slow down ingestion.
   - Recommendation: Ingest-time for v1 with a cap (max 5 cross-branch edges per fact). The KG will be small enough that this is fast. Add a flag to disable if ingestion latency becomes a concern.

3. **How many memory-recall golden tasks should exist initially?**
   - What we know: Currently 3 Ollama eval tasks. Memory-recall tasks test a different capability.
   - What's unclear: Too few tasks means volatile scores; too many means expensive evaluation.
   - Recommendation: Start with 5 memory-recall tasks covering different branches (health, gaming, ops, family, coding). Add more as the knowledge base grows. Keep Ollama eval tasks separate from memory-recall tasks.

4. **Should expired facts be deleted or just flagged?**
   - What we know: The anti-regression principle ("never forget") suggests flagging, not deletion.
   - What's unclear: Will flagged-but-retained expired facts pollute search results?
   - Recommendation: Flag as `temporal_type='expired'` but keep in KG. Exclude expired facts from search results via WHERE clause. Owner can manually delete if desired.

## Sources

### Primary (HIGH confidence)
- `engine/src/jarvis_engine/knowledge/graph.py` -- KnowledgeGraph schema, add_fact, add_edge, to_networkx
- `engine/src/jarvis_engine/memory/ingest.py` -- EnrichedIngestPipeline with fact extraction side-effect
- `engine/src/jarvis_engine/memory/engine.py` -- MemoryEngine schema, insert_record, search_fts, search_vec
- `engine/src/jarvis_engine/memory/search.py` -- hybrid_search with RRF
- `engine/src/jarvis_engine/growth_tracker.py` -- GoldenTask, run_eval, append_history, validate_history_chain
- `engine/src/jarvis_engine/intelligence_dashboard.py` -- build_intelligence_dashboard, ETAs, achievements
- `engine/src/jarvis_engine/knowledge/facts.py` -- FactExtractor with domain-specific regex patterns
- `engine/src/jarvis_engine/knowledge/locks.py` -- FactLockManager auto-lock thresholds
- `engine/src/jarvis_engine/knowledge/regression.py` -- RegressionChecker with WL hash
- `engine/src/jarvis_engine/memory/classify.py` -- BranchClassifier with 9 branches
- `engine/src/jarvis_engine/memory/embeddings.py` -- EmbeddingService with nomic-embed-text-v1.5
- `engine/src/jarvis_engine/harvesting/harvester.py` -- KnowledgeHarvester pattern for pipeline integration
- `engine/src/jarvis_engine/harvesting/session_ingestors.py` -- ClaudeCodeIngestor, CodexIngestor patterns
- `engine/src/jarvis_engine/app.py` -- create_app() DI composition root
- `engine/src/jarvis_engine/handlers/ops_handlers.py` -- Handler pattern (GrowthEvalHandler, etc.)
- `engine/src/jarvis_engine/handlers/knowledge_handlers.py` -- Knowledge handler patterns

### Secondary (MEDIUM confidence)
- NetworkX documentation on graph traversal (shortest_path, neighbors, subgraph) -- well-established stable APIs
- SQLite ALTER TABLE documentation -- standard behavior for adding columns with defaults

### Tertiary (LOW confidence)
- None. All findings are based on direct codebase analysis.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all dependencies already installed and verified working in prior phases
- Architecture: HIGH -- all patterns follow existing codebase conventions (Command Bus, handler pattern, pipeline composition)
- Cross-branch reasoning: MEDIUM -- the approach is sound but the efficiency of LIKE-based cross-branch matching at scale is uncertain
- Temporal metadata: HIGH -- straightforward schema migration following established patterns
- Golden task evolution: HIGH -- extends existing growth_tracker.py with same patterns
- Pitfalls: HIGH -- identified from direct codebase analysis of existing edge cases

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (30 days -- stable domain, no external dependencies changing)
