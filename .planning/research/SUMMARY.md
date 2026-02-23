# Project Research Summary

**Project:** Jarvis Local-First AI Personal Assistant
**Domain:** Local-first AI personal assistant with intelligent memory, multi-model LLM routing, semantic search, knowledge graphs, and voice synthesis
**Researched:** 2026-02-22
**Confidence:** MEDIUM-HIGH

## Executive Summary

Jarvis is a local-first, single-owner AI personal assistant that aims to surpass commercial alternatives (Siri, Alexa, Gemini, ChatGPT) through deep personalization, persistent semantic memory, and verifiable self-improvement. The existing codebase has 29 Python source files with a working CLI, mobile API, voice synthesis, persona layer, and basic memory -- but the architecture is monolithic (31k-token main.py god object) and memory is backed by flat JSONL files with keyword-only search. The single most important transformation is replacing this flat memory with a SQLite-backed semantic memory system using local embeddings, which unlocks every advanced feature downstream.

The recommended approach is an incremental modular monolith refactoring: decompose main.py via a Command Bus pattern, migrate storage to SQLite with FTS5 full-text search and sqlite-vec vector search in a single database file, add local embeddings via sentence-transformers with the nomic-embed-text-v1.5 model, and layer on a NetworkX knowledge graph persisted to SQLite. The stack is deliberately minimal -- direct Anthropic and Ollama SDKs instead of LiteLLM/LangChain, SQLite instead of ChromaDB/Pinecone, and timestamp-based sync instead of CRDTs. Every choice optimizes for a single-user, single-machine, privacy-first deployment on Windows 11.

The primary risks are: (1) the big-bang temptation -- trying to refactor architecture and add embeddings simultaneously instead of incrementally, (2) choosing outdated embedding models (all-MiniLM-L6-v2 appears in most tutorials but is objectively inferior), (3) over-engineering sync for a two-device setup, and (4) losing existing functionality during the monolith decomposition. Mitigation strategy: dual-write during migration (JSONL and SQLite simultaneously), keep all 125 tests passing at every step, and defer advanced features (behavioral model, cross-domain reasoning, emotional awareness) until the memory foundation is proven stable.

## Key Findings

### Recommended Stack

The stack centers on SQLite as the unified storage layer. By combining SQLite core, FTS5 full-text search, and the sqlite-vec extension, all structured data, keyword indexes, and vector embeddings live in a single file with transactional consistency, single-backup semantics, and hybrid search via SQL. This eliminates the need for ChromaDB, FAISS, or any external vector database.

**Core technologies:**
- **Python >=3.11**: Runtime language. Performance gains over 3.10, required by NetworkX 3.6+, provides tomllib and ExceptionGroup in stdlib.
- **SQLite + FTS5 + sqlite-vec >=0.1.6**: Unified storage for structured data, full-text search, and vector similarity search. Single-file, zero-config, ACID-compliant.
- **sentence-transformers >=5.0 + nomic-embed-text-v1.5**: Local embedding model. 768-dim, 8192 token context, Apache 2.0. Vastly superior to MiniLM-L6-v2 (81% vs 56% Top-5 retrieval accuracy).
- **anthropic >=0.80 + ollama >=0.4**: Direct SDKs for two-provider LLM routing. No LiteLLM proxy overhead needed.
- **NetworkX >=3.4**: In-memory knowledge graph with SQLite persistence. Sufficient for <1M nodes.
- **edge-tts >=7.2.7**: Neural TTS already in production. No changes needed.
- **FastAPI >=0.115 + uvicorn**: Mobile API server (upgrade from ThreadingHTTPServer).
- **pydantic-settings >=2.13**: Typed, validated configuration to replace ad-hoc config.py.
- **APScheduler >=3.11 + watchdog >=6.0**: Background task scheduling and file system monitoring.

### Expected Features

**Must have (table stakes):**
- SQLite + FTS5 memory database replacing JSONL flat files
- Local embeddings for semantic memory search
- At least one real connector (Calendar) to power daily briefings
- Enhanced multi-model routing (Opus for reasoning, Sonnet for routine, Ollama for local/privacy)
- Anti-regression verification via signed snapshots with knowledge integrity checks
- Natural language conversation layer on top of existing command structure

