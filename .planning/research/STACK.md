# Stack Research

**Domain:** Local-first AI personal assistant with intelligent memory, multi-model LLM routing, semantic search, knowledge graphs, and voice synthesis
**Researched:** 2026-02-22
**Confidence:** MEDIUM-HIGH (most core libraries verified via PyPI/official docs; some integration patterns based on community evidence)

## Recommended Stack

### Core Runtime

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | >=3.11 | Runtime language | Already in use. 3.11+ for performance gains (10-60% faster than 3.10), required by NetworkX 3.6+, well-supported by all recommended libraries. Keep >=3.11 not >=3.10 to access tomllib stdlib and ExceptionGroup. |
| numpy | >=2.0.0 | Array computing for embeddings | Already a dependency. v2.x has breaking changes from v1.x but sentence-transformers 5.x requires it. Pin >=2.0.0 to avoid conflicts. |
| pydantic-settings | >=2.13.0 | Typed configuration management | Replace current ad-hoc config.py with type-safe, validated settings. Supports env vars, JSON files, secrets. Production-stable, released 2026-02-19. |

**Confidence:** HIGH -- versions verified via PyPI as of 2026-02-22.

### Database & Storage

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| SQLite (stdlib) | >=3.41 | Primary structured data store | Already available via Python stdlib. No external server needed. FTS5 built-in for full-text search. ACID-compliant. Perfect for single-user local-first architecture. |
| sqlite-vec | >=0.1.6 | Vector similarity search in SQLite | Adds KNN vector search directly inside SQLite via `vec0` virtual table. No separate vector DB needed. Single-file database. SIMD-accelerated. Maintained by Alex Garcia (sqlite ecosystem expert). Stable release 0.1.6, alpha 0.1.7 available. |
| FTS5 (SQLite built-in) | -- | Full-text keyword search | Built into SQLite, zero additional dependencies. Combined with sqlite-vec enables hybrid search (keyword + semantic) in a single database file. |

**Confidence:** HIGH -- sqlite-vec verified via PyPI (0.1.6, 2024-11-20) and GitHub (0.1.7-alpha.10, 2026-02-13). FTS5 is part of SQLite core.

**Architecture note:** The combination of SQLite + FTS5 + sqlite-vec gives you a unified storage layer where structured data, full-text indexes, and vector embeddings live in ONE file. This is architecturally superior to running ChromaDB or FAISS alongside SQLite because:
1. No data synchronization between two storage systems
2. Single backup/restore story
3. Hybrid search via SQL (FTS5 keyword match + vec0 cosine similarity) in one query
4. Transactional consistency across all data types

### Embeddings

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| sentence-transformers | >=5.0.0 | Local embedding model inference | Industry-standard Python framework for text embeddings. Runs fully offline after initial model download. Supports all recommended models below. v5.2.3 released 2026-02-17. Python >=3.10. |
| nomic-embed-text-v1.5 | -- | Primary embedding model | Use `nomic-ai/nomic-embed-text-v1.5` via sentence-transformers. 768-dim embeddings, 8192 token context (vs 512 for MiniLM). Significantly better retrieval accuracy than all-MiniLM-L6-v2 per 2025 benchmarks. Fully open-source (Apache 2.0). Runs locally with no API key. ~274MB model. |

**Confidence:** MEDIUM-HIGH -- sentence-transformers version verified via PyPI. Model comparison based on multiple benchmark sources (BentoML, AIMultiple, HackerNews discussions) all agreeing MiniLM-L6-v2 is outdated.

**Critical guidance on embedding model choice:**

Do NOT use `all-MiniLM-L6-v2` for new projects despite its popularity in tutorials. Multiple 2025 benchmarks show it achieves only 28% Top-1 retrieval accuracy vs 40%+ for modern models. Its 512-token context is severely limiting for a personal assistant that ingests long documents. Its 2019 architecture cannot compete with retrieval-optimized models.

Use `nomic-embed-text-v1.5` because:
- 8192 token context window (16x larger than MiniLM)
- Better retrieval accuracy (81.2% vs 56% Top-5 on standard benchmarks)
- Matryoshka embeddings: can truncate dimensions (768 -> 256 -> 128) for speed/quality tradeoff
- Runs via sentence-transformers with `trust_remote_code=True`
- Also available via Ollama (`ollama pull nomic-embed-text`) for integration with existing Ollama setup

**Fallback option:** If GPU memory is constrained, `BAAI/bge-small-en-v1.5` (384-dim, 33MB) is a reasonable lightweight alternative that still outperforms MiniLM-L6-v2.

