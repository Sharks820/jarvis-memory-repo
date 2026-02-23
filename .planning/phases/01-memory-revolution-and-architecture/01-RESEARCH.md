# Phase 1: Memory Revolution and Architecture - Research

**Researched:** 2026-02-22
**Domain:** Python monolith decomposition via Command Bus, SQLite + FTS5 + sqlite-vec memory engine, sentence-transformers embedding pipeline, JSONL-to-SQLite data migration
**Confidence:** HIGH

## Summary

Phase 1 is the foundational rewrite that transforms Jarvis from a command runner with flat-file memory into a modular application with a real semantic memory engine. The work splits into three interconnected areas: (1) decomposing the 2757-line monolithic `main.py` into a Command Bus architecture where CLI, mobile API, and daemon all dispatch through the same mediator, (2) building a SQLite-backed memory engine that combines FTS5 full-text search with sqlite-vec vector similarity search for hybrid retrieval, and (3) migrating all existing JSONL/JSON memory data into the new SQLite database with zero record loss and enriched ingestion (chunking, embedding generation, semantic branch classification).

The existing codebase has 29 Python source files and 126 test functions across 23 test files. The main.py file contains 40+ subcommands routed via a giant argparse if/elif chain, with 38 `cmd_*` functions that inline business logic. The current memory system consists of two parallel stores: `MemoryStore` (55 lines, JSONL append-only event log) and `brain_memory.py` (644 lines, JSONL records with keyword-based token overlap scoring and hardcoded keyword rules for branch classification). The ingestion pipeline (`ingest.py`, 53 lines) does no chunking, no embedding, no enrichment -- it just writes to the event log. All of this must be replaced incrementally while keeping every existing test passing.

The technology stack is locked by prior research: SQLite + FTS5 + sqlite-vec 0.1.6+ for unified storage, nomic-embed-text-v1.5 (768-dim, 8192 token context) via sentence-transformers 5.0+ for embeddings, and a Command Bus mediator pattern for architecture decomposition. The primary risk is regression during migration. The mitigation strategy is adapter shims -- the new Command Bus handlers call the same underlying functions initially, allowing incremental extraction without breaking test assertions.

