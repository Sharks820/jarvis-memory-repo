# Architecture Research: Jarvis Local-First AI Personal Assistant

**Domain:** Local-first AI personal assistant with hierarchical memory, semantic search, multi-model routing, knowledge graph, device sync, and streaming voice personality
**Researched:** 2026-02-22
**Confidence:** HIGH (existing codebase analyzed, patterns verified against official docs and multiple sources)

## Current State Analysis

The existing Jarvis codebase has 29 Python source files with a monolithic `main.py` (~31k tokens) that acts as a god-object CLI router. The current architecture has these layers:

- **CLI Router** (`main.py`): Giant argparse handler with 30+ commands, inline business logic, direct function calls to every module
- **Memory** (`memory_store.py` + `brain_memory.py`): JSONL append-only files, keyword-based search via token overlap scoring, JSON fact ledger with basic conflict tracking
- **Ingestion** (`ingest.py`): Thin pipeline that writes to MemoryStore with no chunking, no enrichment, no embeddings
- **Model Routing** (`router.py`): Simple if/else on risk+complexity strings, no actual model integration beyond Ollama
- **Voice** (`voice.py`): Edge-TTS with streaming chunks, Windows Speech fallback, no personality integration at synthesis layer
- **Persona** (`persona.py`): Template-based response composition with humor levels, completely disconnected from voice synthesis
- **Mobile API** (`mobile_api.py`): ThreadingHTTPServer with HMAC auth, spawns subprocess for commands
- **Sync** (`resilience.py`): Status checks only, no actual bidirectional data sync
- **Connectors** (`connectors.py`): Permission framework defined but all connectors are stubs

**Key structural problems:**
1. `main.py` knows about every module and wires everything together inline
2. No dependency injection -- modules create their own `MemoryStore` instances
3. Memory search is O(n) token overlap against all records
4. No embeddings, no database, no semantic understanding
5. Sync is reporting only, not actual data transfer
6. Persona and voice are disconnected layers

## Recommended Architecture

### System Overview

```
+=========================================================================+
|                       INTERFACE LAYER                                    |
|  +----------+  +----------+  +-----------+  +----------+                |
|  |  CLI     |  |  Mobile  |  |  Desktop  |  |  Daemon  |                |
|  |  Router  |  |  API     |  |  Widget   |  |  Loop    |                |
|  +----+-----+  +----+-----+  +----+------+  +----+-----+               |
|       |              |             |              |                      |
+-------+--------------+-------------+--------------+---------------------+
        |              |             |              |
+-------v--------------v-------------v--------------v---------------------+
|                     COMMAND BUS (mediator)                               |
|   Routes intents to handlers. All interfaces produce Command objects.    |
+-------+----------------------------+-----------------------------------+
        |                            |
+-------v----------------------------v-----------------------------------+
|                     SERVICE LAYER                                       |
|  +----------------+  +-------------+  +-----------+  +---------------+  |
|  | Memory         |  | Intelligence|  | Voice &   |  | Connector     |  |
|  | Service        |  | Router      |  | Persona   |  | Service       |  |
|  +-------+--------+  +------+------+  +-----+-----+  +------+-------+  |
|          |                  |               |               |           |
+----------+------------------+---------------+---------------+-----------+
           |                  |               |               |
+----------v------------------v---------------v---------------v-----------+
|                     CORE LAYER                                          |
|  +----------+  +-----------+  +----------+  +---------+  +-----------+ |
|  | Memory   |  | Knowledge |  | Model    |  | Sync    |  | Security  | |
|  | Engine   |  | Graph     |  | Gateway  |  | Engine  |  | Gate      | |
|  +----+-----+  +-----+-----+  +----+-----+  +----+----+  +-----+----+ |
|       |              |              |             |              |      |
+-------+--------------+--------------+-------------+--------------+-----+
        |              |              |             |              |
+-------v--------------v--------------v-------------v--------------v-----+
|                     STORAGE LAYER                                       |
|  +---------------------+  +------------------+  +-------------------+  |
|  | SQLite DB            |  | Embedding Store  |  | File System       |  |
|  | - records table      |  | (sqlite-vec)     |  | - snapshots       |  |
|  | - facts table        |  |                  |  | - configs         |  |
|  | - FTS5 index         |  |                  |  | - sync manifests  |  |
|  | - sync changelog     |  |                  |  |                   |  |
|  +---------------------+  +------------------+  +-------------------+  |
+------------------------------------------------------------------------+
```

