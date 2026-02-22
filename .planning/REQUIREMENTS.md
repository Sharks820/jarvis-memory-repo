# REQUIREMENTS

## Functional
1. Run a local orchestration loop with tool routing.
2. Persist episodic memory and retrieve relevant context.
3. Enforce policy checks before sensitive actions.
4. Provide structured audit logs of decisions and actions.
5. Support optional cloud burst routing for difficult tasks.
6. Ingest and retain teachings from Claude, Opus, Gemini, and user task outcomes with source provenance.
7. Expose a unified interface reachable from phone and computer.
8. Execute broad task categories through tool adapters (dev, research, personal ops) under explicit capability policies.
9. Maintain encrypted, restorable memory/state backups with documented recovery steps.
10. Support multimodal generation and editing workflows: image, code, video, and 3D asset outputs via pluggable providers.
11. Support text-first and voice-enabled interaction modes from phone and desktop.
12. Support end-to-end automation flows (plan -> execute -> verify -> report) with explicit approval tiers.
13. Track measurable capability growth over time with auditable eval history and anti-regression gates.
14. Support cost-reduction operations: subscription audit suggestions, repair triage playbooks, and energy-efficiency optimization workflows.

## Non-Functional
1. Local-first operation with explicit data boundaries.
2. Default-deny posture for high-risk tool actions.
3. Transparent, reproducible behavior with state files.
4. Simple bootstrap path for ongoing Codex-led development.
5. No regression in core behavior after memory or policy updates (eval-gated promotions).
6. All actions attributable to task intent, scope, and approval state.
7. Provider independence: memory and policies portable across model providers.
8. Objective performance telemetry for reasoning quality, latency, and throughput.
9. Tamper-evident evaluation history and reproducible scoring traces.

## Out Of Scope (Initial Build)
1. Fully autonomous retraining in production runtime.
2. Unrestricted shell/browser actions without review controls.
3. Complex UI before core engine loop is stable.
4. Physical robotics/actuation beyond software-controllable device integrations.