### LLM Integration

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| anthropic | >=0.80.0 | Claude Opus/Sonnet API access | Official Anthropic Python SDK. Direct API calls without proxy overhead. Async support (AsyncAnthropic). Streaming support. v0.83.0 released 2026-02-19. Minimal latency -- no intermediary layer. |
| ollama | >=0.4.0 | Local model inference (Ollama API client) | Official Python client for Ollama. Already used in project. Supports chat, generate, embeddings, model management, streaming. v0.6.1 released 2025-11-13. |

**Confidence:** HIGH -- both verified via PyPI with recent releases.

**Critical decision: Direct SDKs over LiteLLM.**

Do NOT use LiteLLM for this project. Rationale:

1. **Overhead for a single-user app is unjustified.** LiteLLM adds 8ms+ P95 latency per request and introduces a proxy process. For a personal assistant with two providers (Anthropic + Ollama), the abstraction cost exceeds the benefit.
2. **Only two providers.** LiteLLM shines when routing across 10+ providers. With exactly two (cloud Anthropic, local Ollama), a simple router class (which already exists in router.py) is cleaner and more debuggable.
3. **Dependency weight.** LiteLLM pulls in 50+ transitive dependencies (Redis optional, httpx, tokenizers, etc.). The direct SDKs are lightweight.
4. **Custom routing logic.** Jarvis needs domain-specific routing (risk-based, complexity-based, cost-aware). This is better expressed in 50 lines of Python than in LiteLLM YAML config.

Instead, extend the existing `ModelRouter` class to:
- Route by task complexity (Opus for reasoning, Sonnet for summarization, Ollama for simple tasks)
- Track cost per-model
- Implement fallback chains (Anthropic unavailable -> Ollama fallback)
- Log routing decisions for learning

### Knowledge Graph

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| NetworkX | >=3.4 | In-memory knowledge graph with persistence | Production-stable graph library. DiGraph supports directed relationships (subject -> predicate -> object triples). Serialize to JSON/pickle for persistence. 3.6.1 released 2025-12-08. Python >=3.11. No external graph DB needed. |

**Confidence:** MEDIUM -- NetworkX version verified via PyPI. Its use for knowledge graphs is well-documented in 2025 tutorials. However, at scale (100K+ triples) performance may degrade vs a dedicated graph DB. For a single-user personal assistant, this is unlikely to be a problem for years.

**Architecture note:** Store the graph structure in NetworkX for traversal/query, but persist triple data in SQLite for durability. On startup, load from SQLite into NetworkX. This gives you SQL queryability AND graph traversal without an external graph database.

### Voice Synthesis

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| edge-tts | >=7.2.7 | Neural text-to-speech | Already in use and working well. Microsoft Edge neural voices. High-quality British male voices (en-GB-ThomasNeural). Free, no API key. Latest: 7.2.7 (2025-12-12). |

**Confidence:** HIGH -- already validated in production, version verified via PyPI.

**No changes recommended.** The existing voice.py implementation is solid with streaming chunked playback, fallback to Windows Speech, and configurable voice profiles. Keep as-is.

### Mobile Sync & HTTP

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| httpx | >=0.27.0 | Async HTTP client for API calls | Both sync and async. HTTP/2 support. Used by Anthropic SDK internally. Prefer over aiohttp for consistency with the Anthropic SDK's transport layer. |
| FastAPI | >=0.115.0 | Mobile API server (upgrade from current) | Already serving mobile API. Type-safe, async-native, automatic OpenAPI docs. If not already using FastAPI, the existing mobile_api.py HTTP server should be upgraded to it. |
| uvicorn | >=0.32.0 | ASGI server for FastAPI | Production-grade async server. Pairs with FastAPI. |

**Confidence:** MEDIUM -- httpx and FastAPI are standard choices. Mobile sync protocol design is the harder problem (see Architecture section).

**Sync protocol recommendation:** Do NOT use CRDTs (cr-sqlite, sqlite-sync) for V1. They add massive complexity for a two-device single-owner setup. Instead:
- Use timestamp-based last-write-wins with device priority (desktop = primary)
- Sync via encrypted JSON diffs over HTTPS
- Desktop is always authoritative for conflicts
- Track sync watermarks per-device

CRDTs become valuable at 3+ devices or multi-user -- neither applies here.

### Background Tasks & Scheduling

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| APScheduler | >=3.11.0 | Scheduled background tasks (nightly maintenance, sync cycles) | In-process task scheduler. BackgroundScheduler runs in separate thread. Cron-like triggers. No external service needed. v3.11.2 released 2025-12-22. Python >=3.8. |
| watchdog | >=6.0.0 | File system monitoring for auto-ingestion | Cross-platform filesystem event monitoring. Detects file creation/modification/deletion. Perfect for auto-ingesting new documents dropped into monitored folders. Python >=3.9. |

