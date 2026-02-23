# Phase 2: Knowledge Graph and Anti-Regression - Research

**Researched:** 2026-02-22
**Domain:** NetworkX knowledge graph with SQLite persistence, fact extraction from text, immutable fact locks with contradiction quarantine, signed snapshot regression verification
**Confidence:** HIGH

## Summary

Phase 2 builds a knowledge graph layer on top of the Phase 1 SQLite memory engine. The core work is: (1) extracting structured facts (subject-predicate-object triples) from ingested content and storing them as nodes and edges in a NetworkX DiGraph backed by SQLite tables, (2) implementing a fact lifecycle system where facts progress from provisional to confirmed to locked status based on confidence, source count, and owner confirmation -- with locked facts protected from silent overwriting, (3) quarantining contradictions for owner review via CLI, and (4) building regression verification that compares knowledge graph integrity between signed snapshots.

The existing codebase already has significant scaffolding for this phase. The `facts` table in `engine.py` has `key`, `value`, `confidence`, `locked`, `sources`, and `history` columns. The `brain_memory.py` module has `_extract_fact_candidates()` (regex-based fact extraction), `_update_fact_store()` (conflict detection with confidence promotion), and `brain_regression_report()` (basic health checks). The `memory_snapshots.py` module has HMAC-signed snapshot creation and verification. Phase 2 replaces the flat JSON fact store with a proper graph structure, adds immutable lock enforcement, formalizes contradiction quarantine with owner resolution, and upgrades regression detection to compare between snapshots rather than just reporting current state.

NetworkX 3.6.1 is the standard Python graph library (pure Python, zero required dependencies, already compatible with Python 3.11+). For persistence, the recommended approach is NOT to use NetworkDisk or pickle -- instead, store nodes and edges in dedicated SQLite tables and reconstruct the NetworkX DiGraph on load. This keeps the SQLite database as the single source of truth (consistent with Phase 1), avoids pickling security concerns, and allows SQL queries against graph data without loading the full graph into memory.

**Primary recommendation:** Store knowledge graph as SQLite tables (`kg_nodes`, `kg_edges`, `kg_contradictions`), reconstruct NetworkX DiGraph on demand for graph operations (traversal, hashing), and extend the existing ingestion pipeline to extract facts during ingest.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| KNOW-01 | Facts extracted from ingested content stored in knowledge graph (NetworkX backed by SQLite) | NetworkX 3.6.1 DiGraph with SQLite persistence via `kg_nodes` and `kg_edges` tables. Fact extraction via regex patterns (extending existing `_extract_fact_candidates`) plus embedding-based entity extraction. Integrated into `EnrichedIngestPipeline` as a new pipeline step after classification. |
| KNOW-02 | Facts that reach locked status cannot be overwritten by lower-confidence information | `locked` column already exists in `facts` table. Enforcement logic in fact update path: if `locked=1`, reject any update that changes `value`. Locked status reached when: confidence >= 0.9 AND sources >= 3, OR owner explicitly confirms via CLI command. |
| KNOW-03 | Incoming facts contradicting locked facts quarantined as "pending contradiction" for owner review | `kg_contradictions` table stores quarantined contradictions with `status='pending'`. Owner resolves via `jarvis-engine contradiction-resolve` CLI command (accept-new, keep-old, merge). Resolution updates the graph and logs the decision. |
| KNOW-04 | Regression report compares knowledge counts and fact integrity between signed snapshots | Extend `memory_snapshots.py` to include knowledge graph metrics in snapshot metadata (node count, edge count, locked fact count, WL graph hash). Regression report loads two snapshots and diffs these metrics, reporting any decrease in counts or hash change without corresponding addition. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| NetworkX | >=3.6.1 | In-memory graph operations, traversal, hashing | Standard Python graph library. Pure Python, zero deps. DiGraph for directed fact relationships. `weisfeiler_lehman_graph_hash` for integrity verification. |
| SQLite (stdlib) | >=3.41 | Graph node/edge persistence | Already the primary data store from Phase 1. Same database file. ACID transactions protect graph consistency. |
| Python re (stdlib) | -- | Regex-based fact extraction | Extends existing `_extract_fact_candidates` pattern. Lightweight, no external NLP dependency needed for structured fact patterns. |
| hashlib (stdlib) | -- | Fact content hashing | SHA-256 for fact deduplication and integrity verification. Already used throughout codebase. |
| hmac (stdlib) | -- | Signed snapshot verification | Already used in `memory_snapshots.py`. Extends to include graph metrics. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json (stdlib) | -- | Serialize node/edge attributes for SQLite | Store complex attributes (sources list, history) as JSON text in SQLite columns |
| struct (stdlib) | -- | Embedding serialization | Already in use from Phase 1 for sqlite-vec |
| datetime (stdlib) | -- | Temporal metadata on facts | Track when facts were created, updated, locked |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| NetworkX + SQLite tables | NetworkDisk | NetworkDisk provides direct SQLite-backed graph, but is immature (v0.x), adds a dependency, and prevents SQL queries against raw graph data |
| NetworkX + SQLite tables | Pickle/GraphML files | No SQL queryability, pickle has security concerns, GraphML files are slow for large graphs |
| Regex fact extraction | spaCy NER + dependency parsing | spaCy adds ~200MB dependency, requires model download. Overkill for structured fact extraction where patterns are domain-specific. Could be added in future phase. |
| Regex fact extraction | LLM-based extraction | Requires API calls per ingest (cost, latency). Better suited for Phase 5 (Knowledge Harvesting) where cloud APIs are already used. |
| Custom SQLite graph tables | Neo4j / graph database | Separate process, separate storage. Violates local-first single-file principle. Massive overkill for single-user knowledge graph. |