**Should have (differentiators -- v1.x):**
- Three-tier memory hierarchy (STM/MTM/LTM) with heat-driven promotion/eviction
- Knowledge graph with contradiction detection and fact locking
- Continuous learning engine extracting knowledge from every interaction
- Proactive assistance system (cron-driven morning briefs, medication reminders, calendar prep)
- Email connector (IMAP) following the calendar connector pattern
- Wake word + voice input (Whisper STT)
- Enhanced personality layer with contextual humor

**Defer (v2+):**
- Owner behavioral model (needs months of interaction data)
- Cross-domain reasoning (needs populated knowledge graph + multiple connectors)
- Emotional context awareness (needs behavioral model)
- Temporal knowledge with decay
- Adversarial self-testing at scale
- Full bidirectional mobile sync with encrypted protocol
- Real-time streaming voice conversation

### Architecture Approach

The recommended architecture is a modular monolith with four layers: Interface (CLI, Mobile API, Widget, Daemon), Command Bus (mediator dispatching typed commands to handlers), Service (Memory, Intelligence Router, Voice/Persona, Connectors), and Core (Memory Engine, Knowledge Graph, Model Gateway, Sync Engine, Security Gate) -- all backed by a unified SQLite storage layer. The decomposition from the current god-object main.py should be done incrementally over 10 steps, keeping tests green at every step.

**Major components:**
1. **Command Bus** -- Mediator pattern replacing main.py's 30+ command if/elif chain. All interfaces produce Command dataclasses; handlers contain business logic.
2. **Memory Engine** -- SQLite + FTS5 + sqlite-vec hybrid search with reciprocal rank fusion. Tiered storage (hot/warm/cold) with automatic promotion/demotion.
3. **Knowledge Graph** -- NetworkX in-memory graph persisted to SQLite. Fact locking, contradiction detection, anti-regression verification.
4. **Intelligence Router** -- Intent classification driving model selection. Unified Model Gateway abstracting Ollama and Anthropic behind a common interface.
5. **Sync Engine** -- Changelog-based bidirectional sync with field-level merge and last-writer-wins conflict resolution. Desktop authoritative.
6. **Voice/Persona Pipeline** -- Merged persona composition and Edge-TTS streaming into a single subsystem for personality-aware speech.

### Critical Pitfalls

These are synthesized from warnings across STACK, FEATURES, and ARCHITECTURE research (no separate PITFALLS.md was produced).

1. **Using outdated embedding models (all-MiniLM-L6-v2)** -- Despite appearing in most tutorials, this 2019 model has only 28% Top-1 retrieval accuracy and a 512-token context limit. Use nomic-embed-text-v1.5 instead (8192 tokens, 81% Top-5 accuracy). This is a decision you cannot easily reverse once embeddings are generated.

2. **Over-engineering with LiteLLM/LangChain** -- For a two-provider setup (Anthropic + Ollama), adding LiteLLM introduces 8ms+ P95 latency, 50+ transitive dependencies, and YAML config complexity. LangChain adds 100+ dependencies and hides prompt engineering behind opaque abstractions. Use direct SDKs with a custom 50-line router.

3. **Keeping the monolithic main.py** -- At 31k tokens, main.py knows about every module and wires everything together inline. Every change touches the same file. Decompose via Command Bus pattern incrementally, not via big-bang rewrite.

4. **Using CRDTs for two-device sync** -- CRDTs solve multi-writer conflicts that do not exist for single-owner, two-device sync. They add massive implementation complexity. Use timestamp-based last-writer-wins with desktop as authoritative.

5. **Letting interfaces access storage directly** -- CLI handlers querying SQLite directly and mobile API writing JSONL creates duplicated business rules and makes schema changes dangerous. All storage access must go through the service layer via Command Bus.