**Confidence:** HIGH -- both are mature, stable libraries verified via PyPI.

### Development & Testing

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| pytest | >=8.0.0 | Test framework | Already in use with 125 passing tests. |
| ruff | >=0.9.0 | Linter + formatter | Already in use. Replaces flake8 + black + isort. |
| mypy | >=1.11.0 | Static type checking | Already in use. |
| hypothesis | >=6.112.0 | Property-based testing | Already in dev dependencies. Use for testing embedding similarity properties and sync conflict resolution. |
| bandit | >=1.7.9 | Security linting | Already in use. Critical for a personal assistant handling sensitive data. |

**Confidence:** HIGH -- all already in pyproject.toml and verified.

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tiktoken | >=0.7.0 | Token counting for context window management | When building prompts for Claude -- count tokens to avoid exceeding limits. Also used for smart chunking during ingestion. |
| tenacity | >=8.0.0 | Retry logic with exponential backoff | Wrap all API calls (Anthropic, external services) with configurable retry policies. |
| cryptography | >=42.0.0 | Encryption for sync protocol and sensitive data | Encrypt sync payloads between desktop and mobile. Encrypt sensitive memory records at rest. |
| rich | >=13.0.0 | Terminal UI formatting | Upgrade CLI output from plain text to formatted tables, progress bars, status indicators. Already standard in Python CLI tools. |

**Confidence:** MEDIUM -- these are well-known libraries but specific version compatibility with the rest of the stack should be validated during implementation.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Vector store | sqlite-vec (in SQLite) | ChromaDB | Adds a separate process/database. Data lives outside SQLite, requiring sync between two storage systems. Overkill for single-user local-first. |
| Vector store | sqlite-vec (in SQLite) | FAISS | C++ library with Python bindings. No metadata storage, no SQL queryability. Must manage a separate index file. Good for 1M+ vectors but Jarvis will have <100K for years. |
| Vector store | sqlite-vec (in SQLite) | Pinecone/Weaviate | Cloud-hosted. Violates local-first privacy constraint. |
| LLM routing | Direct SDKs (anthropic + ollama) | LiteLLM | Proxy overhead, 50+ transitive deps, YAML config complexity. Only 2 providers needed. See detailed rationale above. |
| LLM routing | Direct SDKs | LangChain | Massive framework, opinionated abstractions, version churn. Jarvis needs surgical control over prompts and routing, not a framework. |
| Embedding model | nomic-embed-text-v1.5 | all-MiniLM-L6-v2 | Outdated (2019 architecture), 512 token limit, 28% Top-1 retrieval accuracy. Still appears in most tutorials but benchmarks unanimously show it is inferior. |
| Embedding model | nomic-embed-text-v1.5 | OpenAI text-embedding-3-small | Cloud API call required. Violates local-first for retrieval. Adds latency + cost to every search. |
| Knowledge graph | NetworkX (in-process) | Neo4j | External server process. Massive overhead for single-user. NetworkX handles <1M nodes easily in-memory. |
| Knowledge graph | NetworkX (in-process) | RDFLib | Over-engineered for personal assistant triples. SPARQL query language adds complexity without proportional benefit. |
| Sync protocol | Timestamp LWW over HTTPS | CRDTs (cr-sqlite) | Extreme complexity for two-device, single-owner. CRDTs solve multi-writer conflicts that don't exist here. |
| Sync protocol | Timestamp LWW over HTTPS | SQLite Sync extension | Very new (2025), limited Python support, designed for multi-user collaboration not personal sync. |
| Config management | pydantic-settings | python-dotenv | No type safety, no validation, just string env vars. pydantic-settings gives typed, validated config with defaults. |
| Config management | pydantic-settings | dynaconf | More complex than needed. pydantic-settings integrates naturally with the existing Python type system. |
| TTS | edge-tts (keep existing) | Coqui TTS | Requires GPU for quality output. edge-tts is free, high-quality, and already working. |
| TTS | edge-tts (keep existing) | ElevenLabs | Cloud API, paid, adds latency. edge-tts neural voices are sufficient quality. |
| HTTP client | httpx | aiohttp | aiohttp is async-only. httpx provides both sync and async, and is already the transport for the Anthropic SDK. One less dependency. |
| HTTP client | httpx | requests | No async support. httpx is the modern replacement with identical API plus async. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| LangChain | Massive abstraction layer with constant breaking changes. Adds 100+ transitive dependencies. Hides prompt engineering behind opaque chains. Personal assistant needs surgical prompt control. | Direct Anthropic/Ollama SDKs + custom routing |
| ChromaDB | Separate process, separate storage, separate backup story. For a single-user app with <100K vectors, sqlite-vec inside the existing SQLite DB is simpler and more maintainable. | sqlite-vec extension in SQLite |
| all-MiniLM-L6-v2 | 2019 architecture, 512-token context, bottom-tier retrieval accuracy in 2025 benchmarks. Every tutorial uses it but it is objectively worse than modern alternatives. | nomic-embed-text-v1.5 or BAAI/bge-small-en-v1.5 |
| LiteLLM | Adds proxy overhead, 50+ dependencies, YAML config complexity for what is a 2-provider routing problem solvable in 50 lines of Python. | Custom ModelRouter with anthropic + ollama SDKs |
| Pinecone / Weaviate / Qdrant | Cloud vector databases. Violate local-first privacy requirement. Add network latency to every retrieval. | sqlite-vec (local, in-process) |
| Neo4j / ArangoDB | External graph database servers. Massive operational overhead for single-user knowledge graph with <100K triples. | NetworkX (in-process, serialize to SQLite) |
| Docker | Project constraint: must work on Windows 11 without Docker. | Direct Python execution with venv |
| Redis | No need for distributed caching or message queuing in a single-user local app. | SQLite for persistence, in-memory Python dicts for caching |