**Installation:**
```bash
# NetworkX is the only new dependency
pip install networkx>=3.6.1
```

**Note:** NetworkX 3.6.1 has no required dependencies (numpy, scipy, matplotlib are all optional). It is pure Python and installs in seconds.

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
+-- memory/
|   +-- engine.py              # Extend schema: kg_nodes, kg_edges, kg_contradictions tables
|   +-- ingest.py              # Extend pipeline: fact extraction step after classify
+-- knowledge/                 # NEW: Knowledge graph subsystem
|   +-- __init__.py
|   +-- graph.py               # KnowledgeGraph class: SQLite <-> NetworkX bridge
|   +-- facts.py               # FactExtractor: regex patterns for structured facts
|   +-- locks.py               # FactLockManager: immutability enforcement
|   +-- contradictions.py      # ContradictionManager: quarantine and resolution
|   +-- regression.py          # RegressionChecker: snapshot comparison
+-- commands/
|   +-- knowledge_commands.py  # NEW: Command dataclasses for graph operations
+-- handlers/
|   +-- knowledge_handlers.py  # NEW: Handler classes for knowledge commands
```

### Pattern 1: SQLite-Backed Knowledge Graph
**What:** Store graph nodes and edges in SQLite tables. Reconstruct NetworkX DiGraph on demand for graph operations. The SQLite tables are the source of truth; NetworkX is the computation engine.
**When to use:** All knowledge graph operations -- queries, traversals, integrity checks.
**Example:**
```python
# knowledge/graph.py
import json
import networkx as nx
from pathlib import Path

