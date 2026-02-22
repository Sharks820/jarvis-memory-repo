# Jarvis Ops Pack

This module set turns Jarvis from planning-only into an executable daily operations core.

## Included Capabilities
1. Multimodal task routing (`run-task`):
- `code`: local code generation via Ollama.
- `image`, `video`, `model3d`: routed with policy gates and execution planning.

2. Daily operations intelligence:
- `ops-brief`: summarizes tasks/calendar/emails/bills/subscriptions.
- `ops-export-actions`: generates executable action queue with risk classes.

3. Automation executor:
- `automation-run`: evaluates every action through capability gate + policy allowlist.
- Dry-run by default, explicit flags required for privileged execution.

4. Voice interface (local):
- `voice-list`
- `voice-say --profile jarvis_like`

## Safety Model
1. Capability gate blocks privileged actions without explicit approval.
2. Policy allowlist blocks disallowed shell command execution.
3. Action and task decisions are logged to `.planning/events.jsonl`.

## Reality Check
1. "Do everything" requires integration adapters per system (calendar provider, email API, utility account API, smart-home stack).
2. This pack provides the orchestration spine and safety rails; adapters are layered on top.