6. **Loading embedding model at import time or per-request** -- Import-time loading delays startup for all commands. Per-request loading adds ~2s latency. Use lazy-loaded singleton via DI container: first call loads the model, subsequent calls reuse it.

7. **Trying to build smart home control, custom LLM training, or real-time streaming conversation** -- These are anti-features that fragment focus. Jarvis's strength is knowledge and life management, not hardware control. RAG + prompting achieves 90% of fine-tuning benefit at 1% cost. Full duplex voice is a v3+ feature.

## Implications for Roadmap

Based on research, the following phase structure reflects dependency ordering, architectural constraints, and risk mitigation.

### Phase 1: Foundation -- Architecture Refactoring and SQLite Migration
**Rationale:** Everything depends on the memory system, and the memory system cannot be properly built on top of a monolithic main.py. The architecture must be decomposed first, then storage migrated to SQLite. These are the lowest-risk, highest-impact changes.
**Delivers:** Command Bus pattern, modular project structure, SQLite database with FTS5 full-text search, migration of all JSONL data to SQLite, dual-write period for validation.
**Addresses:** Table stakes (persistent memory, queryable storage), anti-pattern removal (god module, direct storage access).
**Avoids:** Big-bang rewrite risk -- each step keeps 125 tests passing. Dual-write prevents data loss during migration.

### Phase 2: Semantic Intelligence -- Embeddings and Enhanced Routing
**Rationale:** With SQLite in place, adding sqlite-vec and local embeddings transforms memory from keyword matching to semantic understanding. This is the single highest-value feature upgrade. Multi-model routing can be enhanced in parallel since it is architecturally independent.
**Delivers:** Local embedding generation (nomic-embed-text-v1.5), sqlite-vec vector storage, hybrid search (FTS5 + embedding + recency via RRF), enhanced model routing (Opus/Sonnet/Ollama with cost tracking).
**Uses:** sentence-transformers, sqlite-vec, anthropic SDK, ollama SDK.
**Implements:** Memory Engine hybrid search, Intelligence Router, Model Gateway.

### Phase 3: Knowledge Layer -- Graph, Facts, and Anti-Regression
**Rationale:** With semantic search working, the knowledge graph can leverage embeddings for contradiction detection (semantic similarity, not just exact key match). Anti-regression locks depend on a proper fact store. This phase builds the intelligence that differentiates Jarvis from every commercial assistant.
**Delivers:** NetworkX knowledge graph persisted to SQLite, typed fact relationships, contradiction detection with quarantine for conflicts, fact locking policy, anti-regression verification with signed snapshots.
**Addresses:** Differentiators (knowledge graph, anti-regression locks, contradiction detection).

### Phase 4: Real-World Connectors and Proactive Intelligence
**Rationale:** The memory and knowledge systems are now ready to receive real-world data. Calendar integration is the single most impactful connector (enables daily briefings). Proactive assistance requires connectors + memory + scheduling to all be working.
**Delivers:** Real calendar connector (Google Calendar / ICS), enhanced daily briefing with memory context, medication reminder system, proactive assistance framework (APScheduler-driven), file system auto-ingestion (watchdog).
**Addresses:** Table stakes (calendar awareness, medication reminders, daily briefing), differentiators (proactive assistance).

### Phase 5: Continuous Learning and Enriched Ingestion
**Rationale:** With connectors feeding real data and the knowledge graph storing facts, the ingestion pipeline can be enriched to extract entities, classify branches semantically, and feed the continuous learning loop. This is where Jarvis starts getting meaningfully smarter with use.
**Delivers:** Enriched ingestion pipeline (chunking, entity extraction, semantic branch classification), continuous learning engine, learning mission integration with permanent memory, three-tier memory hierarchy (hot/warm/cold).
**Addresses:** Differentiators (continuous learning, three-tier memory, self-improvement).

