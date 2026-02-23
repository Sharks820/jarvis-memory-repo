# Jarvis — Limitless Personal AI Assistant

## What This Is

A local-first, always-learning personal AI assistant inspired by Iron Man's J.A.R.V.I.S., built for Conner. Jarvis manages day-to-day life (calendar, email, tasks, bills, health, school, family, gaming), learns continuously from every interaction, syncs seamlessly between desktop PC and Samsung Galaxy S25 Ultra, and speaks with a British male neural voice with mild humor. It is designed to become an all-inclusive assistant with no limitations on its knowledge depth.

## Core Value

Jarvis learns from everything it ingests, never forgets, never regresses, and becomes more useful every single day without constant maintenance.

## Requirements

### Validated

- Validated: CLI command interface with 30+ commands — existing
- Validated: Edge-TTS British male neural voice (en-GB-ThomasNeural) — existing
- Validated: Windows Speech fallback TTS — existing
- Validated: HMAC-signed mobile API with replay protection — existing
- Validated: Owner guard with master password and trusted device management — existing
- Validated: Tiered capability authorization (read/bounded_write/privileged) — existing
- Validated: Ollama-based local code generation with quality profiles — existing
- Validated: Daemon mode with idle detection and gaming auto-pause — existing
- Validated: Branch-based memory filing (9 branches) — existing (needs upgrade)
- Validated: Content-hash deduplication (SHA-256) — existing
- Validated: 125 passing tests — existing

### Active

- [ ] Revolutionary brain memory system with SQLite + embeddings + semantic search
- [ ] Anti-regression locks that permanently protect learned knowledge
- [ ] Continuous learning engine that extracts and retains knowledge from every interaction
- [ ] Multi-model intelligence routing (Opus for reasoning, best-in-class for each task type)
- [ ] Bidirectional mobile-desktop learning sync with conflict resolution
- [ ] J.A.R.V.I.S.-quality personality with contextual humor and natural conversation
- [ ] Real connector integrations (calendar, email, tasks, bills — not stubs)
- [ ] Self-improving capability growth with auditable verification
- [ ] Streaming voice with natural cadence and personality-aware responses
- [ ] Knowledge graph with fact interconnection and contradiction detection

### Out of Scope

- Cloud-hosted deployment — local-first is non-negotiable for privacy and control
- Training custom LLMs from scratch — use best available models via API + local inference
- Mobile native app (for now) — mobile HTTP API + quick-access web panel is sufficient
- Multi-user support — this is Conner's personal assistant, single-owner by design

## Context

- **Runtime**: Windows 11 desktop PC (primary), Samsung Galaxy S25 Ultra (mobile/secondary)
- **Existing codebase**: 29 Python source files, 125 passing tests, monolithic main.py (~31k tokens)
- **Current state**: Skeleton is solid but brain is hollow — memory system lacks real search/retrieval, no embeddings, no database, no semantic understanding. Branch-based filing works but uses keyword matching instead of semantic classification. Ingestion pipeline is too thin (no chunking, no enrichment). Connectors are stubs.
- **Voice**: Edge-TTS with British male voices working well. Needs personality layer on top.
- **Models**: Currently Ollama only. User wants Opus/best-in-class models for reasoning and learning.
- **Previous work**: Senior architecture review proposed 6-layer memory hierarchy but was never implemented.

## Constraints

- **Privacy**: All core data stays local. Cloud APIs used for inference only, never for storage.
- **Platform**: Windows 11 primary. Must work without Docker or Linux dependencies.
- **Dependencies**: Minimize external services. SQLite over Postgres. Local embeddings preferred.
- **Python**: >=3.10, existing setuptools build system.
- **Budget**: Use cloud APIs strategically (Opus for complex reasoning, cheaper models for simple tasks).
- **Regression**: No change may cause previously-learned knowledge to be lost or degraded.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SQLite + FTS5 for memory storage | No external DB server needed, great Python support, full-text search built-in | — Pending |
| Local embeddings (sentence-transformers) for semantic search | Privacy-first, no API calls for retrieval, fast inference | — Pending |
| Claude Opus for complex reasoning, Sonnet for routine tasks | Best-in-class reasoning when it matters, cost-efficient for simple work | — Pending |
| Edge-TTS for voice (keep existing) | Already working well, high-quality British neural voices | Validated |
| Branch-based filing upgraded to semantic classification | Preserves existing mental model, adds intelligence to filing | — Pending |
| Bidirectional sync via encrypted diff-based protocol | Efficient bandwidth, conflict resolution, works on mobile data | — Pending |
| Desktop PC primary, S25 Ultra mobile secondary | Two-device setup, sync between them | — Pending |

---
*Last updated: 2026-02-22 after GSD initialization*
