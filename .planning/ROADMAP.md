# Roadmap: Jarvis -- Limitless Personal AI Assistant

## Overview

Jarvis is a brownfield project with 29 existing Python source files and 125 passing tests. The codebase has a solid skeleton but a hollow brain -- memory is flat JSONL with keyword matching, connectors are stubs, and the monolithic main.py routes everything inline. This roadmap transforms Jarvis from a command runner into an always-learning personal AI that never forgets. Phase 1 is the foundation: decomposing the architecture and building a real memory system with SQLite, embeddings, and semantic search. Every subsequent phase builds on that foundation -- knowledge graph, intelligence routing, real connectors, voice personality, knowledge harvesting, continuous learning, device sync, and proactive intelligence.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Memory Revolution and Architecture** - Decompose monolithic main.py via Command Bus, migrate to SQLite + FTS5 + sqlite-vec with semantic search, enriched ingestion pipeline, and zero-loss data migration
- [ ] **Phase 2: Knowledge Graph and Anti-Regression** - Build fact extraction with NetworkX backed by SQLite, implement fact locks that prevent knowledge loss, contradiction quarantine, and regression verification
- [ ] **Phase 3: Intelligence Routing** - Unified model gateway for Ollama + Anthropic, intent-based complexity routing, fallback chains, and per-query cost tracking
- [ ] **Phase 4: Connectors and Daily Intelligence** - Real calendar, email, and task integrations replacing stubs, combined into a genuinely useful daily briefing
- [ ] **Phase 5: Knowledge Harvesting** - Multi-source knowledge extraction from MiniMax, Kimi, Claude Code, Codex, and Gemini with deduplication, validation, and budget controls
- [ ] **Phase 6: Voice and Personality** - Persona layer with British butler character and contextual tone adaptation, plus Whisper-grade speech-to-text for voice commands
- [ ] **Phase 7: Continuous Learning and Self-Improvement** - Knowledge extraction from every interaction, cross-branch fact reasoning, temporal metadata on facts, and golden task evaluation
- [ ] **Phase 8: Mobile-Desktop Sync** - Changelog-based bidirectional sync between desktop PC and Samsung Galaxy S25 Ultra with field-level conflict resolution and encrypted transport
- [ ] **Phase 9: Proactive Intelligence and Polish** - Proactive assistance that surfaces info before being asked, wake word activation, progressive cost reduction, and adversarial self-testing

## Phase Details

### Phase 1: Memory Revolution and Architecture
**Goal**: Jarvis has a real brain -- semantic search finds what you meant (not just what you said), all memory lives in a queryable database, and the codebase is decomposed into maintainable modules that all 125+ tests still pass against
**Depends on**: Nothing (first phase)
**Requirements**: ARCH-01, ARCH-02, ARCH-03, ARCH-04, ARCH-05, ARCH-06, MEM-01, MEM-02, MEM-03, MEM-04, MEM-05, MEM-06, MEM-07, MEM-08
**Success Criteria** (what must be TRUE):
  1. User can ask a natural language question and get semantically relevant memory results (not just keyword matches) -- e.g., asking "what medications do I take" finds records about prescriptions even if they never use the word "medications"
  2. All existing JSONL/JSON memory data has been migrated into SQLite with zero records lost, verified by count comparison and spot-check queries
  3. CLI commands, mobile API endpoints, and daemon loop all dispatch through the same Command Bus -- no business logic lives in interface code
  4. Memory records are automatically classified into the correct branch (ops, coding, health, etc.) using embedding similarity rather than keyword rules
  5. All 125+ existing tests pass without modification to test assertions (adapter shims are acceptable)
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md -- Command Bus architecture and main.py decomposition
- [ ] 01-02-PLAN.md -- SQLite + FTS5 + sqlite-vec memory engine with hybrid search
- [ ] 01-03-PLAN.md -- Enriched ingestion pipeline and data migration

### Phase 2: Knowledge Graph and Anti-Regression
**Goal**: Jarvis builds a web of interconnected facts from everything it ingests, protects confirmed knowledge with immutable locks, and can prove nothing has been lost between sessions
**Depends on**: Phase 1
**Requirements**: KNOW-01, KNOW-02, KNOW-03, KNOW-04
**Success Criteria** (what must be TRUE):
  1. Facts extracted from ingested content appear as nodes in the knowledge graph with typed relationships connecting them
  2. A fact confirmed by the owner or verified by multiple sources becomes locked and cannot be silently overwritten -- attempting to contradict it results in a quarantined "pending contradiction" for owner review
  3. Running a regression report compares knowledge counts and fact integrity against a previous signed snapshot and reports any discrepancies
  4. Owner can review and resolve quarantined contradictions via a CLI command (accept new fact, keep old fact, or merge)