**Primary recommendation:** Decompose in three plans: (1) Command Bus + main.py decomposition with adapter shims, (2) SQLite + FTS5 + sqlite-vec memory engine with hybrid search, (3) enriched ingestion pipeline + data migration. Each plan must end with all 126 tests passing.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ARCH-01 | Monolithic main.py decomposed into Command Bus pattern with thin interfaces, typed commands, and separate handlers | Command Bus pattern documented with Python dataclass commands, handler registry, and bus.dispatch() mediator. main.py has 38 cmd_* functions and 40+ subcommands to extract. |
| ARCH-02 | All interfaces (CLI, mobile API, daemon) produce Command objects dispatched through the same bus | Three entry points identified: main.py CLI (argparse), mobile_api.py (ThreadingHTTPServer), and daemon loop in main.py cmd_daemon_run. Each must produce Command dataclasses. |
| ARCH-03 | Service layer mediates between interfaces and core storage -- interfaces never access storage directly | Service layer pattern with MemoryService, handler injection via constructor. Currently main.py directly calls brain_memory, memory_store, ingest functions. |
| ARCH-04 | Lazy-loaded embedding model (loads on first use, not at import time) | EmbeddingService pattern with lazy singleton: model=None until first .embed() call, then cached. sentence-transformers load takes ~1-2s. |
| ARCH-05 | SQLite WAL mode with write serialization for concurrent access from daemon + API + CLI | WAL mode enables concurrent reads with single writer. Write serialization must happen in application code (Python threading.Lock or queue). sqlite3 check_same_thread=False for multi-thread. |
| ARCH-06 | All 125+ existing tests continue to pass after each migration step | 126 test functions across 23 files. Adapter shim strategy: new handlers call existing functions initially, preserving return types and side effects. |
| MEM-01 | All memory records stored in SQLite with FTS5 full-text search index | SQLite FTS5 virtual table with external content configuration. records table + fts_records FTS5 index on summary/content fields. |
| MEM-02 | All memory records have embedding vectors stored via sqlite-vec for semantic search | sqlite-vec vec0 virtual table with float[768] embedding column. vectors serialized via struct.pack for insertion. KNN via WHERE embedding MATCH ? ORDER BY distance. |
| MEM-03 | Hybrid search (FTS5 keyword + embedding cosine + recency decay) returns relevant results | Reciprocal Rank Fusion (RRF) combining FTS5 rank and vec0 distance. Formula: 1/(k+fts_rank) + 1/(k+vec_rank) with recency boost multiplier. Alex Garcia documented this pattern. |
| MEM-04 | Memory records classified into branches using semantic classification instead of keyword matching | Replace _pick_branch() hardcoded keyword rules with embedding cosine similarity against branch centroid vectors. Pre-compute centroids from branch descriptions. |
| MEM-05 | Three-tier memory hierarchy (hot/warm/cold) with automatic promotion and demotion | tier column in records table. TierManager classifies by recency (48h hot), access count, and confidence. Cold records get compacted summaries. |
| MEM-06 | Ingestion pipeline chunks long content, extracts entities, generates embeddings, and classifies branch before storage | Enhanced IngestPipeline: sanitize -> deduplicate (SHA-256) -> chunk (if >2000 chars) -> embed (sentence-transformers) -> classify branch (embedding similarity) -> write SQLite. |
| MEM-07 | Content-hash deduplication (SHA-256) prevents duplicate records | Existing behavior in brain_memory.py (content_hash + hash_to_record_id index). Preserve in SQLite with UNIQUE constraint on content_hash column. |
| MEM-08 | Migration script imports all existing JSONL/JSON memory data into SQLite without data loss | Migration reads .planning/brain/records.jsonl + .planning/events.jsonl + .planning/brain/facts.json + .planning/brain/index.json. Count verification pre/post. Generates embeddings for all existing records. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | >=3.11 | Runtime | Already in use. 3.11+ for performance, tomllib, ExceptionGroup |
| SQLite (stdlib) | >=3.41 | Primary data store | Zero-config, ACID, built-in FTS5. Python 3.11+ ships adequate version |
| sqlite-vec | >=0.1.6 | Vector similarity search | KNN search inside SQLite via vec0 virtual table. pip-installable. SIMD-accelerated. By Alex Garcia |
| sentence-transformers | >=5.0.0 | Embedding model inference | Industry standard for local text embeddings. v5.2.3 current. Runs offline |
| nomic-embed-text-v1.5 | -- | Embedding model | 768-dim, 8192 token context, Apache 2.0. 81% Top-5 retrieval vs 56% for MiniLM. Via sentence-transformers with trust_remote_code=True |
| numpy | >=2.0.0 | Array operations | Required by sentence-transformers 5.x. Already a dependency |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| struct (stdlib) | -- | Vector serialization | Serialize float vectors to bytes for sqlite-vec insertion |
| threading (stdlib) | -- | Write serialization | Lock for SQLite write access from multiple threads |
| hashlib (stdlib) | -- | Content deduplication | SHA-256 hashing for dedup (existing pattern) |
| torch (CPU-only) | >=2.0 | ML backend | Required by sentence-transformers. Install CPU-only to save ~1.8GB |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| sqlite-vec | ChromaDB | Separate process + storage. sqlite-vec keeps everything in one file |
| sqlite-vec | FAISS | No metadata storage, no SQL queryability, separate index file |
| nomic-embed-text-v1.5 | all-MiniLM-L6-v2 | 512 token limit, 28% Top-1 accuracy. Outdated 2019 architecture |
| nomic-embed-text-v1.5 | BAAI/bge-small-en-v1.5 | 384-dim, 33MB. Fallback if RAM constrained (<8GB) |
| Command Bus | Full CQRS | Overkill for single-user. Command Bus gives decoupling without event store overhead |

**Installation:**
```bash
# CPU-only PyTorch first (saves ~1.8GB)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Core phase 1 dependencies
pip install sentence-transformers>=5.0.0 sqlite-vec>=0.1.6

# Already in pyproject.toml
pip install numpy>=2.0.0
```