### Component Responsibilities

| Component | Responsibility | Communicates With |
|-----------|----------------|-------------------|
| **CLI Router** | Parse argparse commands, produce Command objects, print results | Command Bus only |
| **Mobile API** | HTTP endpoints with HMAC auth, produce Command objects | Command Bus, Security Gate |
| **Desktop Widget** | Quick-panel UI, produce Command objects | Command Bus |
| **Daemon Loop** | Scheduled tasks (maintenance, sync, auto-ingest) | Command Bus |
| **Command Bus** | Route Command objects to correct handler, enforce auth | All services |
| **Memory Service** | Ingest, query, compact, regression checks | Memory Engine, Knowledge Graph |
| **Intelligence Router** | Classify intent complexity, select model, manage fallback chain | Model Gateway, Memory Service |
| **Voice & Persona** | Compose personality-aware text, synthesize speech, stream audio | Persona config, Edge-TTS |
| **Connector Service** | Manage external data sources (calendar, email, tasks, bills) | Memory Service, external APIs |
| **Memory Engine** | SQLite CRUD, FTS5 search, embedding search, tiered storage | SQLite DB, Embedding Store |
| **Knowledge Graph** | Fact CRUD, contradiction detection, provenance tracking, anti-regression locks | Memory Engine |
| **Model Gateway** | Unified interface to Ollama, Anthropic API, OpenAI API | External LLM APIs |
| **Sync Engine** | Bidirectional diff-based sync, conflict resolution, changelog management | Memory Engine, File System |
| **Security Gate** | Owner guard, capability tiers, trusted device management | All mutation paths |

## Recommended Project Structure

```
engine/src/jarvis_engine/
+-- __init__.py
+-- app.py                    # Application bootstrap, DI container
+-- command_bus.py             # Command/handler registry, mediator pattern
+-- commands/                  # Command definitions (dataclasses)
|   +-- __init__.py
|   +-- memory_commands.py     # IngestCommand, QueryCommand, CompactCommand
|   +-- voice_commands.py      # SpeakCommand, VoiceRunCommand
|   +-- system_commands.py     # SyncCommand, SelfHealCommand, StatusCommand
|   +-- task_commands.py       # CodeGenCommand, ImageGenCommand
+-- handlers/                  # Command handlers (business logic)
|   +-- __init__.py
|   +-- memory_handlers.py
|   +-- voice_handlers.py
|   +-- system_handlers.py
|   +-- task_handlers.py
+-- interfaces/                # Entry points (thin, no business logic)
|   +-- __init__.py
|   +-- cli.py                 # Replaces monolithic main.py
|   +-- mobile_api.py          # HTTP server (kept, cleaned)
|   +-- daemon.py              # Background loop
+-- memory/                    # Memory subsystem
|   +-- __init__.py
|   +-- engine.py              # SQLite + FTS5 + sqlite-vec operations
|   +-- embeddings.py          # sentence-transformers model loading + encoding
|   +-- tiers.py               # Hot/warm/cold tier management
|   +-- ingest.py              # Enriched ingestion pipeline
|   +-- search.py              # Hybrid search (keyword + semantic + recency)
+-- knowledge/                 # Knowledge graph subsystem
|   +-- __init__.py
|   +-- graph.py               # Fact nodes, relations, provenance
|   +-- contradiction.py       # Conflict detection and resolution
|   +-- anti_regression.py     # Locked facts, regression scoring
+-- intelligence/              # LLM routing subsystem
|   +-- __init__.py
|   +-- router.py              # Intent classification + model selection
|   +-- gateway.py             # Unified model API (Ollama, Anthropic, OpenAI)
|   +-- profiles.py            # Model profiles (cost, speed, capability)
+-- voice/                     # Voice + personality subsystem
|   +-- __init__.py
|   +-- persona.py             # Personality composition (upgraded)
|   +-- tts.py                 # Edge-TTS + Windows Speech (existing, cleaned)
|   +-- streaming.py           # Streaming pipeline (persona -> TTS -> playback)
+-- sync/                      # Device sync subsystem
|   +-- __init__.py
|   +-- engine.py              # Diff generation, merge, conflict resolution
|   +-- changelog.py           # Operation log for sync
|   +-- transport.py           # HTTP-based sync protocol
+-- connectors/                # External service connectors
|   +-- __init__.py
|   +-- base.py                # Connector interface
|   +-- calendar.py
|   +-- email.py
|   +-- tasks.py
+-- security/                  # Security subsystem
|   +-- __init__.py
|   +-- owner_guard.py         # Existing, moved
|   +-- capability.py          # Existing, moved
|   +-- policy.py              # Existing, moved
+-- config.py                  # Configuration (existing, extended)
```

