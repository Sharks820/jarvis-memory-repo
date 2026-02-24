# Milestones

## v1.0 — Desktop Engine (Complete)

**Completed:** 2026-02-23
**Phases:** 1–9 (18 plans executed)
**Tests:** 473 passing
**Duration:** ~3 hours total execution

### What Shipped

- SQLite + FTS5 + sqlite-vec memory engine with semantic search
- Knowledge graph with fact extraction, contradiction detection, fact locks
- Intelligence routing (Ollama + Anthropic) with intent-based complexity routing
- Real calendar (ICS), email (IMAP), task (Todoist) connectors + daily briefing
- Multi-source knowledge harvesting (MiniMax, Kimi, Gemini, Claude Code, Codex)
- Persona layer with British butler personality + contextual tone
- Speech-to-text via faster-whisper
- Continuous learning engine with cross-branch reasoning + golden task eval
- Changelog-based mobile-desktop sync with encrypted transport
- Proactive intelligence with wake word detection + adaptive nudges
- Cost tracking with budget controls + adversarial self-testing
- HMAC-signed mobile API with owner guard + device trust

### Key Decisions

- SQLite for everything (memory, knowledge graph, cost tracking, sync changelog)
- Local embeddings via sentence-transformers (nomic-embed-text-v1.5)
- CQRS command bus architecture (70+ commands)
- Fernet encryption with PBKDF2HMAC for sync payloads

### Last Phase Number

Phase 9 (last plan: 09-02)