**Plans**: TBD

Plans:
- [ ] 02-01: NetworkX knowledge graph with SQLite persistence and fact extraction
- [ ] 02-02: Fact locks, contradiction quarantine, and regression verification

### Phase 3: Intelligence Routing
**Goal**: Jarvis routes queries to the right model for the job -- Opus for complex reasoning, Sonnet for routine summarization, local Ollama for simple or private tasks -- with transparent cost tracking
**Depends on**: Phase 1
**Requirements**: INTL-01, INTL-02, INTL-03, INTL-04
**Success Criteria** (what must be TRUE):
  1. User can send a query and it is automatically routed to the appropriate model (Opus, Sonnet, or Ollama) based on complexity classification, without the user specifying which model to use
  2. If the Anthropic API is unavailable, queries gracefully fall back to local Ollama with a notification to the user rather than an error
  3. Per-query cost is logged in SQLite and the user can view a cost summary showing spend by model and time period
  4. Simple private queries (e.g., "what's on my calendar") never leave the machine -- they are handled by local Ollama
**Plans**: TBD

Plans:
- [ ] 03-01: Model gateway with unified interface and fallback chains
- [ ] 03-02: Intent classifier and cost tracking

### Phase 4: Connectors and Daily Intelligence
**Goal**: Jarvis knows the owner's real schedule, real emails, and real tasks -- and combines them into a morning briefing that is genuinely useful for planning the day
**Depends on**: Phase 1, Phase 3
**Requirements**: CONN-01, CONN-02, CONN-03, CONN-04
**Success Criteria** (what must be TRUE):
  1. Daily briefing includes real calendar events pulled from Google Calendar or an ICS feed (not stub/mock data)
  2. Daily briefing includes a summary of recent emails triaged by importance, pulled from a real IMAP inbox
  3. Daily briefing includes actual pending tasks from an integrated task source
  4. The combined daily briefing weaves together calendar, email, tasks, medications, and relevant memory context into a coherent narrative the owner can act on
**Plans**: TBD

Plans:
- [ ] 04-01: Calendar and task connector integration
- [ ] 04-02: Email connector and unified daily briefing

### Phase 5: Knowledge Harvesting
**Goal**: Jarvis can actively learn from multiple AI sources -- asking MiniMax, Kimi, Claude, Codex, and Gemini about topics and distilling their knowledge into its own permanent memory
**Depends on**: Phase 1, Phase 2, Phase 3
**Requirements**: HARV-01, HARV-02, HARV-03, HARV-04, HARV-05, HARV-06, HARV-07
**Success Criteria** (what must be TRUE):
  1. User can instruct Jarvis to research a topic and it queries at least three external AI sources (MiniMax, Kimi, Gemini) to gather knowledge
  2. Harvested knowledge is deduplicated against existing facts and ingested through the standard memory pipeline (not a separate storage path)
  3. Contradictions between harvested knowledge and existing locked facts are quarantined for review rather than silently accepted
  4. Cost tracking shows per-source API spend and the user can set daily/monthly budget limits that halt harvesting when exceeded
**Plans**: TBD

Plans:
- [ ] 05-01: MiniMax, Kimi, and Gemini API harvesters
- [ ] 05-02: Claude Code and Codex session ingestors with dedup and budget controls

### Phase 6: Voice and Personality
**Goal**: Jarvis speaks with a distinctive British butler personality that adapts its tone to context, and can listen to voice commands with accuracy matching the Whisper app
**Depends on**: Phase 1
**Requirements**: VOICE-01, VOICE-02, VOICE-03, VOICE-04
**Success Criteria** (what must be TRUE):
  1. Jarvis responses carry a distinct British butler personality with contextual mild humor -- responses about gaming are lighter than responses about health or finance
  2. Voice output uses Edge-TTS with streaming chunked playback (existing behavior preserved and enhanced with personality-aware phrasing)
  3. User can speak a voice command and it is transcribed with accuracy comparable to the Whisper desktop app, then executed as if typed
  4. Persona tone adapts based on the branch/domain of the query: professional for health and finance, warm for family, light humor for gaming and casual topics