## Stack Patterns by Variant

**If embedding quality matters more than speed (default -- recommended):**
- Use `nomic-embed-text-v1.5` (768-dim, 8192 token context)
- Store full 768-dim vectors in sqlite-vec
- ~274MB model, ~1 second first-load, ~50ms per embedding after warmup
- Because retrieval accuracy directly impacts assistant quality

**If running on a machine with limited RAM (<8GB available):**
- Use `BAAI/bge-small-en-v1.5` (384-dim, 512 token context, 33MB model)
- Store 384-dim vectors in sqlite-vec
- Because the model is 8x smaller with only moderate accuracy loss

**If embedding via Ollama is preferred (keep everything through Ollama):**
- Use `ollama pull nomic-embed-text` and call via ollama Python client
- Same model but served through Ollama's infrastructure
- Because it simplifies the runtime to one model server (Ollama) rather than sentence-transformers + Ollama separately

**If Claude API costs need strict control:**
- Route all tasks through Ollama by default
- Only escalate to Claude Opus for: multi-step reasoning, ambiguous queries, knowledge synthesis
- Use Claude Sonnet (not Opus) for: summarization, classification, simple Q&A
- Track costs per-query in SQLite for budget monitoring

## Version Compatibility Matrix

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| sentence-transformers >=5.0 | numpy >=2.0, Python >=3.10 | Will pull torch as dependency (~2GB). First install is large. |
| sqlite-vec >=0.1.6 | SQLite >=3.41 (recommended), Python 3 | Works with older SQLite but some features need >=3.41. Python stdlib sqlite3 on 3.11+ ships adequate SQLite version. |
| anthropic >=0.80 | httpx >=0.23, Python >=3.9 | Pulls httpx as transitive dependency. |
| networkx >=3.4 | Python >=3.11 | No heavy dependencies. Pairs with matplotlib for visualization (optional). |
| pydantic-settings >=2.13 | pydantic >=2.0, Python >=3.9 | Pulls pydantic v2 as dependency. |
| edge-tts >=7.2.7 | Python >=3.7 | Lightweight. Already installed. |
| APScheduler >=3.11 | Python >=3.8 | Lightweight, no heavy dependencies. |

**Critical compatibility note:** sentence-transformers pulls PyTorch (~2GB download). This is the heaviest dependency in the stack. On Windows 11, the CPU-only torch package (`pip install torch --index-url https://download.pytorch.org/whl/cpu`) is sufficient for embedding inference and is ~200MB instead of ~2GB. Use CPU-only unless the machine has an NVIDIA GPU with CUDA.

## Installation

