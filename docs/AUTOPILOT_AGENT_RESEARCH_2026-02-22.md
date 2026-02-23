# Autopilot Agent Research (2026-02-22)

This brief captures high-value design patterns from leading agent systems and how Jarvis should implement stronger versions.

## Primary Sources Reviewed
- OpenClaw repository: https://github.com/openclaw/openclaw
- OpenClaw docs: https://docs.openclaw.ai/
- OpenClaw trust model: https://docs.openclaw.ai/security/trust-model
- OpenHands repository: https://github.com/All-Hands-AI/OpenHands
- Microsoft AutoGen docs: https://microsoft.github.io/autogen/stable/
- LangGraph docs: https://langchain-ai.github.io/langgraph/

## What These Systems Do Well
- OpenClaw:
  - Focuses on autonomous coding/browser tasks with RL-style post-training loops and tool-use trajectories.
  - Publishes a trust model emphasizing explicit permissions and user control.
- OpenHands:
  - Agent works directly in real repos and terminals with human-in-the-loop corrections.
  - Strong on practical software task completion flow.
- AutoGen:
  - Multi-agent orchestration patterns for decomposition, debate, and tool specialization.
  - Good fit for review pipelines and planner/executor splits.
- LangGraph:
  - Durable long-running workflows with persistent state and resumability.
  - Strong model for 24/7 agents that must survive restarts.

## Jarvis Design Upgrades (Applied/Planned)
- Applied:
  - Capability gates for privileged actions.
  - Auditable growth tracking (hash-chained eval history).
  - Secure mobile ingestion (HMAC + nonce + replay window).
  - Voice identity gating for executable voice commands.
  - Connector prompts with exactly two action paths: voice and tap.
  - Always-on daemon mode with startup task scripts.
- Planned Next:
  - Planner/Executor/Verifier triad:
    - Planner proposes steps.
    - Executor runs gated actions.
    - Verifier checks result quality and security policy compliance.
  - Memory distillation:
    - Promote only high-confidence, validated facts to long-term semantic memory.
  - Multi-agent specialist lanes:
    - LifeOps, Coding, PhoneOps, Security reviewers.
  - Durable queueing:
    - Event-sourced action queue with idempotency keys and retry policies.

## "Better Than Baseline" Targets
- Faster recovery:
  - Daemon restart recovers state and pending actions without duplicates.
- Safer autonomy:
  - No privileged execution without explicit approval + identity proof.
- Real learning evidence:
  - Improvement only counted when capability eval scores and audit traces improve.
- Lower user workload:
  - Auto-detection of missing connectors and setup prompts.
  - One-command daily autopilot (`ops-autopilot`) and always-on mode (`daemon-run`).