class KnowledgeGraph:
    """SQLite-persistent knowledge graph with NetworkX computation layer."""

    def __init__(self, db: "MemoryEngine") -> None:
        self._db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create kg tables if they don't exist."""
        self._db._db.executescript("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(node_type);
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_locked ON kg_nodes(locked);

            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_record TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (source_id) REFERENCES kg_nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES kg_nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_edges_unique
                ON kg_edges(source_id, target_id, relation);

            CREATE TABLE IF NOT EXISTS kg_contradictions (
                contradiction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                existing_value TEXT NOT NULL,
                incoming_value TEXT NOT NULL,
                existing_confidence REAL NOT NULL,
                incoming_confidence REAL NOT NULL,
                incoming_source TEXT DEFAULT NULL,
                record_id TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                resolved_at TEXT DEFAULT NULL,
                resolution TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (node_id) REFERENCES kg_nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_kg_contradictions_status
                ON kg_contradictions(status);
            CREATE INDEX IF NOT EXISTS idx_kg_contradictions_node
                ON kg_contradictions(node_id);
        """)
        self._db._db.commit()

    def to_networkx(self) -> nx.DiGraph:
        """Reconstruct full NetworkX DiGraph from SQLite tables."""
        G = nx.DiGraph()
        # Load nodes
        cur = self._db._db.execute(
            "SELECT node_id, label, node_type, confidence, locked FROM kg_nodes"
        )
        for row in cur.fetchall():
            G.add_node(row[0], label=row[1], node_type=row[2],
                       confidence=row[3], locked=bool(row[4]))
        # Load edges
        cur = self._db._db.execute(
            "SELECT source_id, target_id, relation, confidence FROM kg_edges"
        )
        for row in cur.fetchall():
            G.add_edge(row[0], row[1], relation=row[2], confidence=row[3])
        return G

    def add_fact(self, node_id: str, label: str, confidence: float,
                 source_record: str = "") -> bool:
        """Add or update a fact node. Returns False if blocked by lock."""
        # Check if node exists and is locked
        existing = self._db._db.execute(
            "SELECT locked, label, confidence FROM kg_nodes WHERE node_id = ?",
            (node_id,)
        ).fetchone()

        if existing and existing[0]:  # locked
            if label != existing[1]:  # contradiction
                self._quarantine_contradiction(
                    node_id, existing[1], label,
                    existing[2], confidence, source_record
                )
                return False
            return True  # Same value, no-op

        if existing:
            # Update existing unlocked node
            sources = json.loads(
                self._db._db.execute(
                    "SELECT sources FROM kg_nodes WHERE node_id = ?",
                    (node_id,)
                ).fetchone()[0]
            )
            if source_record and source_record not in sources:
                sources.append(source_record)
            self._db._db.execute(
                """UPDATE kg_nodes
                   SET label = ?, confidence = ?, sources = ?,
                       updated_at = datetime('now')
                   WHERE node_id = ?""",
                (label, max(confidence, existing[2]),
                 json.dumps(sources[-50:]), node_id)
            )
        else:
            sources = [source_record] if source_record else []
            self._db._db.execute(
                """INSERT INTO kg_nodes
                   (node_id, label, confidence, sources)
                   VALUES (?, ?, ?, ?)""",
                (node_id, label, confidence, json.dumps(sources))
            )
        self._db._db.commit()
        return True
```

### Pattern 2: Fact Extraction in Ingestion Pipeline
**What:** After the existing pipeline steps (sanitize, dedup, chunk, embed, classify), extract structured facts from the content and insert them into the knowledge graph.
**When to use:** Every ingest operation. Facts are extracted as a side effect of memory ingestion.
**Example:**
```python
# knowledge/facts.py
import re
from typing import NamedTuple

class FactTriple(NamedTuple):
    subject: str       # node_id for subject
    predicate: str     # edge relation
    object_val: str    # node_id or value for object
    confidence: float  # extraction confidence

class FactExtractor:
    """Extract structured facts from text using domain-specific patterns."""

    # Pattern categories -- extend as Jarvis learns new domains
    PATTERNS = [
        # Health: "takes medication X", "prescribed X"
        (re.compile(r"(?:takes?|prescribed?|on)\s+([\w\s]+?)\s+(?:for|daily|twice|morning|evening)",
                     re.IGNORECASE),
         "health.medication", "takes", 0.75),
        # Schedule: "meeting at X", "appointment on X"
        (re.compile(r"(?:meeting|appointment|event)\s+(?:at|on|with)\s+(.+?)(?:\.|$)",
                     re.IGNORECASE),
         "ops.schedule", "has_event", 0.65),
        # Preference: "prefers X", "likes X", "favorite X"
        (re.compile(r"(?:prefers?|likes?|favorite)\s+(.+?)(?:\.|,|$)",
                     re.IGNORECASE),
         "preference", "prefers", 0.70),
        # Family: "son/daughter named X", "wife/husband X"
        (re.compile(r"(?:son|daughter|wife|husband|spouse|child)\s+(?:named?\s+)?(\w+)",
                     re.IGNORECASE),
         "family.member", "family_relation", 0.80),
    ]

    def extract(self, text: str, source: str, branch: str) -> list[FactTriple]:
        """Extract fact triples from text content."""
        facts: list[FactTriple] = []
        for pattern, subject_prefix, predicate, base_conf in self.PATTERNS:
            for match in pattern.finditer(text):
                object_val = match.group(1).strip()
                if len(object_val) < 2 or len(object_val) > 100:
                    continue
                subject = f"{subject_prefix}.{_normalize(object_val)}"
                facts.append(FactTriple(
                    subject=subject,
                    predicate=predicate,
                    object_val=object_val,
                    confidence=base_conf,
                ))
        return facts[:10]  # Cap per-content extraction
```

### Pattern 3: Immutable Fact Locks with Contradiction Quarantine
**What:** Facts progress through a lifecycle: provisional (confidence < 0.8) -> confirmed (>= 0.8 or multiple sources) -> locked (>= 0.9 AND sources >= 3, or owner-confirmed). Locked facts cannot be silently overwritten. Contradictions are quarantined.
**When to use:** Every fact update operation. Lock enforcement is checked before any write to `kg_nodes`.
**Example:**
```python
# knowledge/locks.py
class FactLockManager:
    """Manages fact lifecycle: provisional -> confirmed -> locked."""

    LOCK_THRESHOLD_CONFIDENCE = 0.9
    LOCK_THRESHOLD_SOURCES = 3

    def should_auto_lock(self, node: dict) -> bool:
        """Check if a fact should be automatically locked."""
        confidence = node.get("confidence", 0.0)
        sources = json.loads(node.get("sources", "[]"))
        return (confidence >= self.LOCK_THRESHOLD_CONFIDENCE
                and len(sources) >= self.LOCK_THRESHOLD_SOURCES)

    def lock_fact(self, db, node_id: str, locked_by: str = "auto") -> bool:
        """Set a fact as locked. Returns True if successful."""
        db.execute(
            """UPDATE kg_nodes
               SET locked = 1, locked_at = datetime('now'), locked_by = ?
               WHERE node_id = ? AND locked = 0""",
            (locked_by, node_id)
        )
        db.commit()
        return db.total_changes > 0

    def owner_confirm_lock(self, db, node_id: str) -> bool:
        """Owner explicitly locks a fact regardless of thresholds."""
        return self.lock_fact(db, node_id, locked_by="owner")
```

### Pattern 4: Regression Verification via Snapshot Comparison
**What:** Extend signed snapshots to include knowledge graph metrics. Regression checker loads two snapshots and compares node counts, edge counts, locked fact counts, and the Weisfeiler-Lehman graph hash.
**When to use:** Scheduled maintenance (nightly), on-demand via CLI, after any bulk operation.
**Example:**
```python
# knowledge/regression.py
import networkx as nx

class RegressionChecker:
    """Compare knowledge graph state between snapshots."""

    def capture_metrics(self, kg: KnowledgeGraph) -> dict:
        """Capture current knowledge graph metrics for snapshot."""
        G = kg.to_networkx()
        node_count = G.number_of_nodes()
        edge_count = G.number_of_edges()
        locked_count = sum(
            1 for _, d in G.nodes(data=True) if d.get("locked")
        )
        # Weisfeiler-Lehman hash captures structural integrity
        graph_hash = nx.weisfeiler_lehman_graph_hash(
            G, node_attr="label", edge_attr="relation"
        )
        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "locked_count": locked_count,
            "graph_hash": graph_hash,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    def compare(self, previous: dict, current: dict) -> dict:
        """Compare two snapshots and report discrepancies."""
        report = {
            "status": "pass",
            "discrepancies": [],
            "previous": previous,
            "current": current,
        }
        # Check for knowledge loss (node/edge count decrease)
        if current["node_count"] < previous["node_count"]:
            report["discrepancies"].append({
                "type": "node_loss",
                "previous": previous["node_count"],
                "current": current["node_count"],
                "delta": current["node_count"] - previous["node_count"],
            })
        if current["edge_count"] < previous["edge_count"]:
            report["discrepancies"].append({
                "type": "edge_loss",
                "previous": previous["edge_count"],
                "current": current["edge_count"],
                "delta": current["edge_count"] - previous["edge_count"],
            })
        # Check locked facts haven't decreased
        if current["locked_count"] < previous["locked_count"]:
            report["discrepancies"].append({
                "type": "locked_fact_loss",
                "previous": previous["locked_count"],
                "current": current["locked_count"],
                "severity": "critical",
            })
        # Set overall status
        if any(d.get("severity") == "critical" for d in report["discrepancies"]):
            report["status"] = "fail"
        elif report["discrepancies"]:
            report["status"] = "warn"
        return report
```

### Pattern 5: Command Bus Integration
**What:** New knowledge graph commands follow the same Command Bus pattern established in Phase 1.
**When to use:** All CLI interactions with the knowledge graph.
**Example:**
```python
# commands/knowledge_commands.py
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class KnowledgeStatusCommand:
    as_json: bool = False

@dataclass
class KnowledgeStatusResult:
    node_count: int = 0
    edge_count: int = 0
    locked_count: int = 0
    pending_contradictions: int = 0
    graph_hash: str = ""

@dataclass(frozen=True)
class ContradictionListCommand:
    status: str = "pending"
    limit: int = 20

@dataclass
class ContradictionListResult:
    contradictions: list[dict] = field(default_factory=list)

@dataclass(frozen=True)
class ContradictionResolveCommand:
    contradiction_id: int
    resolution: str  # "accept_new", "keep_old", "merge"
    merge_value: str = ""  # Only used when resolution is "merge"

@dataclass
class ContradictionResolveResult:
    success: bool = False
    node_id: str = ""
    resolution: str = ""
    message: str = ""

@dataclass(frozen=True)
class FactLockCommand:
    node_id: str
    action: str = "lock"  # "lock" or "unlock"

@dataclass
class FactLockResult:
    success: bool = False
    node_id: str = ""
    locked: bool = False

@dataclass(frozen=True)
class KnowledgeRegressionCommand:
    snapshot_path: str = ""
    as_json: bool = False

@dataclass
class KnowledgeRegressionResult:
    report: dict[str, Any] = field(default_factory=dict)
```

### Anti-Patterns to Avoid
- **Full graph in memory permanently:** Do NOT keep the NetworkX DiGraph loaded at all times. Build it on demand for queries, let it be garbage-collected. SQLite is the source of truth.
- **Pickle for persistence:** Do NOT use `nx.write_gpickle()`. Pickle is insecure and does not support concurrent access or partial queries.
- **NLP heavyweight for fact extraction:** Do NOT add spaCy or transformer models for entity extraction in this phase. Domain-specific regex patterns are sufficient for Jarvis's structured domains (health, ops, family, etc.). LLM extraction comes in Phase 5.
- **Separate graph database:** Do NOT use Neo4j, ArangoDB, or any external graph DB. The knowledge graph lives in the same SQLite file as all other Jarvis data.
- **Silent conflict resolution:** Do NOT auto-resolve contradictions against locked facts. The entire point of KNOW-03 is that contradictions to locked facts are quarantined for human review.
- **Modifying the `facts` table directly:** The existing `facts` table in `engine.py` schema is the Phase 1 legacy structure. Phase 2 creates new `kg_nodes`, `kg_edges`, `kg_contradictions` tables. The old `facts` table should be migrated into the new structure, not extended.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Graph traversal and shortest path | Custom BFS/DFS over SQLite rows | NetworkX DiGraph algorithms | Hundreds of tested graph algorithms. `nx.descendants()`, `nx.ancestors()`, `nx.shortest_path()` |
| Graph structural hashing | Custom hash over nodes/edges | `nx.weisfeiler_lehman_graph_hash()` | Proven isomorphism-aware hashing. Accepts `node_attr` and `edge_attr` parameters. |
| Graph serialization for snapshots | Custom JSON builder | `nx.node_link_data()` / `nx.node_link_graph()` | Standard JSON format. Preserves all node/edge attributes. Roundtrips cleanly. |
| HMAC-signed snapshots | Custom signing | Existing `memory_snapshots.py` | Already implemented with `create_signed_snapshot()` and `verify_signed_snapshot()`. Just extend with graph metrics. |
| Fact deduplication | Custom hash index | SQLite UNIQUE constraint on `kg_edges(source_id, target_id, relation)` | Database-enforced, crash-safe |
| Contradiction tracking | In-memory list | SQLite `kg_contradictions` table with `status` column | Persistent across restarts, queryable, auditable |

**Key insight:** NetworkX handles all graph computation (traversal, hashing, serialization). SQLite handles all persistence and concurrency. The bridge between them is simple: load from SQLite -> compute in NetworkX -> write back to SQLite. This separation keeps each tool in its strength zone.

## Common Pitfalls

### Pitfall 1: Graph-Database Impedance Mismatch
**What goes wrong:** Trying to make SQLite act like a graph database leads to complex recursive CTEs, slow multi-hop queries, or inconsistent state between SQLite and NetworkX.
**Why it happens:** SQLite is a relational database, not a graph database. Multi-hop traversals require recursive queries or multiple round trips.
**How to avoid:** Use SQLite ONLY for storage/persistence. For any graph operation requiring traversal (shortest path, connected components, ancestors/descendants), load the subgraph into NetworkX first. Keep the bridge layer clean and explicit.
**Warning signs:** Writing SQL with 3+ JOIN levels, or implementing custom BFS in SQL.

### Pitfall 2: Race Conditions in Lock Enforcement
**What goes wrong:** A fact gets overwritten between the "check if locked" read and the "update value" write, violating the immutability guarantee.
**Why it happens:** TOCTOU (time-of-check-time-of-use) race condition in concurrent access from daemon + API + CLI.
**How to avoid:** Use SQLite's `UPDATE ... WHERE locked = 0` pattern -- the WHERE clause and the SET happen atomically. Also use the existing `_write_lock` from `MemoryEngine` for all knowledge graph writes.
**Warning signs:** Facts that were locked somehow have different values, or contradictions not being quarantined.

### Pitfall 3: Stale NetworkX Graph After SQLite Writes
**What goes wrong:** Code loads a NetworkX graph, another process writes to SQLite, the NetworkX graph is now stale. Computations on the stale graph produce incorrect results.
**Why it happens:** NetworkX DiGraph is an in-memory snapshot. It does not auto-refresh from SQLite.
**How to avoid:** Never cache the NetworkX graph across operations. Always call `to_networkx()` fresh for each operation that needs it. The overhead is minimal for graphs under 100K nodes (milliseconds).
**Warning signs:** Graph hash changes without corresponding node/edge additions, or regression reports showing phantom changes.

### Pitfall 4: Unbounded Fact Extraction
**What goes wrong:** Aggressive regex patterns extract dozens of "facts" from every piece of content, flooding the knowledge graph with low-quality noise.
**Why it happens:** Overly broad patterns, no confidence threshold, no extraction cap.
**How to avoid:** Cap extractions per content (max 10). Set minimum confidence threshold (>= 0.5). Use narrow, domain-specific patterns rather than generic NER. Quality over quantity -- better to extract 2 high-confidence facts than 20 low-confidence ones.
**Warning signs:** Knowledge graph growing faster than memory records, many nodes with confidence < 0.5.

### Pitfall 5: Migrating Old Facts Table Without Dedup
**What goes wrong:** The existing `facts` table (from Phase 1 schema) and `brain_memory.py` JSON facts get imported into `kg_nodes` with duplicates, creating incorrect source counts and inflated confidence.
**Why it happens:** Same fact exists in both the old JSON store and the SQLite facts table from Phase 1 schema initialization.
**How to avoid:** Deduplicate during migration using `node_id` as the unique key. The migration should be a one-time operation that reads from old stores, deduplicates, and writes to `kg_nodes`/`kg_edges`. Use INSERT OR IGNORE.
**Warning signs:** Unexpectedly high node counts after migration, facts with duplicate sources lists.

### Pitfall 6: Regression Report Without Baseline
**What goes wrong:** The first regression report has nothing to compare against and either crashes or reports everything as "new" (not useful).
**Why it happens:** No previous snapshot exists at system initialization.
**How to avoid:** Handle the "no previous snapshot" case explicitly: the first run creates a baseline snapshot and reports "baseline established" rather than attempting comparison. Store the knowledge graph metrics in the existing snapshot metadata JSON.
**Warning signs:** NullPointerError or KeyError when running regression on fresh install.

## Code Examples

Verified patterns from official sources:

### NetworkX DiGraph for Knowledge Graph
```python
# Source: NetworkX 3.6.1 official tutorial
import networkx as nx

# Create directed graph for fact relationships
G = nx.DiGraph()

# Add fact nodes with attributes
G.add_node("health.medication.metformin",
           label="metformin",
           node_type="medication",
           confidence=0.92,
           locked=True)

G.add_node("health.condition.diabetes",
           label="type 2 diabetes",
           node_type="condition",
           confidence=0.88,
           locked=False)

# Add relationship edge
G.add_edge("health.medication.metformin",
           "health.condition.diabetes",
           relation="treats",
           confidence=0.85)

# Query: what does metformin treat?
for _, target, data in G.out_edges("health.medication.metformin", data=True):
    if data["relation"] == "treats":
        print(f"Metformin treats: {G.nodes[target]['label']}")

# Graph metrics for regression
print(f"Nodes: {G.number_of_nodes()}")
print(f"Edges: {G.number_of_edges()}")
```

### Weisfeiler-Lehman Graph Hash for Integrity
```python
# Source: NetworkX 3.6.1 algorithms/graph_hashing docs
import networkx as nx

G = nx.DiGraph()
G.add_node("fact1", label="metformin")
G.add_node("fact2", label="diabetes")
G.add_edge("fact1", "fact2", relation="treats")

# Hash includes node labels and edge relations
graph_hash = nx.weisfeiler_lehman_graph_hash(
    G,
    node_attr="label",
    edge_attr="relation",
    iterations=3,
    digest_size=16
)
# Returns 32-char hex string (2 * digest_size)
# Isomorphic graphs with same attributes -> same hash
# Any structural or attribute change -> different hash
```

### node_link_data for Snapshot Serialization
```python
# Source: NetworkX 3.6.1 readwrite/json_graph docs
import json
import networkx as nx

G = nx.DiGraph()
G.add_node("fact1", label="metformin", confidence=0.9, locked=True)
G.add_node("fact2", label="diabetes", confidence=0.85, locked=False)
G.add_edge("fact1", "fact2", relation="treats", confidence=0.85)

# Serialize to JSON-compatible dict
data = nx.node_link_data(G)
json_str = json.dumps(data, indent=2)
# Output: {"directed": true, "multigraph": false, "graph": {},
#          "nodes": [...], "edges": [...]}

# Deserialize back to graph
G2 = nx.node_link_graph(data)
assert G2.number_of_nodes() == G.number_of_nodes()
```

### SQLite Tables for Graph Persistence
```python
# Pattern: Store nodes/edges in SQLite, reconstruct NetworkX on demand
import sqlite3
import json

def save_node(db, node_id, label, node_type, confidence, sources):
    db.execute(
        """INSERT OR REPLACE INTO kg_nodes
           (node_id, label, node_type, confidence, sources, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (node_id, label, node_type, confidence, json.dumps(sources))
    )
    db.commit()

def save_edge(db, source_id, target_id, relation, confidence, source_record):
    db.execute(
        """INSERT OR IGNORE INTO kg_edges
           (source_id, target_id, relation, confidence, source_record)
           VALUES (?, ?, ?, ?, ?)""",
        (source_id, target_id, relation, confidence, source_record)
    )
    db.commit()

def load_graph(db) -> nx.DiGraph:
    G = nx.DiGraph()
    for row in db.execute("SELECT * FROM kg_nodes").fetchall():
        G.add_node(row["node_id"], label=row["label"],
                   node_type=row["node_type"],
                   confidence=row["confidence"],
                   locked=bool(row["locked"]))
    for row in db.execute("SELECT * FROM kg_edges").fetchall():
        G.add_edge(row["source_id"], row["target_id"],
                   relation=row["relation"],
                   confidence=row["confidence"])
    return G
```

### Atomic Lock Check + Contradiction Quarantine
```python
# Pattern: TOCTOU-safe lock enforcement using SQL WHERE clause
def update_fact_with_lock_check(db, node_id, new_label, new_confidence,
                                 source_record):
    """Update a fact, quarantining if locked and contradicting."""
    # Atomic check: try to update only unlocked nodes
    cur = db.execute(
        """UPDATE kg_nodes
           SET label = ?, confidence = MAX(confidence, ?),
               updated_at = datetime('now')
           WHERE node_id = ? AND locked = 0""",
        (new_label, new_confidence, node_id)
    )
    if cur.rowcount > 0:
        db.commit()
        return "updated"

    # Node either doesn't exist or is locked -- check which
    existing = db.execute(
        "SELECT locked, label, confidence FROM kg_nodes WHERE node_id = ?",
        (node_id,)
    ).fetchone()

    if existing is None:
        # New node -- insert
        db.execute(
            """INSERT INTO kg_nodes (node_id, label, confidence, sources)
               VALUES (?, ?, ?, ?)""",
            (node_id, new_label, new_confidence,
             json.dumps([source_record] if source_record else []))
        )
        db.commit()
        return "inserted"

    # Node is locked -- quarantine if contradicting
    if existing["label"] != new_label:
        db.execute(
            """INSERT INTO kg_contradictions
               (node_id, existing_value, incoming_value,
                existing_confidence, incoming_confidence,
                incoming_source, record_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (node_id, existing["label"], new_label,
             existing["confidence"], new_confidence,
             "ingest", source_record)
        )
        db.commit()
        return "quarantined"

    return "no_change"  # Same value, locked, fine
```

## State of the Art

| Old Approach (Current) | Phase 2 Approach | Impact |
|------------------------|------------------|--------|
| JSON flat-file facts store (`brain_memory.py` `_save_facts`) | SQLite `kg_nodes` + `kg_edges` tables | ACID transactions, queryable, concurrent access |
| Keyword-regex fact extraction (`_extract_fact_candidates`) | Extended regex FactExtractor with domain patterns + confidence scores | Structured triples with provenance tracking |
| Confidence-based auto-promote on conflict | Immutable locks + quarantine for locked fact contradictions | Owner control, no silent overwrites |
| `brain_regression_report()` -- snapshot of current state only | Snapshot comparison with diff metrics (node/edge/locked counts + WL hash) | Proves nothing was lost between sessions |
| No graph structure -- flat key-value facts | NetworkX DiGraph with typed relationships | Enables cross-fact reasoning, relationship traversal |
| Conflicts logged but auto-resolved if `cand_conf >= current_conf + 0.05` | Contradictions to locked facts quarantined, owner resolves via CLI | Human-in-the-loop for critical knowledge changes |

**Deprecated/outdated:**
- `brain_memory.py` `_load_facts` / `_save_facts` JSON flat file: Replaced by `kg_nodes` + `kg_edges` SQLite tables
- `brain_memory.py` `_extract_fact_candidates` hardcoded patterns: Replaced by extensible `FactExtractor` class
- `brain_regression_report()` single-snapshot health check: Replaced by dual-snapshot comparison with WL graph hash

## Open Questions

1. **How to handle the `facts` table already in `engine.py` schema?**
   - What we know: `engine.py` `_init_schema()` creates a `facts` table with `key`, `value`, `confidence`, `locked`, `sources`, `history` columns. This was created during Phase 1.
   - What's unclear: Whether any code currently writes to this SQLite `facts` table (vs the JSON flat file in `brain_memory.py`). The schema exists but may be empty.
   - Recommendation: Check if the SQLite `facts` table has any data. If empty, it can be dropped and replaced with `kg_nodes`/`kg_edges`. If populated, migrate its data into the new tables during the Phase 2 migration step. Add a schema version bump (version 2) in `schema_version` table.

2. **Graph size expectations and performance implications?**
   - What we know: For a single-user assistant, the knowledge graph will likely be 1K-50K nodes within the first year. NetworkX handles millions of nodes in memory.
   - What's unclear: Whether `to_networkx()` on every operation is acceptable, or if we need caching with invalidation.
   - Recommendation: Start with no caching (rebuild on demand). At the expected scale (< 50K nodes), building from SQLite takes <100ms. Add caching only if profiling shows a bottleneck.

3. **Fact extraction quality without NLP models?**
   - What we know: Regex patterns work well for structured domains (medications, family members, schedule items). They fail on open-ended prose.
   - What's unclear: What percentage of Jarvis ingested content will yield useful regex-extracted facts.
   - Recommendation: Start with high-precision, low-recall patterns. It is better to extract 5 correct facts per day than 50 noisy ones. The extraction patterns can be extended iteratively. Phase 5 (Knowledge Harvesting) will add LLM-based extraction for the long tail.

4. **Backward compatibility with existing `brain_memory.py` callers?**
   - What we know: `main.py` imports from `brain_memory` directly. Tests exercise `brain_memory` functions. Multiple handlers delegate to `brain_memory`.
   - What's unclear: Whether to maintain the old `brain_memory.py` fact functions as adapter shims or fully replace them.
   - Recommendation: Keep `brain_memory.py` functional during Phase 2 (it handles records, not just facts). Only the fact-related functions (`_load_facts`, `_save_facts`, `_extract_fact_candidates`, `_update_fact_store`) should be replaced by the new knowledge graph module. The record-handling functions (`ingest_brain_record`, `build_context_packet`, etc.) remain as-is since they are being migrated to SQLite in a separate track.

## Sources

### Primary (HIGH confidence)
- [NetworkX 3.6.1 PyPI](https://pypi.org/project/networkx/) -- Version 3.6.1 confirmed, Python >=3.11 required, zero runtime dependencies
- [NetworkX 3.6.1 Tutorial](https://networkx.org/documentation/stable/tutorial.html) -- DiGraph API, add_node/add_edge with attributes
- [NetworkX DiGraph docs](https://networkx.org/documentation/stable/reference/classes/digraph.html) -- Full DiGraph class reference
- [NetworkX JSON Graph docs](https://networkx.org/documentation/stable/reference/readwrite/json_graph.html) -- node_link_data/node_link_graph serialization
- [NetworkX graph hashing docs](https://networkx.org/documentation/stable/reference/algorithms/graph_hashing.html) -- weisfeiler_lehman_graph_hash function reference with node_attr/edge_attr
- [weisfeiler_lehman_graph_hash reference](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.graph_hashing.weisfeiler_lehman_graph_hash.html) -- Full signature: `(G, edge_attr=None, node_attr=None, iterations=3, digest_size=16)`
- Existing codebase analysis: `engine.py`, `brain_memory.py`, `memory_snapshots.py`, `command_bus.py`, `memory_commands.py`, `memory_handlers.py`

### Secondary (MEDIUM confidence)
- [NetworkX releases](https://networkx.org/documentation/stable/release/index.html) -- Version 3.6.1 released 2025-12-08, dev version 3.7rc0
- [Knowledge Graphs from scratch with Python](https://lopezyse.medium.com/knowledge-graphs-from-scratch-with-python-f3c2a05914cc) -- Patterns for building knowledge graphs with NetworkX
- [NetworkX Discussion: Custom persistent graph](https://groups.google.com/g/networkx-discuss/c/XUsKKr999KQ) -- Community patterns for SQLite persistence with NetworkX
- [NetworkDisk docs](https://networkdisk.inria.fr/tutorials/introduction.html) -- Alternative SQLite-backed graph (evaluated, not recommended)
- [Python hmac docs](https://docs.python.org/3/library/hmac.html) -- HMAC verification patterns used in snapshot signing

### Tertiary (LOW confidence)
- [Building a Local LLM-Powered Knowledge Graph](https://danielkliewer.com/blog/2025-10-19-building-a-local-llm-powered-knowledge-graph) -- Patterns for LLM-based graph construction (Phase 5 relevance)
- [extr library for regex NER+RE](https://github.com/dpasse/extr) -- Evaluated for fact extraction, decided custom patterns are simpler

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- NetworkX 3.6.1 version verified via PyPI, API confirmed via official docs, zero-dependency confirmed
- Architecture: HIGH -- Pattern of SQLite tables + NetworkX reconstruction is well-established in community. Schema design based on existing Phase 1 patterns. Command Bus integration follows proven Phase 1 patterns.
- Pitfalls: HIGH -- Race condition patterns verified against SQLite documentation. Lock enforcement uses atomic SQL WHERE pattern. Stale graph concern addressed by "rebuild on demand" pattern.
- Fact extraction: MEDIUM -- Regex patterns are domain-specific and will need iterative refinement. Quality depends on content patterns that are hard to predict until real-world usage.

**Research date:** 2026-02-22
**Valid until:** 2026-03-22 (NetworkX is stable, SQLite is stable, no fast-moving dependencies)