```bash
# Core dependencies
pip install anthropic>=0.80.0 ollama>=0.4.0 sentence-transformers>=5.0.0 sqlite-vec>=0.1.6

# Knowledge graph and config
pip install networkx>=3.4 pydantic-settings>=2.13.0

# Background tasks and file monitoring
pip install apscheduler>=3.11.0 watchdog>=6.0.0

# HTTP and API
pip install httpx>=0.27.0 fastapi>=0.115.0 uvicorn>=0.32.0

# Supporting utilities
pip install tiktoken>=0.7.0 tenacity>=8.0.0 cryptography>=42.0.0 rich>=13.0.0

# Voice (already installed)
pip install edge-tts>=7.2.7

# Already installed (keep)
pip install numpy>=2.0.0

# Dev dependencies (already in pyproject.toml)
pip install -D pytest>=8.0.0 ruff>=0.9.0 mypy>=1.11.0 bandit>=1.7.9 hypothesis>=6.112.0

# IMPORTANT: For CPU-only PyTorch (saves ~1.8GB):
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Disk and Memory Budget

| Component | Disk | RAM (running) | Notes |
|-----------|------|---------------|-------|
| PyTorch (CPU-only) | ~200MB | ~100MB | Required by sentence-transformers |
| nomic-embed-text-v1.5 model | ~274MB | ~500MB | Downloaded once, cached locally |
| SQLite database (1yr usage) | ~50-200MB | ~10-50MB | Depends on ingestion volume |
| Ollama models | 2-8GB each | 4-8GB | Depends on model choice (codellama, mistral, etc.) |
| Total new additions | ~500MB disk | ~600MB RAM | On top of existing Ollama usage |

## Migration Path from Current State

The existing codebase uses:
- `memory_store.py`: JSONL append-only event log -> **Migrate to SQLite with events table**
- `brain_memory.py`: JSONL records with keyword-based branch filing -> **Migrate to SQLite with FTS5 + sqlite-vec embeddings**
- `router.py`: Simple risk/complexity routing -> **Extend with model-specific routing (Opus/Sonnet/Ollama) and cost tracking**
- `ingest.py`: Minimal pipeline (hash, log, truncate) -> **Add chunking, embedding generation, entity extraction, branch classification via embeddings**
- `config.py`: Ad-hoc configuration loading -> **Replace with pydantic-settings typed config**

Each migration should be incremental:
1. Add SQLite alongside existing JSONL (dual-write)
2. Migrate reads to SQLite
3. Remove JSONL dependency
4. Add embeddings and vector search
5. Add knowledge graph layer

## Sources

- [sqlite-vec PyPI](https://pypi.org/project/sqlite-vec/) -- version 0.1.6 verified (HIGH confidence)
- [sqlite-vec GitHub releases](https://github.com/asg017/sqlite-vec/releases) -- v0.1.7-alpha.10 verified (HIGH confidence)
- [Alex Garcia: Hybrid Search with sqlite-vec + FTS5](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) -- hybrid search patterns (HIGH confidence)
- [sentence-transformers PyPI](https://pypi.org/project/sentence-transformers/) -- version 5.2.3 verified (HIGH confidence)
- [Anthropic SDK PyPI](https://pypi.org/project/anthropic/) -- version 0.83.0 verified (HIGH confidence)
- [Ollama Python PyPI](https://pypi.org/project/ollama/) -- version 0.6.1 verified (HIGH confidence)
- [NetworkX PyPI](https://pypi.org/project/networkx/) -- version 3.6.1 verified (HIGH confidence)
- [edge-tts PyPI](https://pypi.org/project/edge-tts/) -- version 7.2.7 verified (HIGH confidence)
- [LiteLLM PyPI](https://pypi.org/project/litellm/) -- version 1.81.14 verified, evaluated and rejected (HIGH confidence)
- [pydantic-settings PyPI](https://pypi.org/project/pydantic-settings/) -- version 2.13.1 verified (HIGH confidence)
- [APScheduler PyPI](https://pypi.org/project/APScheduler/) -- version 3.11.2 verified (HIGH confidence)
- [numpy PyPI](https://pypi.org/project/numpy/) -- version 2.4.2 verified (HIGH confidence)
- [BentoML: Best Open-Source Embedding Models 2026](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) -- embedding comparison (MEDIUM confidence)
- [HN: Don't use all-MiniLM-L6-v2](https://news.ycombinator.com/item?id=46081800) -- community evidence against MiniLM (MEDIUM confidence)
- [AIMultiple: Open Source Embedding Models Benchmark](https://research.aimultiple.com/open-source-embedding-models/) -- benchmark data (MEDIUM confidence)
- [LiteLLM alternatives analysis](https://www.truefoundry.com/blog/litellm-alternatives) -- proxy overhead concerns (MEDIUM confidence)
- [Nomic Embed Text v1.5 HuggingFace](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) -- model specifications (HIGH confidence)

---
*Stack research for: Jarvis local-first AI personal assistant*
*Researched: 2026-02-22*
