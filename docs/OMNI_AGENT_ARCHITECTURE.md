# Omni Agent Architecture

## Goal
Deliver a high-capability Jarvis agent you can use from phone or computer, while preventing uncontrolled execution drift.

## Core Principle
"Do anything I ask" is implemented as:
1. Broad tool capability coverage.
2. Strong intent-scope enforcement.
3. Tiered authorization for risky operations.

## Planes

### 1) Access Plane
- Unified API + session layer.
- Clients: desktop terminal/web and mobile web/app.
- Strong auth: device-bound session + short-lived tokens.

### 2) Intelligence Plane
- Local primary model for most tasks.
- Cloud burst for hard/high-risk tasks (optional).
- Secondary verifier model for disagreement checking.

### 3) Memory/Learning Plane
- Ingestion sources: user prompts, task outcomes, Claude/Opus/Gemini outputs.
- Memory classes:
  - Episodic (what happened)
  - Semantic (facts and references)
  - Procedural (how to do repeated tasks)
- Distillation pipeline converts raw interactions into stable, retrievable memory.

### 4) Execution Plane
- Tool adapters grouped by capability area (dev, docs, web, personal ops).
- Execution workers run with scoped credentials and sandbox constraints.
- Every tool action includes task id, rationale, and trace id.

### 5) Security Plane
- Default deny.
- Capability tiers:
  - Tier 0: read-only
  - Tier 1: bounded writes in approved paths
  - Tier 2: privileged actions requiring explicit approval
- Prompt-injection handling: external content is never trusted as executable intent.

## No-Regression Strategy
1. Golden task suite for core workflows.
2. Behavioral baseline scores tracked per release.
3. Any memory/policy/routing change must pass eval gate.
4. Rollback path on regression detection.

## Mobile + Desktop Access Strategy
1. Host the control API on desktop runtime.
2. Access via secure tunnel/VPN and authenticated gateway.
3. Keep privileged actions approval-gated, especially from mobile sessions.

