# Resilience And Continuous Learning

## Non-Negotiable Boundaries
1. No covert account evasion or policy circumvention.
2. Durability is achieved through portable architecture, backups, and provider failover.

## "Gets Stronger Over Time" Design
Strength is produced by a controlled loop, not uncontrolled self-editing:

1. Capture
- Ingest every meaningful interaction and task outcome.
- Preserve source provenance (`user`, `claude`, `opus`, `gemini`, `task_outcome`).

2. Distill
- Convert raw logs to stable memory candidates:
  - Facts (semantic)
  - Playbooks (procedural)
  - Outcomes (episodic)

3. Validate
- Run memory-quality checks and contradiction detection.
- Reject low-confidence or conflicting candidates.

4. Promote
- Promote only if evaluation scores improve or remain stable.
- Keep rollback snapshots for every promotion.

5. Re-evaluate
- Re-run golden tasks after every policy/memory update.
- Block promotion on any regression.

## No-Regression Controls
1. Golden benchmark set (your core workflows).
2. Gate thresholds:
- Accuracy must not decrease.
- Safety violations must remain zero.
- Latency budget must remain within defined bounds.
3. Automatic rollback if gate fails.

## "Never Lose Him" Durability Strategy
1. Local-first canonical state
- `.planning/`, memory stores, policy definitions, and prompts are canonical local assets.

2. Encrypted backups
- Hourly local snapshots.
- Daily off-device encrypted backup.
- Weekly immutable archive snapshot.

3. Multi-provider failover (legit)
- Keep provider adapters swappable.
- If one model/provider is unavailable, route to secondary provider.
- Keep memory independent of any single provider account.

4. Identity continuity
- Stable Jarvis identity = local policy + memory + prompts + eval suite.
- Rehydrate on new hardware by restoring backup and re-running bootstrap checks.

## Security Against Drift
1. Intent lock: every action tied to an explicit task scope.
2. Capability tiers enforce boundaries for risky actions.
3. External content is never treated as executable intent.
4. Privileged operations require explicit user approval.

