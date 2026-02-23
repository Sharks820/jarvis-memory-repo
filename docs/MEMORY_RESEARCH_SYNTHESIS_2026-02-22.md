# Memory Research Synthesis (2026-02-22)

## Inputs
- Claude review: `.planning/reviews/claude_memory_arch_review.md`
- Gemini review: `.planning/reviews/gemini_memory_arch_review.md`
- Kimi review: `.planning/reviews/kimi_memory_arch_review.md`
- Web references:
  - https://docs.langchain.com/oss/python/concepts/memory
  - https://arxiv.org/abs/2304.03442
  - https://arxiv.org/abs/2303.11366
  - https://research.memgpt.ai/

## Convergent Best Practices
1. Separate memory layers by role (working, episodic, semantic, procedural).
2. Keep write path deterministic and auditable (append-only plus hash identity).
3. Prevent drift with contradiction detection at write time.
4. Keep retrieval compact with diversity limits and recency/relevance scoring.
5. Track confidence and provenance per canonical fact.
6. Compact old memory without deleting signal.
7. Continuously score memory health with hard thresholds.

## Implemented in Jarvis
- Branch-indexed long-term memory records.
- Canonical fact ledger with confidence and conflict history.
- Automatic dedupe + redaction for auto-ingested memories.
- Compaction pipeline (`brain-compact`) preserving summarized archive.
- Regression health report (`brain-regression`) with pass/warn/fail gates.
- Context packet now includes canonical facts + relevance-ranked episodic snippets.

## Remaining Gaps
- No cryptographic signed snapshots yet.
- No explicit verified/provisional/rejected fact classes yet.
- No retrieval-time hard blocker for unresolved conflicts yet.
- No scheduled autonomous maintenance loop yet (compact + regression + alert).

## Priority Next Steps
1. Implement signed memory checkpoints (`.planning/brain/snapshots/*.tar.zst.sig`).
2. Add fact verification classes and a source quality field.
3. Block unresolved conflicting facts from prompt injection unless user overrides.
4. Add a nightly maintenance cycle that runs compaction/regression and logs alerts.
5. Expand golden memory regression tests with adversarial contradiction suites.
