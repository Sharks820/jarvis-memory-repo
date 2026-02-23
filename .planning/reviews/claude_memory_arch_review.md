Here are 12 concise bullets for anti-regression AI memory architecture:

- Memory drift: accumulated observations can silently contradict earlier facts, causing the system to act on stale or conflicting state without detection.
- Context window overflow: unbounded memory retrieval floods the prompt and displaces task-relevant information.
- Silent regression: changes to memory storage/retrieval/ranking can break behavior without explicit error signals.
- L1 episodic session memory: short-lived, conversation-scoped working memory with automatic expiry.
- L2 semantic project memory: persistent knowledge graph per project storing entities, relations, and provenance timestamps.
- L3 procedural global memory: cross-project learned patterns/preferences with decay scoring.
- Consistency gateway: write-time contradiction detection against existing facts before commit.
- Round-trip fidelity tests: store/retrieve/compare checks across schema migrations.
- Contradiction detection tests: inject conflicting facts and measure false negatives.
- Retrieval ranking tests: golden query sets with expected ordering and drift thresholds.
- Phase 1 instrument: add structured logs and content hashes for all memory operations.
- Phase 2 harden: deploy consistency gateway + CI gates on full memory regression suite.
