# Requirements: Jarvis — Limitless Personal AI Assistant

**Defined:** 2026-02-22
**Core Value:** Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Memory System (MEM)

- [ ] **MEM-01**: All memory records stored in SQLite with FTS5 full-text search index
- [ ] **MEM-02**: All memory records have embedding vectors stored via sqlite-vec for semantic search
- [ ] **MEM-03**: Hybrid search (FTS5 keyword + embedding cosine + recency decay) returns relevant results for any natural language query
- [ ] **MEM-04**: Memory records are classified into branches (ops, coding, health, finance, security, learning, family, communications, gaming) using semantic classification instead of keyword matching
- [ ] **MEM-05**: Three-tier memory hierarchy (hot/warm/cold) with automatic promotion and demotion based on access patterns, recency, and confidence
- [ ] **MEM-06**: Ingestion pipeline chunks long content, extracts entities, generates embeddings, and classifies branch before storage
- [ ] **MEM-07**: Content-hash deduplication (SHA-256) prevents duplicate records (preserve existing behavior)
- [ ] **MEM-08**: Migration script imports all existing JSONL/JSON memory data into SQLite without data loss

### Anti-Regression & Knowledge (KNOW)

- [ ] **KNOW-01**: Facts extracted from ingested content are stored in a knowledge graph (NetworkX backed by SQLite)
- [ ] **KNOW-02**: Facts that reach locked status (high confidence, multiple sources, owner-confirmed) cannot be overwritten by lower-confidence information
- [ ] **KNOW-03**: Incoming facts that contradict locked facts are quarantined as "pending contradiction" for owner review
- [ ] **KNOW-04**: Regression report compares knowledge counts and fact integrity between signed snapshots to prove nothing was lost
- [ ] **KNOW-05**: Cross-branch fact relationships enable cross-domain reasoning queries
- [ ] **KNOW-06**: Temporal metadata on facts distinguishes permanent knowledge from time-sensitive information

### Multi-Source Knowledge Harvesting (HARV)

- [ ] **HARV-01**: Knowledge harvester can query MiniMax API to extract and distill knowledge on specified topics
- [ ] **HARV-02**: Knowledge harvester can query Kimi API to extract and distill knowledge on specified topics
- [ ] **HARV-03**: Knowledge harvester can ingest learning outputs from Claude Code sessions
- [ ] **HARV-04**: Knowledge harvester can ingest learning outputs from Codex sessions
- [ ] **HARV-05**: Knowledge harvester can query Gemini API (free tier) to extract and distill knowledge on specified topics
- [ ] **HARV-06**: Harvested knowledge is deduplicated, validated against existing facts, and ingested through the standard memory pipeline
- [ ] **HARV-07**: Cost tracking per API source with configurable budget limits per day/month

### Intelligence Routing (INTL)

- [ ] **INTL-01**: Model gateway provides unified interface to Ollama (local), Anthropic API (Claude Opus/Sonnet), and other cloud APIs
- [ ] **INTL-02**: Intent classifier routes queries by complexity: Opus for complex reasoning/coding, Sonnet for routine summarization, local Ollama for simple/private tasks
- [ ] **INTL-03**: Fallback chain handles API failures gracefully (cloud unavailable -> local Ollama)
- [ ] **INTL-04**: Cost tracking per-query stored in SQLite for budget monitoring
- [ ] **INTL-05**: Progressive cost reduction: as local knowledge grows, more queries can be answered locally without cloud API calls

### Voice & Personality (VOICE)

- [ ] **VOICE-01**: Edge-TTS British male neural voice (en-GB-ThomasNeural) output with streaming chunked playback (preserve existing)
- [ ] **VOICE-02**: Persona layer composes personality-aware responses with British butler character and contextual mild humor
- [ ] **VOICE-03**: Persona adapts tone by context: professional for health/finance, light humor for gaming/casual, warm for family
- [ ] **VOICE-04**: Whisper-grade speech-to-text processes voice commands with accuracy matching the Whisper app (local faster-whisper or cloud Whisper API)
- [ ] **VOICE-05**: Wake word detection ("Jarvis") enables hands-free voice activation from across the room

### Mobile-Desktop Sync (SYNC)

- [ ] **SYNC-01**: Changelog-based bidirectional sync between desktop PC and Samsung Galaxy S25 Ultra
- [ ] **SYNC-02**: Only changes since last sync are transmitted (not full database state)
- [ ] **SYNC-03**: Field-level conflict resolution with desktop as authoritative for ties
- [ ] **SYNC-04**: Sync payloads are encrypted in transit
- [ ] **SYNC-05**: Learning acquired on mobile is merged into desktop knowledge base and vice versa

### Connectors (CONN)

- [ ] **CONN-01**: Calendar connector reads real events from Google Calendar or ICS feed
- [ ] **CONN-02**: Email connector reads and triages messages via IMAP (read-only initially)
- [ ] **CONN-03**: Task connector integrates with actual task source (not just local file)
- [ ] **CONN-04**: Daily briefing combines real calendar events, email summaries, tasks, medications, and memory context into genuinely useful morning brief
- [ ] **CONN-05**: Proactive assistance system surfaces relevant info without being asked (bill due alerts, medication reminders, meeting prep)