**Critical note:** PyTorch CPU-only must be installed BEFORE sentence-transformers to avoid pulling the full CUDA build (~2GB). Use `--index-url https://download.pytorch.org/whl/cpu`.

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
+-- __init__.py
+-- app.py                    # Application bootstrap, DI wiring
+-- command_bus.py             # Command/handler registry, dispatch
+-- commands/                  # Command dataclasses (no logic)
|   +-- __init__.py
|   +-- memory_commands.py     # IngestCommand, QueryCommand, CompactCommand
|   +-- voice_commands.py      # SpeakCommand, VoiceRunCommand
|   +-- system_commands.py     # SyncCommand, StatusCommand, SnapshotCommand
|   +-- task_commands.py       # CodeGenCommand, ImageGenCommand
|   +-- ops_commands.py        # BriefCommand, AutopilotCommand
|   +-- security_commands.py   # OwnerGuardCommand, RuntimeControlCommand
+-- handlers/                  # Command handlers (business logic)
|   +-- __init__.py
|   +-- memory_handlers.py
|   +-- voice_handlers.py
|   +-- system_handlers.py
|   +-- task_handlers.py
|   +-- ops_handlers.py
|   +-- security_handlers.py
+-- interfaces/                # Thin entry points
|   +-- __init__.py
|   +-- cli.py                 # Replaces main.py argparse dispatch
+-- memory/                    # Memory subsystem
|   +-- __init__.py
|   +-- engine.py              # SQLite + FTS5 + sqlite-vec CRUD
|   +-- embeddings.py          # Lazy-loaded sentence-transformers
|   +-- tiers.py               # Hot/warm/cold tier management
|   +-- search.py              # Hybrid search (FTS5 + vec + recency RRF)
|   +-- migration.py           # JSONL -> SQLite migration script
+-- brain_memory.py            # KEPT -- adapter shim during transition
+-- memory_store.py            # KEPT -- adapter shim during transition
+-- ingest.py                  # KEPT initially, enhanced in Plan 3
+-- [all other existing files]  # Untouched
```

### Pattern 1: Command Bus (Mediator)
**What:** All user-facing interfaces produce typed Command dataclasses. A central CommandBus dispatches them to registered handlers. Handlers return typed Result dataclasses.
**When to use:** Every user action. This replaces the 38 cmd_* functions in main.py.
**Example:**
```python
# commands/memory_commands.py
from dataclasses import dataclass

@dataclass
class QueryMemoryCommand:
    query: str
    max_items: int = 10
    max_chars: int = 2400

@dataclass
class QueryMemoryResult:
    selected: list[dict]
    selected_count: int
    canonical_facts: list[dict]
    total_records_scanned: int


# command_bus.py
from typing import Any, Callable, TypeVar

T = TypeVar("T")

class CommandBus:
    def __init__(self) -> None:
        self._handlers: dict[type, Callable] = {}

    def register(self, command_type: type, handler: Callable) -> None:
        self._handlers[command_type] = handler

    def dispatch(self, command: Any) -> Any:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise ValueError(f"No handler for {type(command).__name__}")
        return handler(command)


# handlers/memory_handlers.py
class QueryMemoryHandler:
    def __init__(self, root: Path):
        self._root = root

    def handle(self, cmd: QueryMemoryCommand) -> QueryMemoryResult:
        # Initially delegates to existing function
        packet = build_context_packet(
            self._root,
            query=cmd.query,
            max_items=cmd.max_items,
            max_chars=cmd.max_chars,
        )
        return QueryMemoryResult(
            selected=packet["selected"],
            selected_count=packet["selected_count"],
            canonical_facts=packet["canonical_facts"],
            total_records_scanned=packet["total_records_scanned"],
        )
```

### Pattern 2: Adapter Shim Strategy
**What:** During decomposition, new Command Bus handlers delegate to existing functions. Tests continue passing because the underlying behavior is unchanged. Handlers are upgraded one at a time.
**When to use:** Every step of the main.py decomposition. This is the key to zero-regression migration.
**Example:**
```python
# interfaces/cli.py -- thin wrapper
def handle_brain_status(args) -> int:
    cmd = BrainStatusCommand(as_json=args.json)
    result = bus.dispatch(cmd)
    # Format output identically to old cmd_brain_status()
    if result.as_json:
        print(json.dumps(result.data, indent=2))
    else:
        for branch in result.branches:
            print(f"  {branch['branch']}: {branch['count']} records")
    return 0