### Phase 6: Voice, Persona, and Mobile Sync
**Rationale:** These are polish and accessibility features that benefit from a mature memory system. Voice input requires the query pipeline to work well. Sync requires the memory format to be finalized. Persona enhancement benefits from rich context.
**Delivers:** Wake word detection + Whisper STT for voice input, merged persona/TTS streaming pipeline, encrypted changelog-based mobile-desktop sync, pydantic-settings configuration upgrade.
**Addresses:** Differentiators (voice-activated ambient mode, personality layer, bidirectional sync).

### Phase 7: Advanced Intelligence (v2+)
**Rationale:** These features require months of accumulated interaction data and a mature, stable platform. Attempting them earlier wastes effort on systems that cannot function without data.
**Delivers:** Owner behavioral model, cross-domain reasoning, temporal knowledge with decay, adversarial self-testing, emotional context awareness.
**Addresses:** Revolutionary features that require accumulated data and proven infrastructure.

### Phase Ordering Rationale

- **Phases 1-2 are strictly sequential**: Architecture refactoring must precede embeddings because the embedding service needs a proper DI container and SQLite storage layer to integrate into.
- **Phase 3 depends on Phase 2**: Contradiction detection uses embedding similarity. Fact locking builds on SQLite-backed fact storage.
- **Phase 4 depends on Phases 1-3**: Connectors need the memory service layer (Phase 1), semantic ingestion (Phase 2), and knowledge extraction (Phase 3) to be valuable.
- **Phase 5 depends on Phase 4**: Continuous learning needs real data flowing from connectors to have material to learn from.
- **Phase 6 is largely independent** but benefits from stable memory format (sync) and rich context (persona). Voice input is architecturally independent but lower priority than core intelligence.
- **Phase 7 requires accumulated data**: Behavioral models need months of interactions. Cross-domain reasoning needs multiple populated knowledge domains.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (Embeddings):** Embedding model loading strategy on Windows 11, CPU-only PyTorch installation, nomic-embed-text-v1.5 performance benchmarking on target hardware. Need to validate 50ms per-embedding claim on the actual machine.
- **Phase 4 (Connectors):** Google Calendar API OAuth flow on Windows desktop app (not web app), IMAP authentication for email providers (Gmail app passwords vs OAuth2). These integration patterns are provider-specific.
- **Phase 6 (Voice Input):** Wake word detection library selection (Porcupine licensing, OpenWakeWord alternatives), Whisper model size tradeoff on CPU. Sparse documentation for Windows-specific audio input pipelines.
- **Phase 6 (Sync):** Encrypted transport design, sync conflict resolution edge cases, mobile-to-desktop authentication handshake. No established pattern for this specific two-device scenario.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Architecture):** Command Bus / mediator pattern is well-documented. SQLite migration from JSONL is straightforward. Existing tests provide safety net.
- **Phase 3 (Knowledge Graph):** NetworkX usage for knowledge graphs is well-documented in 2025 tutorials. Fact locking is a simple policy pattern.
- **Phase 5 (Learning):** The learning missions and growth tracker modules already exist. Enriched ingestion follows established RAG pipeline patterns.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All package versions verified via PyPI as of 2026-02-22. Embedding model comparison based on multiple independent benchmarks. |
| Features | HIGH | Competitive landscape well-documented. Feature prioritization aligns with dependency analysis. |
| Architecture | HIGH | Existing codebase analyzed directly. Patterns verified against official docs and multiple architectural sources. |
| Pitfalls | MEDIUM | Synthesized from warnings across three research documents rather than dedicated pitfall research. Coverage may have gaps in operational/deployment pitfalls. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Windows-specific deployment pitfalls:** Research focused on library choices and architecture, but Windows 11 has specific gotchas (path length limits, file locking, service installation, audio device management) that were not deeply investigated. Validate during Phase 1 implementation.
- **PyTorch installation on Windows:** The CPU-only torch installation path (`--index-url https://download.pytorch.org/whl/cpu`) saves ~1.8GB but compatibility with sentence-transformers 5.x on Windows 11 needs hands-on validation.
- **sqlite-vec on Windows:** The extension is pip-installable and claims Windows support, but most documented usage is Linux/macOS. Validate SIMD acceleration availability on target machine.
- **Operational pitfalls (dedicated research missing):** No PITFALLS.md was produced. Common operational pitfalls like SQLite WAL mode configuration, APScheduler thread safety, FastAPI graceful shutdown on Windows, and edge-tts network dependency during outages were not systematically cataloged.
- **Memory budget validation:** The estimated ~600MB additional RAM (on top of Ollama) for embeddings + SQLite needs validation against actual machine specs, especially when Ollama models are loaded.
- **Embedding migration strategy:** When switching from keyword-based to semantic search, all existing brain records need embedding generation. The batch import time (~30 min for 100K records) and the strategy for handling records that existed before embeddings needs planning.