**Plans**: TBD

Plans:
- [ ] 06-01: Persona integration with contextual tone adaptation
- [ ] 06-02: Whisper-grade speech-to-text pipeline

### Phase 7: Continuous Learning and Self-Improvement
**Goal**: Jarvis extracts and permanently retains knowledge from every interaction, connects facts across life domains for cross-domain reasoning, and can measurably prove it is getting smarter over time
**Depends on**: Phase 1, Phase 2, Phase 3
**Requirements**: GROW-01, GROW-02, KNOW-05, KNOW-06
**Success Criteria** (what must be TRUE):
  1. After a conversation about a new topic, Jarvis can answer questions about that topic in a future session -- knowledge persists without the user explicitly saving anything
  2. Cross-branch queries work: asking "do any of my medications conflict with my gaming schedule" finds relationships between health-branch facts and gaming-branch facts
  3. Golden task evaluation shows capability scores that demonstrably improve over time, with an auditable history of eval runs
  4. Facts have temporal metadata: permanent facts (pharmacy hours) are distinguished from time-sensitive facts (milk expiration), and expired information is automatically flagged
**Plans**: TBD

Plans:
- [ ] 07-01: Continuous learning engine with automatic knowledge extraction
- [ ] 07-02: Cross-branch reasoning and golden task evaluation

### Phase 8: Mobile-Desktop Sync
**Goal**: Knowledge learned on the phone is available on the desktop and vice versa, with efficient incremental sync and automatic conflict resolution
**Depends on**: Phase 1, Phase 2
**Requirements**: SYNC-01, SYNC-02, SYNC-03, SYNC-04, SYNC-05
**Success Criteria** (what must be TRUE):
  1. After ingesting knowledge on the mobile device, that knowledge appears on the desktop after the next sync cycle (and vice versa)
  2. Only changes since the last sync are transmitted -- not the full database state -- verified by measuring payload size relative to total database size
  3. If the same record is modified on both devices between syncs, field-level conflict resolution merges the changes with desktop as authoritative for ties
  4. Sync payloads are encrypted in transit so intercepted network traffic reveals no readable memory content
**Plans**: TBD

Plans:
- [ ] 08-01: Changelog table and diff-based sync engine
- [ ] 08-02: Encrypted transport and conflict resolution

### Phase 9: Proactive Intelligence and Polish
**Goal**: Jarvis acts before being asked -- surfacing relevant information at the right time -- and demonstrates measurable, ongoing self-improvement with reducing cloud costs
**Depends on**: Phase 4, Phase 7, Phase 8
**Requirements**: CONN-05, VOICE-05, INTL-05, GROW-03, GROW-04
**Success Criteria** (what must be TRUE):
  1. Jarvis proactively surfaces relevant information without being asked: bill due alerts, medication reminders, meeting prep notes appear at appropriate times
  2. Saying "Jarvis" from across the room activates voice input mode (wake word detection), enabling hands-free interaction
  3. The percentage of queries answered locally (without cloud API calls) measurably increases over time as local knowledge grows
  4. Adversarial self-testing periodically quizzes Jarvis on retained knowledge and alerts the owner if recall accuracy drops below baseline
**Plans**: TBD

Plans:
- [ ] 09-01: Proactive assistance system and wake word detection
- [ ] 09-02: Progressive cost reduction and adversarial self-testing

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9

Note: Phases 3, 6, and 8 depend only on Phase 1 and could theoretically run in parallel after Phase 1 completes. The linear order above represents the recommended sequence for a solo developer.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Memory Revolution and Architecture | 1/3 | In progress | - |
| 2. Knowledge Graph and Anti-Regression | 0/2 | Not started | - |
| 3. Intelligence Routing | 0/2 | Not started | - |
| 4. Connectors and Daily Intelligence | 0/2 | Not started | - |
| 5. Knowledge Harvesting | 0/2 | Not started | - |
| 6. Voice and Personality | 0/2 | Not started | - |
| 7. Continuous Learning and Self-Improvement | 0/2 | Not started | - |
| 8. Mobile-Desktop Sync | 0/2 | Not started | - |
| 9. Proactive Intelligence and Polish | 0/2 | Not started | - |