# handlers/memory_handlers.py
class BrainStatusHandler:
    def handle(self, cmd: BrainStatusCommand) -> BrainStatusResult:
        # Phase 1: delegate to existing function
        data = brain_status(self._root)
        return BrainStatusResult(data=data, ...)
        # Phase 2 (later): call self._memory_engine.status()
```

### Pattern 3: SQLite Memory Engine with Hybrid Search
**What:** Unified SQLite database with three virtual tables: records (base data), fts_records (FTS5), vec_records (sqlite-vec). Hybrid search combines results via Reciprocal Rank Fusion.
**When to use:** All memory storage and retrieval operations.
**Example:**
```python
# memory/engine.py
import sqlite3
import struct
import sqlite_vec

def serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)

class MemoryEngine:
    def __init__(self, db_path: Path):
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)
        self._write_lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
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

            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_hash
                ON records(content_hash);

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_records
                USING fts5(summary, content='records', content_rowid='rowid');

            CREATE VIRTUAL TABLE IF NOT EXISTS vec_records
                USING vec0(
                    record_id TEXT PRIMARY KEY,
                    embedding float[768]
                );

            CREATE TABLE IF NOT EXISTS facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                locked INTEGER NOT NULL DEFAULT 0,
                updated_utc TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                old_confidence REAL NOT NULL,
                new_confidence REAL NOT NULL,
                record_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            );
        """)
```

### Pattern 4: Lazy-Loaded Embedding Service
**What:** The sentence-transformers model is NOT loaded at import time. It loads on first call to embed(), then is cached. This avoids ~1-2s startup penalty for commands that do not need embeddings.
**When to use:** Always. The EmbeddingService is injected into MemoryEngine and IngestPipeline.
**Example:**
```python
# memory/embeddings.py
class EmbeddingService:
    def __init__(self, model_name: str = "nomic-ai/nomic-embed-text-v1.5"):
        self._model_name = model_name
        self._model = None

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self._model_name, trust_remote_code=True
            )
        prefixed = f"{prefix}: {text}"
        embedding = self._model.encode(prefixed)
        return embedding.tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")

    def embed_batch(self, texts: list[str], prefix: str = "search_document") -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self._model_name, trust_remote_code=True
            )
        prefixed = [f"{prefix}: {t}" for t in texts]
        embeddings = self._model.encode(prefixed)
        return [e.tolist() for e in embeddings]
```

### Pattern 5: Hybrid Search with Reciprocal Rank Fusion
**What:** Every query runs FTS5 keyword search AND sqlite-vec KNN search, then combines results via RRF with recency boost.
**When to use:** All memory retrieval through MemoryEngine.search().
**Example:**
```python
# memory/search.py
def hybrid_search(
    db: sqlite3.Connection,
    query: str,
    query_embedding: bytes,
    k: int = 10,
    rrf_k: int = 60,
    recency_weight: float = 0.3,
) -> list[dict]:
    # 1. FTS5 keyword search
    fts_rows = db.execute(
        """SELECT record_id, rank FROM fts_records
           WHERE summary MATCH ? ORDER BY rank LIMIT ?""",
        (query, k * 3),
    ).fetchall()

    # 2. sqlite-vec KNN search
    vec_rows = db.execute(
        """SELECT record_id, distance FROM vec_records
           WHERE embedding MATCH ? AND k = ?
           ORDER BY distance""",
        (query_embedding, k * 3),
    ).fetchall()

    # 3. Reciprocal Rank Fusion
    scores: dict[str, float] = {}
    for rank, (rid, _) in enumerate(fts_rows):
        scores[rid] = scores.get(rid, 0) + 1.0 / (rrf_k + rank + 1)
    for rank, (rid, _) in enumerate(vec_rows):
        scores[rid] = scores.get(rid, 0) + 1.0 / (rrf_k + rank + 1)

    # 4. Recency boost
    for rid in scores:
        ts = db.execute(
            "SELECT ts FROM records WHERE record_id = ?", (rid,)
        ).fetchone()
        if ts:
            recency = _recency_weight(ts[0])
            scores[rid] *= (1.0 + recency_weight * recency)

    # 5. Return top-k
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    return [_load_record(db, rid) for rid, _ in ranked]
```

### Anti-Patterns to Avoid
- **Big-bang rewrite:** Do NOT try to rewrite main.py in one shot. Use adapter shims and incremental extraction. Each commit should pass all tests.
- **Direct storage access from CLI:** Do NOT let the new interfaces/cli.py import MemoryEngine directly. Always go through Command Bus.
- **Import-time embedding model load:** Do NOT put `SentenceTransformer(...)` at module level. Use lazy loading.
- **Separate vector database:** Do NOT use ChromaDB or FAISS alongside SQLite. Keep everything in sqlite-vec inside the same SQLite file.
- **Hardcoded branch keywords for classification:** Do NOT replicate the existing _pick_branch() keyword rules. Use embedding similarity against branch centroid vectors.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Vector similarity search | Custom cosine similarity loop over all records | sqlite-vec MATCH query | SIMD-accelerated, handles indexing, ~100x faster than Python loops |
| Full-text search | Custom token overlap scoring (current approach) | SQLite FTS5 | BM25 ranking built-in, handles stemming, orders of magnitude faster |
| Text embeddings | Custom word2vec or bag-of-words | sentence-transformers + nomic-embed-text-v1.5 | 768-dim semantic embeddings with 8192 token context. No custom ML needed |
| Database migrations | Manual file parsing and writing | SQLite with schema versioning table | ACID transactions, rollback on failure, single-file backup |
| Content deduplication | Custom hash index in JSON file | SQLite UNIQUE constraint on content_hash | Database-level enforcement, no separate index file to manage |
| Write serialization | Custom file locking | SQLite WAL mode + threading.Lock | WAL handles concurrent reads, Lock serializes writes at application level |

**Key insight:** The current codebase hand-rolls vector search (token overlap in Python), full-text search (manual keyword matching), and deduplication (JSON hash index file). All three have battle-tested SQLite equivalents that are faster, more reliable, and maintained by others.

## Common Pitfalls

### Pitfall 1: FTS5 External Content Sync
**What goes wrong:** FTS5 with `content='records'` does not automatically stay in sync when records are inserted/updated/deleted. Searches return stale or missing results.
**Why it happens:** FTS5 external content tables require explicit triggers or manual INSERT INTO fts_records(fts_records) VALUES('rebuild') commands.
**How to avoid:** Create triggers on the records table that mirror changes to fts_records. OR use content='' (standalone FTS5) and manually insert into both tables in the same transaction.
**Warning signs:** FTS5 search returns fewer results than expected, or recently inserted records are not found by keyword search.

### Pitfall 2: sqlite-vec Requires Binary Serialization
**What goes wrong:** Passing Python lists directly to sqlite-vec INSERT fails or produces garbage results.
**Why it happens:** sqlite-vec expects vectors as raw bytes (packed floats), not JSON arrays or Python lists.
**How to avoid:** Use `struct.pack(f"{len(vector)}f", *vector)` to serialize float lists to bytes before every INSERT and MATCH operation.
**Warning signs:** sqlite3.OperationalError or wildly incorrect distance values in KNN queries.

### Pitfall 3: nomic-embed-text-v1.5 Requires Task Prefixes
**What goes wrong:** Embeddings for documents and queries are in different vector spaces, producing poor cosine similarity scores.
**Why it happens:** The model was trained with task-specific prefixes. Omitting them or using the wrong prefix degrades retrieval quality significantly.
**How to avoid:** Always prefix documents with `"search_document: "` and queries with `"search_query: "`. For branch classification, use `"classification: "`.
**Warning signs:** Semantic search returns seemingly random results, or queries that should match documents get low similarity scores.

### Pitfall 4: Breaking Tests During main.py Decomposition
**What goes wrong:** Extracting cmd_* functions into handlers changes import paths, breaks monkeypatch targets in tests, or changes return value shapes.
**Why it happens:** test_main.py directly tests cmd_* functions via `main_mod.cmd_voice_run(...)` and monkeypatches `main_mod.run_mobile_server`. Moving these functions breaks those references.
**How to avoid:** Keep cmd_* functions in main.py as thin wrappers that call handler.handle(). Tests continue to import and call them. Update tests only after all handlers are verified.
**Warning signs:** ImportError in test files, monkeypatch.setattr failing to find target attribute.

### Pitfall 5: PyTorch CUDA Build Downloaded Instead of CPU-Only
**What goes wrong:** `pip install sentence-transformers` pulls the full CUDA-enabled PyTorch (~2GB) instead of CPU-only (~200MB).
**Why it happens:** If torch is not already installed when sentence-transformers is installed, pip resolves the default (CUDA) torch build.
**How to avoid:** Install CPU-only torch FIRST: `pip install torch --index-url https://download.pytorch.org/whl/cpu`, THEN install sentence-transformers.
**Warning signs:** Download progress showing 1.8GB+ for torch, or disk usage spiking by 2GB+ during pip install.

### Pitfall 6: SQLite Database Locked Errors Under Concurrent Access
**What goes wrong:** Daemon, mobile API, and CLI all try to write simultaneously, producing "database is locked" errors.
**Why it happens:** SQLite WAL mode allows concurrent reads but only ONE writer at a time. Without application-level write serialization, writers contend.
**How to avoid:** Set `PRAGMA busy_timeout=5000` (retry for 5 seconds). Use a threading.Lock around all write operations. WAL mode is essential: `PRAGMA journal_mode=WAL`.
**Warning signs:** sqlite3.OperationalError: "database is locked" during high-activity periods.

### Pitfall 7: Migration Loses Records Due to Encoding Issues
**What goes wrong:** JSONL files contain non-UTF-8 bytes, malformed JSON lines, or edge cases that the migration script silently drops.
**Why it happens:** JSONL files may have been appended to by different code paths over time, some with encoding bugs.
**How to avoid:** Migration script must: (1) count records in source JSONL, (2) count records inserted into SQLite, (3) fail loudly if counts do not match, (4) log every skipped line with the reason. Use errors='replace' for encoding, json.JSONDecodeError for malformed lines.
**Warning signs:** Post-migration record count is lower than pre-migration count.

## Code Examples

### SQLite Database Initialization with FTS5 + sqlite-vec
```python
# Source: sqlite-vec official demo + Alex Garcia hybrid search blog
import sqlite3
import struct
import threading
from pathlib import Path
import sqlite_vec

def create_memory_db(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")

    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    db.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'general',
            tags TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            confidence REAL NOT NULL DEFAULT 0.72,
            tier TEXT NOT NULL DEFAULT 'warm',
            access_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS fts_records
            USING fts5(summary, content='', contentless_delete=1);

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_records
            USING vec0(
                record_id TEXT PRIMARY KEY,
                embedding float[768]
            );
    """)
    return db