## Sources

### Primary (HIGH confidence)
- [sqlite-vec PyPI](https://pypi.org/project/sqlite-vec/) -- version 0.1.6 verified
- [sqlite-vec GitHub](https://github.com/asg017/sqlite-vec) -- official repo, hybrid search patterns
- [Alex Garcia: Hybrid Search with sqlite-vec + FTS5](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) -- hybrid search architecture
- [SQLite FTS5 Extension docs](https://www.sqlite.org/fts5.html) -- official full-text search documentation
- [sentence-transformers PyPI](https://pypi.org/project/sentence-transformers/) -- version 5.2.3 verified
- [Anthropic SDK PyPI](https://pypi.org/project/anthropic/) -- version 0.83.0 verified
- [Ollama Python PyPI](https://pypi.org/project/ollama/) -- version 0.6.1 verified
- [NetworkX PyPI](https://pypi.org/project/networkx/) -- version 3.6.1 verified
- [Nomic Embed Text v1.5 HuggingFace](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) -- model specifications
- [MemoryOS (EMNLP 2025 Oral)](https://github.com/BAI-LAB/MemoryOS) -- hierarchical memory architecture
- [Mem0 research paper](https://arxiv.org/abs/2504.19413) -- universal memory layer
- [Google Gemini Personal Intelligence](https://blog.google/innovation-and-ai/products/gemini-app/personal-intelligence/) -- competitive landscape
- [Local-first software -- Ink & Switch](https://www.inkandswitch.com/essay/local-first/) -- foundational design principles
- [Generative Agents memory architecture -- Stanford](https://arxiv.org/abs/2304.03442) -- tiered memory research
- [MIT Technology Review: Secure AI Assistants](https://www.technologyreview.com/2026/02/11/1132768/is-a-secure-ai-assistant-possible/) -- security principles

### Secondary (MEDIUM confidence)
- [BentoML: Best Open-Source Embedding Models 2026](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) -- embedding model comparison
- [HN: Don't use all-MiniLM-L6-v2](https://news.ycombinator.com/item?id=46081800) -- community evidence
- [LiteLLM alternatives analysis](https://www.truefoundry.com/blog/litellm-alternatives) -- proxy overhead concerns
- [Multi-LLM routing strategies -- TrueFoundry](https://www.truefoundry.com/blog/multi-model-routing) -- routing patterns
- [RouteLLM framework -- LMSys](https://github.com/lm-sys/RouteLLM) -- routing research
- [Stevens AI assistant](https://www.geoffreylitt.com/2025/04/12/how-i-made-a-useful-ai-assistant-with-one-sqlite-table-and-a-handful-of-cron-jobs) -- SQLite + cron architecture pattern
- [OpenClaw personal AI](https://github.com/openclaw/openclaw) -- local-first assistant patterns
- [Modular monolith in Python](https://breadcrumbscollector.tech/modular-monolith-in-python/) -- architecture patterns
- [Knowledge Graph of Thoughts](https://arxiv.org/abs/2504.02670) -- KG reasoning improvement
- [Multi-model routing cost optimization](https://www.swfte.com/blog/intelligent-llm-routing-multi-model-ai) -- 30-70% cost reduction evidence

### Tertiary (LOW confidence)
- [AI agent proactive trends 2026](https://www.salesmate.io/blog/future-of-ai-agents/) -- industry trend validation

---
*Research completed: 2026-02-22*
*Ready for roadmap: yes*
