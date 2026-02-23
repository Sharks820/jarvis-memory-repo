# Jarvis Memory Core V2 (Anti-Regression)

## Objective
Build a long-horizon memory system that keeps learning continuously while preventing context drift, contradiction creep, and retrieval clutter.

## Design Principles
- Layered memory: short-term working context, episodic timeline, semantic facts, procedural preferences.
- Write-time safety: all updates pass dedupe + contradiction checks before becoming canonical.
- Retrieval discipline: context packets are compact, diversity-limited, and scored for relevance + recency.
- Verifiability: facts keep provenance and conflict history.
- Measurability: regression health metrics are first-class and always queryable.

## Implemented Architecture
1. Event Memory (`.planning/events.jsonl`): raw append-only operation history.
2. Brain Records (`.planning/brain/records.jsonl`): normalized branch-indexed episodic summaries.
3. Canonical Fact Ledger (`.planning/brain/facts.json`): durable key/value memory with confidence, sources, history, and conflict tracking.
4. Brain Index (`.planning/brain/index.json`): branch pointers + dedupe hashes.
5. Compaction Summaries (`.planning/brain/summaries.jsonl`): archival rollups for old records.
6. Runtime Gates:
   - auto-ingest dedupe hash ring
   - sensitive-token redaction
   - conflict-aware fact updates

## Commands
- `python -m jarvis_engine.main brain-status`
- `python -m jarvis_engine.main brain-context --query "..."`
- `python -m jarvis_engine.main brain-regression`
- `python -m jarvis_engine.main brain-compact --keep-recent 1800`
- `python -m jarvis_engine.main intelligence-dashboard`

## Regression Health Model
`brain-regression` computes:
- `duplicate_ratio`
- `unresolved_conflicts`
- `branch_entropy`
- pass/warn/fail status

Target operating envelope:
- duplicate ratio < 0.75
- unresolved conflicts < 20
- non-zero branch entropy (memory not collapsing to one domain)

## External Review Inputs
- `.planning/reviews/claude_memory_arch_review.md`
- `.planning/reviews/gemini_memory_arch_review.md`
- `.planning/reviews/kimi_memory_arch_review.md`

Applied recommendations:
- hierarchical memory layers
- immutable logging + checkpoint style compaction
- contradiction-aware updates
- deterministic regression metrics

## Web Research References
- LangChain: long-term/short-term memory patterns
  - https://docs.langchain.com/oss/python/concepts/memory
- Generative Agents (memory retrieval + reflection loops)
  - https://arxiv.org/abs/2304.03442
- Reflexion (verbal reinforcement / iterative self-improvement)
  - https://arxiv.org/abs/2303.11366
- MemGPT (virtual context management for long-horizon tasks)
  - https://research.memgpt.ai/

## Next Up (V3)
1. Add source-verification classes (verified/provisional/rejected) per fact.
2. Add retrieval-time contradiction blocker (do not inject unresolved conflicting facts).
3. Add daily autonomous memory maintenance cycle (compact + regression check + alert).
4. Add signed snapshots for tamper-evident restore points.
