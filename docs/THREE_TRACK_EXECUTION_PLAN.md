# Three-Track Execution Plan

Scope: plan and execute all 3 next steps in parallel with strict safety + regression controls.

## Goal
Build a local-first Jarvis that can:
1. Execute real multimodal generation tasks (image/video/3D/code).
2. Manage day-to-day operations (calendar/email + actionable briefings).
3. Run phone-first voice command flows from Samsung Galaxy S25.

## Delivery Strategy
1. Work in 3 tracks with shared core services.
2. Ship thin vertical slices first, then harden.
3. Every promotion requires:
- passing tests
- no regression in `growth-eval`
- policy gate compliance

## Shared Core (applies to all tracks)
1. Standard adapter contract:
- `plan -> authorize -> execute -> verify -> log`
2. Unified audit record:
- input hash, output hash, provider, model/tool, policy decision, duration
3. Approval tiers:
- `read`, `bounded_write`, `privileged`
4. Rollback:
- config flag to disable any adapter quickly

## Track 1: Real Multimodal Adapter Execution
### Objective
Move `image/video/model3d` from planning-only into executable adapters.

### Milestones
1. M1 (Day 1-2): Adapter interface + provider registry.
2. M2 (Day 3-4): Image adapter live execution.
3. M3 (Day 5-6): Video adapter live execution (privileged).
4. M4 (Day 6-7): 3D pipeline adapter live execution (privileged).

### Implementation
1. Add `adapters/image_adapter.py`:
- local or API provider routing
- output artifact path + metadata JSON
2. Add `adapters/video_adapter.py`:
- generation + status polling + download
3. Add `adapters/model3d_adapter.py`:
- generation/conversion + validation
4. Extend `run-task`:
- execute all task types, not just code

### Acceptance Criteria
1. `run-task --type image --execute` writes output artifact + metadata.
2. `run-task --type video --execute --approve-privileged` completes or cleanly reports failure.
3. `run-task --type model3d --execute --approve-privileged` produces valid output + verification summary.
4. All adapter executions logged in `events.jsonl`.

## Track 2: Calendar + Email Operations
### Objective
Turn `ops-brief` from static snapshot mode into live connector mode.

### Milestones
1. M1 (Day 1-2): Connector abstraction + secure credential loading.
2. M2 (Day 3-4): Calendar connector (read + create event drafts).
3. M3 (Day 4-5): Email connector (priority inbox triage + draft replies).
4. M4 (Day 6-7): Bill/subscription monitor adapter integration.

### Implementation
1. Add `connectors/calendar_connector.py`:
- read agenda
- create/update draft events
2. Add `connectors/email_connector.py`:
- fetch unread + classify urgency
- generate draft reply suggestions
3. Add `ops-sync` CLI:
- refresh live snapshot into `.planning/ops_snapshot.live.json`
4. Keep all mutating actions behind approval gate.

### Acceptance Criteria
1. `ops-sync` pulls live calendar + email summaries.
2. `ops-brief` can run against live snapshot.
3. `automation-run` supports connector-backed actions in dry-run and execute modes.
4. Privileged operations denied without explicit approval flag.

## Track 3: Samsung Galaxy S25 Voice-First Flow
### Objective
Enable one-command voice workflow from S25 into Jarvis and back to spoken output.

### Milestones
1. M1 (Day 1-2): Stable mobile ingest + command schema.
2. M2 (Day 3-4): Android shortcut/Termux voice-to-command trigger.
3. M3 (Day 5-6): Desktop Jarvis response synthesis + return message.
4. M4 (Day 7): End-to-end routine: voice command -> task -> spoken brief.

### Implementation
1. Add `mobile/voice_command_schema.json`:
- intent, entity, approval hint, priority
2. Add `voice-run` CLI:
- parse command text
- map to `run-task`, `ops-brief`, `automation-run`
3. Add response channel:
- save response text + optional WAV for playback
4. Add S25 routine instructions:
- wake phrase -> Termux command -> API call -> notification + speech

### Acceptance Criteria
1. S25 sends signed command payload successfully.
2. Jarvis executes mapped action path with policy gating.
3. Response is available as text and optional audio.
4. Failures return actionable reason, not silent drops.

## 14-Day Sprint Breakdown
1. Days 1-3:
- adapter/connector abstractions
- secure credential + policy scaffolding
- voice command schema
2. Days 4-7:
- image + calendar + email first live slice
- S25 command round-trip baseline
3. Days 8-11:
- video + 3D execution
- automation workflow upgrades
4. Days 12-14:
- hardening, load tests, regression gates, release checklist

## Regression + Quality Gates
1. `pytest` must stay green.
2. `growth-eval` strict mode (`--accept-thinking` off) must not regress for promoted model profile.
3. Security checks:
- replay protection
- command allowlist enforcement
- privileged action approvals
4. Artifact verification:
- adapter output path exists
- metadata contains provenance and hashes

## Risk Register
1. Provider API churn:
- mitigation: adapter abstraction + feature flags
2. Voice reliability on mobile:
- mitigation: fallback text trigger path
3. Over-automation risk:
- mitigation: explicit approval for privileged and financial actions
4. Model hallucination in ops workflows:
- mitigation: connector-verified facts + action confirmation prompts

## Definition of Done (for all 3 tracks)
1. End-to-end command execution works from S25 and desktop.
2. All outputs are auditable and policy-gated.
3. Live connectors and multimodal adapters can be individually disabled.
4. No critical security blockers from review pass.