```

### Inserting a Record with FTS5 + Embedding
```python
# Source: sqlite-vec demo.py + nomic-embed-text docs
def insert_record(
    db: sqlite3.Connection,
    lock: threading.Lock,
    record: dict,
    embedding: list[float],
) -> None:
    vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
    with lock:
        with db:  # transaction
            db.execute(
                """INSERT OR IGNORE INTO records
                   (record_id, ts, source, kind, task_id, branch,
                    tags, summary, content_hash, confidence, tier)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["record_id"], record["ts"], record["source"],
                    record["kind"], record["task_id"], record["branch"],
                    json.dumps(record["tags"]), record["summary"],
                    record["content_hash"], record["confidence"],
                    record.get("tier", "warm"),
                ),
            )
            # FTS5 standalone mode -- manual insert
            db.execute(
                "INSERT INTO fts_records(rowid, summary) VALUES (last_insert_rowid(), ?)",
                (record["summary"],),
            )
            # sqlite-vec embedding
            db.execute(
                "INSERT INTO vec_records(record_id, embedding) VALUES (?, ?)",
                (record["record_id"], vec_bytes),
            )
```

### Embedding Generation with nomic-embed-text-v1.5
```python
# Source: HuggingFace nomic-ai/nomic-embed-text-v1.5 model card
from sentence_transformers import SentenceTransformer

# Lazy load -- only call this when embeddings are needed
model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)

# For documents being stored
doc_embedding = model.encode("search_document: Plan my calendar and email for tomorrow.")

# For user queries
query_embedding = model.encode("search_query: What medications do I take?")

# Batch encoding (much faster for migration)
docs = ["search_document: " + text for text in existing_records]
all_embeddings = model.encode(docs, batch_size=64, show_progress_bar=True)
```

### Semantic Branch Classification
```python
# Replace keyword-based _pick_branch() with embedding similarity
import numpy as np

BRANCH_DESCRIPTIONS = {
    "ops": "calendar scheduling meetings daily operations email organization",
    "coding": "programming software development debugging testing code deployment",
    "health": "medications prescriptions doctor appointments health pharmacy wellness",
    "finance": "budget banking payments invoices expenses financial planning",
    "security": "authentication passwords security access control trusted devices",
    "learning": "studying research education knowledge reading learning missions",
    "family": "children family school spouse home activities parenting",
    "communications": "phone calls text messages SMS contacts communication",
    "gaming": "video games gaming sessions steam fortnite competitive play",
}

def compute_branch_centroids(embed_service) -> dict[str, list[float]]:
    """Pre-compute once, cache in memory."""
    return {
        branch: embed_service.embed(desc, prefix="classification")
        for branch, desc in BRANCH_DESCRIPTIONS.items()
    }

def classify_branch(text_embedding: list[float], centroids: dict[str, list[float]]) -> str:
    best_branch = "general"
    best_sim = -1.0
    text_vec = np.array(text_embedding)
    for branch, centroid in centroids.items():
        cent_vec = np.array(centroid)
        sim = np.dot(text_vec, cent_vec) / (np.linalg.norm(text_vec) * np.linalg.norm(cent_vec))
        if sim > best_sim:
            best_sim = sim
            best_branch = branch
    return best_branch if best_sim > 0.3 else "general"
```

### JSONL to SQLite Migration
```python
# memory/migration.py
import json
from pathlib import Path

def migrate_brain_records(
    jsonl_path: Path,
    db: sqlite3.Connection,
    embed_service,
    lock: threading.Lock,
) -> dict:
    if not jsonl_path.exists():
        return {"status": "skip", "reason": "no source file"}

    source_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    source_count = 0
    inserted = 0
    skipped = 0
    errors = []

    for line_num, line in enumerate(source_lines, 1):
        if not line.strip():
            continue
        source_count += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"Line {line_num}: {e}")
            skipped += 1
            continue

        summary = record.get("summary", "")
        embedding = embed_service.embed(summary, prefix="search_document")
        insert_record(db, lock, record, embedding)
        inserted += 1

    assert inserted + skipped == source_count, (
        f"Record count mismatch: {inserted}+{skipped} != {source_count}"
    )
    return {
        "status": "ok",
        "source_count": source_count,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }
```

## State of the Art

| Old Approach (Current) | Current Approach (Phase 1) | Impact |
|------------------------|---------------------------|--------|
| JSONL append-only files | SQLite with FTS5 + sqlite-vec | ACID transactions, hybrid search, single-file backup |
| Token overlap keyword matching | Embedding cosine similarity + FTS5 BM25 | Semantic understanding, 3-4x better recall |
| Hardcoded keyword rules for branches | Embedding similarity to branch centroids | Handles synonyms, new vocabulary, context |
| Monolithic main.py (2757 lines, 38 cmd_* functions) | Command Bus with typed commands + handlers | Testable handlers, shared dispatch for CLI/API/daemon |
| Manual JSON index files for dedup | SQLite UNIQUE constraint on content_hash | Database-enforced, crash-safe, no separate files |
| No write serialization | WAL mode + threading.Lock | Concurrent reads, serialized writes, 5s busy timeout |

**Deprecated/outdated:**
- all-MiniLM-L6-v2: Despite appearing in most tutorials, this 2019 model has only 28% Top-1 retrieval accuracy and 512-token context. Use nomic-embed-text-v1.5 instead.
- JSONL as primary storage: Appropriate for append-only event logs but not for queryable memory with search. SQLite replaces this entirely.

## Open Questions

1. **FTS5 content mode: external content vs standalone?**
   - What we know: External content (`content='records'`) auto-references the base table but requires sync triggers. Standalone (`content=''`) requires manual inserts but is simpler.
   - What's unclear: Which approach is less error-prone for this codebase's write patterns.
   - Recommendation: Use standalone (`content=''`) with explicit inserts in the same transaction. Simpler to reason about, avoids trigger complexity.

2. **sqlite-vec rowid vs text primary key for vec0?**
   - What we know: vec0 supports both integer rowid and text primary key. The demo uses rowid. Auxiliary columns are supported with `+` prefix.
   - What's unclear: Performance implications of text primary key for our record_id pattern (16-char hex strings).
   - Recommendation: Use `record_id TEXT PRIMARY KEY` in vec0 to match the records table primary key. Simplifies joins. If performance is an issue, switch to integer rowid later.

3. **Embedding model download during first run?**
   - What we know: nomic-embed-text-v1.5 is ~274MB and downloads from HuggingFace on first use. No API key needed.
   - What's unclear: Whether the target machine has reliable internet for initial download. Whether to pre-download during install.
   - Recommendation: Add a `jarvis-engine setup-embeddings` CLI command that pre-downloads the model. Document the one-time download requirement.

4. **How to handle records without embeddings during migration transition?**
   - What we know: Existing brain records have no embeddings. Migration must generate them. This takes ~50ms per record.
   - What's unclear: For 1000+ records, migration could take 50+ seconds. Should it block startup or run async?
   - Recommendation: Migration is a one-time CLI command (`jarvis-engine migrate-memory`), not automatic. It runs synchronously with a progress bar. Post-migration, all new records get embeddings at ingest time.

5. **Test adapter strategy for test_main.py?**
   - What we know: test_main.py has 22 test functions that call cmd_* functions directly via `main_mod.cmd_voice_run(...)` and monkeypatch attributes on main_mod.
   - What's unclear: Whether to keep cmd_* as thin wrappers or update test imports.
   - Recommendation: Keep cmd_* functions as thin wrappers in main.py that create Command objects and call bus.dispatch(). Tests continue to call cmd_* without changes. This is the zero-regression path.

## Sources

### Primary (HIGH confidence)
- [sqlite-vec GitHub](https://github.com/asg017/sqlite-vec) -- official repo, vec0 virtual table API, Python demo
- [sqlite-vec PyPI v0.1.6](https://pypi.org/project/sqlite-vec/) -- version verified 2026-02-22
- [Alex Garcia: Hybrid Search with sqlite-vec + FTS5](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) -- RRF scoring, schema design, hybrid search patterns
- [SQLite FTS5 docs](https://www.sqlite.org/fts5.html) -- official full-text search documentation
- [SQLite WAL mode docs](https://sqlite.org/wal.html) -- concurrent access, write serialization
- [nomic-ai/nomic-embed-text-v1.5 HuggingFace](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) -- model card, task prefixes, Matryoshka dimensions, trust_remote_code requirement
- [sentence-transformers PyPI v5.2.3](https://pypi.org/project/sentence-transformers/) -- version verified 2026-02-22
- [sqlite-vec demo.py](https://github.com/asg017/sqlite-vec/blob/main/examples/simple-python/demo.py) -- canonical Python usage pattern

### Secondary (MEDIUM confidence)
- [Simon Willison: sqlite-vec with embeddings](https://til.simonwillison.net/sqlite/sqlite-vec) -- practical sqlite-vec usage patterns
- [Simon Willison: Enabling WAL mode](https://til.simonwillison.net/sqlite/enabling-wal-mode) -- WAL mode configuration
- [Charles Leifer: Going Fast with SQLite and Python](https://charlesleifer.com/blog/going-fast-with-sqlite-and-python/) -- SQLite Python performance patterns
- [SkyPilot: Abusing SQLite to Handle Concurrency](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/) -- concurrent access patterns
- [Towards Data Science: RAG in SQLite](https://towardsdatascience.com/retrieval-augmented-generation-in-sqlite/) -- RAG with sqlite-vec patterns

### Tertiary (LOW confidence)
- [Medium: How sqlite-vec Works](https://medium.com/@stephenc211/how-sqlite-vec-works-for-storing-and-querying-vector-embeddings-165adeeeceea) -- sqlite-vec internals overview

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all versions verified via PyPI, embedding model verified via HuggingFace
- Architecture: HIGH -- existing codebase analyzed (38 cmd_* functions counted, 126 tests counted), Command Bus pattern well-documented
- Pitfalls: HIGH -- verified against official docs (FTS5 sync, sqlite-vec binary format, nomic prefixes, WAL write serialization)
- Migration: MEDIUM -- migration strategy is sound but edge cases in existing JSONL data quality are unknown until attempted

**Research date:** 2026-02-22
**Valid until:** 2026-03-22 (stable libraries, no fast-moving dependencies)
