# Jarvis Unlimited Acceleration Roadmap

## Core Goal
Maximize local learning speed while keeping security and memory integrity stronger than default consumer agents.

## Acceleration Stack
1. Multi-lane ingestion: user/task/mission/automation feeds enter through separate queues.
2. Immediate distillation: new events become compact episodic summaries + canonical facts.
3. Conflict gate: contradictory facts are quarantined until resolved.
4. Retrieval budget controller: cap injected context by relevance, branch diversity, and confidence.
5. Nightly maintenance: compact, regression scan, signed snapshot, anomaly alerts.

## Throughput Upgrades (Next)
1. Add ingestion priority lanes (urgent ops > user requests > passive learning).
2. Add micro-batch summarization every N events to reduce token overhead.
3. Add source-quality score per memory write (official docs > code > forums > unknown).
4. Add verification quorum for mission findings (>=2 independent trusted sources before canonical promote).
5. Add write-ahead memory transaction log + replay.

## Anti-Regression Guardrails
1. Fail hard on unresolved conflict overload.
2. Reject low-confidence memory promotion to canonical facts.
3. Keep immutable signed snapshots for rollback.
4. Run nightly regression report and track trendline.
5. Block automation execution when memory regression status is `fail`.

## Security Hardening Track
1. Move snapshot signing keys to Windows Credential Manager.
2. Add command allowlist manifest for executable actions.
3. Add trusted-origin signatures for UI/API write operations.
4. Keep owner-guard and trusted-device checks mandatory for state mutation.
5. Add local sandbox policy for automation subprocesses.

## UX Performance Track
1. Desktop widget with compositor-friendly pulse animation only.
2. Enter-to-send + auto-send-after-dictation for near zero-click flow.
3. Optional wake-word mode with low-duty-cycle detection.
4. Online/offline visual state in widget.
5. Adaptive animation throttling when minimized or on battery saver.