### Structure Rationale

- **interfaces/**: Thin entry points that parse input and produce Command objects. No business logic. This is what enables CLI, mobile API, and daemon to share identical behavior.
- **commands/ + handlers/**: Mediator pattern separates "what to do" from "how to do it". Commands are pure dataclasses. Handlers contain logic. This replaces main.py's inline wiring.
- **memory/**: Encapsulates the entire memory subsystem behind a clean interface. The rest of the system calls `memory.search.hybrid_search(query)` -- it never touches SQLite directly.
- **knowledge/**: Separated from memory because the knowledge graph has its own lifecycle (fact promotion, contradiction resolution, anti-regression locks) that is distinct from raw memory storage.
- **intelligence/**: Isolated LLM interactions behind a gateway so the system can switch models or add providers without touching business logic.
- **voice/**: Merges persona + TTS into a single subsystem so personality-aware text flows directly into streaming synthesis.
- **sync/**: Clean boundary around all sync logic. The sync engine produces diffs from the changelog and merges incoming diffs, but never directly mutates memory -- it goes through Memory Service.
- **security/**: Collected from scattered owner_guard, capability, policy into one module.

## Architectural Patterns

### Pattern 1: Command Bus (Mediator)

**What:** All user-facing interfaces (CLI, mobile API, daemon) produce typed Command dataclasses. A central bus dispatches them to registered handlers. Handlers return typed Result objects.

**When to use:** Always. Every user action flows through this bus. This is the key pattern that decomposes the monolithic main.py.

**Trade-offs:** Adds one level of indirection. Worth it because it eliminates the CLI-knows-everything problem and enables testing handlers without spinning up HTTP servers or parsing argparse.

**Example:**
```python
@dataclass
class QueryMemoryCommand:
    query: str
    max_items: int = 10
    include_facts: bool = True

@dataclass
class QueryMemoryResult:
    records: list[MemoryRecord]
    facts: list[Fact]
    search_stats: SearchStats

class QueryMemoryHandler:
    def __init__(self, memory_service: MemoryService):
        self._memory = memory_service

    def handle(self, cmd: QueryMemoryCommand) -> QueryMemoryResult:
        results = self._memory.hybrid_search(
            cmd.query, max_items=cmd.max_items
        )
        facts = []
        if cmd.include_facts:
            facts = self._memory.relevant_facts(cmd.query)
        return QueryMemoryResult(
            records=results.records,
            facts=facts,
            search_stats=results.stats,
        )
```

### Pattern 2: Tiered Memory with Promotion/Demotion

**What:** Memory exists in three tiers -- hot (recent, frequently accessed, in-memory cache), warm (indexed in SQLite with FTS5 + embeddings), cold (compacted summaries, archived). Records automatically promote and demote based on access patterns, recency, and confidence.

**When to use:** For all memory operations. The tier system is transparent to callers -- they query MemoryService and get results ranked across all tiers.

**Trade-offs:** More complex write path (must decide tier placement). Compaction can lose detail. Mitigated by keeping immutable append log alongside tiered storage.

**Example:**
```python
class TierManager:
    HOT_WINDOW_HOURS = 48      # Last 48 hours stay hot
    WARM_THRESHOLD_DAYS = 90   # After 90 days, eligible for cold
    COLD_COMPACT_BATCH = 500   # Compact in batches of 500

    def classify(self, record: MemoryRecord) -> Tier:
        age_hours = (now() - record.created_at).total_seconds() / 3600
        if age_hours <= self.HOT_WINDOW_HOURS:
            return Tier.HOT
        if record.access_count > 3 or record.confidence >= 0.85:
            return Tier.WARM  # frequently accessed or high-confidence stays warm
        if age_hours > self.WARM_THRESHOLD_DAYS * 24:
            return Tier.COLD
        return Tier.WARM
```

### Pattern 3: Hybrid Search (FTS5 + Embeddings + Recency)

**What:** Every query runs through three scoring paths simultaneously: FTS5 keyword match (fast, precise for exact terms), embedding cosine similarity (semantic understanding), and recency decay. Scores are combined using reciprocal rank fusion (RRF) to produce a final ranked list.

**When to use:** Every memory retrieval. The hybrid approach eliminates the biggest weakness of the current system (keyword-only matching misses semantically related content).

**Trade-offs:** Slightly slower than pure keyword search (~50-100ms overhead for embedding computation). Worth it because recall improves dramatically.

**Example:**
```python
def hybrid_search(self, query: str, k: int = 10) -> list[ScoredRecord]:
    # 1. FTS5 keyword search
    fts_results = self._fts5_search(query, limit=k * 3)

    # 2. Embedding similarity search
    query_vec = self._embed(query)
    vec_results = self._vec_search(query_vec, limit=k * 3)

    # 3. Reciprocal Rank Fusion
    rrf_k = 60  # standard RRF constant
    scores: dict[str, float] = {}
    for rank, rec in enumerate(fts_results):
        scores[rec.id] = scores.get(rec.id, 0) + 1.0 / (rrf_k + rank)
    for rank, rec in enumerate(vec_results):
        scores[rec.id] = scores.get(rec.id, 0) + 1.0 / (rrf_k + rank)

    # 4. Recency boost
    for rec_id, score in scores.items():
        recency = self._recency_weight(rec_id)
        scores[rec_id] = score * (1.0 + 0.3 * recency)

    # 5. Return top-k
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    return [self._load_record(rid) for rid, _ in ranked]
```

### Pattern 4: Anti-Regression Fact Locks

**What:** Once a fact reaches "locked" status (verified by multiple sources, high confidence, owner-confirmed), it cannot be overwritten by lower-confidence information. Updates to locked facts are quarantined as "pending contradiction" for human review.

**When to use:** For the canonical fact store. This is the core anti-regression mechanism.

**Trade-offs:** Can block legitimate updates if lock threshold is too aggressive. Mitigate with owner override command.

**Example:**
```python
class FactLockPolicy:
    LOCK_CONFIDENCE = 0.90
    LOCK_MIN_SOURCES = 2

    def can_update(self, existing: Fact, incoming: FactCandidate) -> UpdateDecision:
        if not existing.locked:
            return UpdateDecision.ALLOW

        if incoming.confidence > existing.confidence + 0.1:
            return UpdateDecision.QUARANTINE  # Strong signal, needs review
        return UpdateDecision.REJECT  # Too weak to challenge a locked fact
```

### Pattern 5: Changelog-Based Bidirectional Sync

**What:** Every write operation (ingest, fact update, config change) appends an entry to a monotonic changelog with a logical sequence number. Sync works by exchanging changelog ranges: "Give me everything after sequence X." Conflicts are resolved with last-writer-wins at the field level (not record level), with the device producing the higher-confidence update winning ties.

**When to use:** For mobile-desktop sync. This replaces the current status-check-only sync.

**Trade-offs:** Changelog grows over time (needs periodic trimming after confirmed sync). Field-level merge is more complex than record-level but avoids data loss.

**Example:**
```python
@dataclass
class ChangelogEntry:
    seq: int                    # Monotonic sequence number
    device_id: str              # Which device made the change
    timestamp: str              # ISO UTC
    table: str                  # Which table/entity changed
    record_id: str              # Which record
    operation: str              # insert | update | delete
    fields: dict[str, Any]     # Changed fields and new values
    checksum: str              # SHA-256 of serialized fields

class SyncEngine:
    def generate_diff(self, since_seq: int) -> list[ChangelogEntry]:
        """Get all changes since the given sequence number."""
        return self._db.query(
            "SELECT * FROM changelog WHERE seq > ? ORDER BY seq",
            (since_seq,)
        )

    def apply_diff(self, entries: list[ChangelogEntry]) -> SyncResult:
        """Merge incoming changes, field-level conflict resolution."""
        conflicts = []
        applied = 0
        for entry in entries:
            local = self._db.get(entry.table, entry.record_id)
            if local and local.updated_at > entry.timestamp:
                conflicts.append(self._resolve_field_level(local, entry))
            else:
                self._db.apply(entry)
                applied += 1
        return SyncResult(applied=applied, conflicts=conflicts)
```

## Data Flow

### Memory Ingestion Flow

```
[Input Source]
    |
    v
[Sanitize + Deduplicate]  -- SHA-256 content hash check
    |
    v
[Chunk + Enrich]           -- Split long content, extract entities
    |
    v
[Generate Embedding]       -- sentence-transformers all-MiniLM-L6-v2
    |
    v
[Classify Branch]          -- Semantic classification (replaces keyword)
    |
    v
[Write to SQLite]          -- records table + FTS5 index + vec0 table
    |
    v
[Extract Facts]            -- Candidate facts from content
    |
    v
[Contradiction Check]      -- Compare against locked facts
    |                              |
    v                              v
[Promote to Facts]         [Quarantine Conflict]
    |
    v
[Append Changelog]         -- For sync engine
```

### Query Flow

```
[User Query]
    |
    v
[Intent Classification]    -- Intelligence Router determines complexity
    |
    +-- Simple query --> Local model (Ollama)
    +-- Complex query --> Cloud model (Anthropic Opus/Sonnet)
    |
    v
[Build Context Packet]
    |
    +-- [Hybrid Search] -- FTS5 + embeddings + recency
    +-- [Relevant Facts] -- From knowledge graph
    +-- [Active Context] -- Current task, recent conversation
    |
    v
[Compose Prompt]           -- System prompt + context + query
    |
    v
[Model Gateway]            -- Route to selected model
    |
    v
[Response Processing]
    |
    +-- [Extract learnings] --> Memory ingestion (async)
    +-- [Persona composition] --> Personality-aware text
    |
    v
[Voice Synthesis]          -- Streaming TTS with personality cadence
    |
    v
[Output]                   -- Text + speech to user
```

### Sync Flow

```
[Desktop]                              [Mobile]
    |                                      |
    v                                      v
[Changelog]                         [Changelog]
 seq: 1..N                          seq: 1..M
    |                                      |
    |------- POST /sync/pull ------------->|
    |        "give me since seq X"         |
    |<------ entries [X+1..M] -------------|
    |                                      |
    |------- POST /sync/push ------------->|
    |        entries [Y+1..N]              |
    |<------ ack + conflicts --------------|
    |                                      |
    v                                      v
[Apply + Resolve Conflicts]     [Apply + Resolve Conflicts]
    |                                      |
    v                                      v
[Both devices converge]         [Both devices converge]
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: God Module

**What people do:** Keep all command routing, business logic, and I/O in a single file (like current main.py at 31k tokens).

**Why it's wrong:** Every change touches the same file, merge conflicts are constant, testing requires importing the entire system, and the module grows without bound.

**Do this instead:** Command Bus pattern. Thin interfaces produce Command dataclasses. Separate handler files contain business logic. main.py becomes ~200 lines of argparse that calls `bus.dispatch(command)`.

### Anti-Pattern 2: Direct Storage Access from Interfaces

**What people do:** CLI handler directly queries SQLite, mobile API directly writes to JSONL files.

**Why it's wrong:** Storage schema changes require updating every entry point. Business rules (dedup, sanitization, access control) get duplicated or forgotten.

**Do this instead:** All storage access goes through service layer. Interfaces only interact with the Command Bus.

### Anti-Pattern 3: Embedding Model as Global Singleton

**What people do:** Load the sentence-transformers model at module import time, or create new instances per request.

**Why it's wrong:** Import-time loading delays startup for all commands (even ones that don't need embeddings). Per-request loading is slow (~2s model load).

**Do this instead:** Lazy-loaded singleton via the DI container. First call loads the model; subsequent calls reuse it. Model is only loaded when an embedding operation actually occurs.

```python
class EmbeddingService:
    def __init__(self):
        self._model = None

    def embed(self, text: str) -> list[float]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
        return self._model.encode(text).tolist()
```

### Anti-Pattern 4: Sync as Bulk State Transfer

**What people do:** Send the entire database state on each sync, or diff at the file level.

**Why it's wrong:** Bandwidth-expensive on mobile data, creates merge nightmares, and can lose concurrent edits.

**Do this instead:** Changelog-based sync with field-level merge. Only transmit what changed since last sync.

### Anti-Pattern 5: Tight Coupling Between LLM Provider and Business Logic

**What people do:** Directly call `ollama_generate()` or `anthropic_completion()` throughout the codebase.

**Why it's wrong:** Switching providers or adding new ones requires code changes everywhere. Testing requires mocking specific API clients.

**Do this instead:** Model Gateway with a unified interface. Business logic calls `gateway.complete(prompt, profile)`. The gateway handles provider selection, fallback chains, and API differences internally.

## Migration Strategy: Decomposing main.py

The refactoring should be done incrementally to avoid a risky big-bang rewrite. Each step should keep all 125 tests passing.

### Step 1: Extract Command Definitions (LOW RISK)

Create `commands/` directory with pure dataclass definitions for every CLI command. No logic changes. main.py still works identically but commands are now importable types.

### Step 2: Create Handler Stubs (LOW RISK)

Create `handlers/` directory. Each handler wraps the existing function call that currently lives inline in main.py. The handlers call the same underlying functions. main.py delegates to handlers instead of inlining logic.

### Step 3: Introduce Command Bus (MEDIUM RISK)

Create `command_bus.py`. Register handlers. Replace main.py's giant if/elif chain with `bus.dispatch(command)`. This is the structural pivot point.

### Step 4: Extract Interface Modules (LOW RISK)

Move CLI-specific code to `interfaces/cli.py`. Move mobile API to `interfaces/mobile_api.py`. Both now produce commands and dispatch through the bus.

### Step 5: Migrate Memory to SQLite (HIGH IMPACT)

Create `memory/engine.py` with SQLite backend. Write migration script that reads existing JSONL/JSON files and imports into SQLite. Run dual-write mode temporarily (write to both JSONL and SQLite). Validate with regression tests. Remove JSONL writes.

### Step 6: Add Embeddings Layer (HIGH IMPACT)

Add `memory/embeddings.py` with sentence-transformers. Add `sqlite-vec` extension. Generate embeddings for all existing records during migration. Replace keyword-match `build_context_packet` with hybrid search.

### Step 7: Upgrade Knowledge Graph (MEDIUM IMPACT)

Move fact ledger from JSON to SQLite tables. Add proper contradiction detection with embedding similarity (catch semantic conflicts, not just exact key matches). Implement fact locking.

### Step 8: Add Real Sync Engine (HIGH IMPACT)

Create changelog table in SQLite. Implement diff generation and merge. Add sync endpoints to mobile API. Test with simulated mobile device.

### Step 9: Upgrade Intelligence Router (MEDIUM IMPACT)

Add Anthropic and OpenAI API support to Model Gateway. Implement intent classification for routing decisions. Add cost tracking.

### Step 10: Integrate Voice + Persona Pipeline (LOW-MEDIUM IMPACT)

Connect persona composition directly into the TTS streaming pipeline. Add personality-aware cadence hints (pause length, emphasis) to the streaming chunker.

## Scaling Considerations

This is a single-user personal assistant, so scaling is about data volume and response latency, not concurrent users.

| Concern | At 1K records | At 100K records | At 1M records |
|---------|---------------|-----------------|---------------|
| Memory search | <10ms, brute force fine | <100ms with FTS5+vec indexes | May need partitioning by tier/date |
| Embedding generation | <50ms per record | Batch import ~30 min | Batch import ~5 hours, do incrementally |
| SQLite DB size | <5MB | ~200MB (with embeddings) | ~2GB, consider WAL mode tuning |
| Sync payload | <100KB per sync | ~5MB if full resync | Changelog-only sync essential |
| Startup time | <1s | <2s (lazy embedding model) | <2s (model loads on first query) |

### First Bottleneck: Embedding Model Load Time

The sentence-transformers model takes ~1-2 seconds to load on first use. Solve with lazy loading -- only load when an embedding operation is needed. For daemon mode, pre-warm on startup.

### Second Bottleneck: SQLite Write Contention

Multiple writers (daemon auto-ingest, mobile API, CLI) can contend on SQLite. Solve with WAL mode (Write-Ahead Logging) which allows concurrent reads with a single writer, and use a write queue in the daemon to serialize writes.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Anthropic API | REST via httpx, async where possible | Use for complex reasoning, learning extraction. Rate limit aware. |
| Ollama (local) | REST to localhost:11434 | Existing pattern, keep for code gen and simple tasks. Model fallback chain. |
| Edge-TTS (Microsoft) | CLI subprocess (existing) | Keep existing pattern. Consider async streaming for lower latency. |
| Google Calendar | ICS feed or Google API OAuth | Connector reads only. Ingest events as memory records. |
| Email (IMAP) | imaplib, read-only initially | Connector reads only. Extract actionable items. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Interface -> Service | Command Bus (typed commands) | Strict boundary. Interfaces never import service internals. |
| Service -> Core | Direct method calls via injected deps | Services own the orchestration, cores own the data operations. |
| Memory -> Knowledge Graph | Direct (same process, shared SQLite) | Graph queries the same SQLite DB as memory engine. |
| Sync -> Memory | Through Memory Service only | Sync engine never writes directly to SQLite. Goes through service to trigger dedup, validation. |
| Any -> Security | Decorator/middleware pattern | Security checks wrap handlers, not mixed into business logic. |

## Key Technology Decisions

| Decision | Rationale |
|----------|-----------|
| **SQLite + FTS5 + sqlite-vec** for all storage | Single-file database, zero-config, excellent Python support, FTS5 for keyword search, sqlite-vec for vector search. No external server. Verified: sqlite-vec is pip-installable, runs on Windows, produces <75ms query times for 384-dim vectors. |
| **sentence-transformers all-MiniLM-L6-v2** for embeddings | 22MB model, 384-dim output, fast inference (<50ms per sentence), excellent semantic similarity. Runs entirely local. No API calls for retrieval. |
| **Changelog-based sync** (not CRDT) | CRDTs are powerful but complex to implement correctly and overkill for two-device sync. Changelog + field-level merge gives the same convergence guarantee for this use case with much less complexity. |
| **Command Bus** (not full CQRS) | Full CQRS with event sourcing is overkill for a single-user system. Command Bus gives the decoupling benefits without the operational overhead of event stores and projections. |
| **Modular monolith** (not microservices) | Single process, single deployment. The module boundaries are for code organization and testability, not for independent scaling. This is a personal assistant, not a distributed system. |

## Sources

- [sqlite-vec GitHub -- vector search SQLite extension](https://github.com/asg017/sqlite-vec) (HIGH confidence -- official repo)
- [Hybrid full-text + vector search with SQLite -- Alex Garcia](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) (HIGH confidence -- sqlite-vec author)
- [SQLite FTS5 Extension docs](https://www.sqlite.org/fts5.html) (HIGH confidence -- official SQLite docs)
- [sentence-transformers all-MiniLM-L6-v2 -- Hugging Face](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) (HIGH confidence -- official model card)
- [Sentence Transformers documentation](https://sbert.net/) (HIGH confidence -- official docs)
- [Modular monolith in Python](https://breadcrumbscollector.tech/modular-monolith-in-python/) (MEDIUM confidence -- well-regarded blog)
- [Multi-LLM routing strategies -- TrueFoundry](https://www.truefoundry.com/blog/multi-model-routing) (MEDIUM confidence -- industry blog)
- [RouteLLM framework -- LMSys](https://github.com/lm-sys/RouteLLM) (MEDIUM confidence -- research framework)
- [Local-first software -- Ink & Switch](https://www.inkandswitch.com/essay/local-first/) (HIGH confidence -- foundational research)
- [Personal AI Infrastructure -- Daniel Miessler](https://danielmiessler.com/blog/personal-ai-infrastructure) (MEDIUM confidence -- industry thought leader)
- [Generative Agents memory architecture -- Stanford](https://arxiv.org/abs/2304.03442) (HIGH confidence -- academic research)
- [Building a RAG on SQLite](https://blog.sqlite.ai/building-a-rag-on-sqlite) (MEDIUM confidence -- official sqlite.ai blog)
- [Graphiti -- Real-Time Knowledge Graphs for AI Agents](https://github.com/getzep/graphiti) (MEDIUM confidence -- open-source project)
- [The voice AI stack for building agents in 2025 -- AssemblyAI](https://www.assemblyai.com/blog/the-voice-ai-stack-for-building-agents) (MEDIUM confidence -- industry blog)

---
*Architecture research for: Jarvis Local-First AI Personal Assistant*
*Researched: 2026-02-22*