### Architecture (ARCH)

- [ ] **ARCH-01**: Monolithic main.py decomposed into Command Bus pattern with thin interfaces, typed commands, and separate handlers
- [ ] **ARCH-02**: All interfaces (CLI, mobile API, daemon) produce Command objects dispatched through the same bus
- [ ] **ARCH-03**: Service layer mediates between interfaces and core storage — interfaces never access storage directly
- [ ] **ARCH-04**: Lazy-loaded embedding model (loads on first use, not at import time)
- [ ] **ARCH-05**: SQLite WAL mode with write serialization for concurrent access from daemon + API + CLI
- [ ] **ARCH-06**: All 125+ existing tests continue to pass after each migration step

### Self-Improvement (GROW)

- [ ] **GROW-01**: Continuous learning engine extracts knowledge from every interaction and permanently retains it
- [ ] **GROW-02**: Golden task evaluation system measures capability scores that demonstrably improve over time
- [ ] **GROW-03**: Adversarial self-testing periodically quizzes Jarvis on retained knowledge and alerts if recall accuracy drops
- [ ] **GROW-04**: Progressive cost reduction tracked: percentage of queries answered locally vs cloud API increases over time

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Intelligence

- **ADV-01**: Owner behavioral model that learns patterns, preferences, and routines from months of interaction data
- **ADV-02**: Emotional context awareness detecting owner's state from interaction patterns and adapting behavior
- **ADV-03**: Full duplex real-time voice conversation (interrupt handling, multi-turn voice dialogue)

### Extended Platform

- **PLAT-01**: PWA version of quick_access.html with offline support for mobile
- **PLAT-02**: Smart home integration via MCP protocol to Home Assistant (if owner requests)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Cloud-hosted deployment | Local-first is the core value proposition. Privacy is non-negotiable. |
| Custom LLM training/fine-tuning | RAG + prompting + memory achieves 90% of the benefit at 1% of the cost |
| Mobile native app | HTTP API + PWA web panel is sufficient for single user |
| Multi-user support | Single-owner assistant by design. Family gets limited read access via API. |
| Smart home control (native) | Solved problem. Out of scope for v1. MCP integration possible later. |
| LangChain / LiteLLM integration | Massive overhead for a 2-provider routing problem. Direct SDKs preferred. |
| General-purpose web browsing agent | CUA achieves only 38% success on real tasks. Too brittle. |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| MEM-01 | Phase 1 | Pending |
| MEM-02 | Phase 1 | Pending |
| MEM-03 | Phase 1 | Pending |
| MEM-04 | Phase 1 | Pending |
| MEM-05 | Phase 2 | Pending |
| MEM-06 | Phase 1 | Pending |
| MEM-07 | Phase 1 | Pending |
| MEM-08 | Phase 1 | Pending |
| KNOW-01 | Phase 2 | Pending |
| KNOW-02 | Phase 2 | Pending |
| KNOW-03 | Phase 2 | Pending |
| KNOW-04 | Phase 2 | Pending |
| KNOW-05 | Phase 3 | Pending |
| KNOW-06 | Phase 3 | Pending |
| HARV-01 | Phase 4 | Pending |
| HARV-02 | Phase 4 | Pending |
| HARV-03 | Phase 4 | Pending |
| HARV-04 | Phase 4 | Pending |
| HARV-05 | Phase 4 | Pending |
| HARV-06 | Phase 4 | Pending |
| HARV-07 | Phase 4 | Pending |
| INTL-01 | Phase 3 | Pending |
| INTL-02 | Phase 3 | Pending |
| INTL-03 | Phase 3 | Pending |
| INTL-04 | Phase 3 | Pending |
| INTL-05 | Phase 5 | Pending |
| VOICE-01 | Phase 5 | Pending |
| VOICE-02 | Phase 5 | Pending |
| VOICE-03 | Phase 5 | Pending |
| VOICE-04 | Phase 5 | Pending |
| VOICE-05 | Phase 6 | Pending |
| SYNC-01 | Phase 6 | Pending |
| SYNC-02 | Phase 6 | Pending |
| SYNC-03 | Phase 6 | Pending |
| SYNC-04 | Phase 6 | Pending |
| SYNC-05 | Phase 6 | Pending |
| CONN-01 | Phase 3 | Pending |
| CONN-02 | Phase 5 | Pending |
| CONN-03 | Phase 5 | Pending |
| CONN-04 | Phase 3 | Pending |
| CONN-05 | Phase 5 | Pending |
| ARCH-01 | Phase 1 | Pending |
| ARCH-02 | Phase 1 | Pending |
| ARCH-03 | Phase 1 | Pending |
| ARCH-04 | Phase 1 | Pending |
| ARCH-05 | Phase 1 | Pending |
| ARCH-06 | Phase 1 | Pending |
| GROW-01 | Phase 4 | Pending |
| GROW-02 | Phase 4 | Pending |
| GROW-03 | Phase 6 | Pending |
| GROW-04 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 49 total
- Mapped to phases: 49
- Unmapped: 0

---
*Requirements defined: 2026-02-22*
*Last updated: 2026-02-22 after GSD initialization*
