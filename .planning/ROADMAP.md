# ROADMAP

## Phase 0: Secure Skeleton
- [ ] Create runnable engine scaffold.
- [ ] Add policy gate and baseline audit log.
- [ ] Add local config/state management.

## Phase 1: Intelligence Layer
- [ ] Add model router (local primary, cloud optional).
- [ ] Add tool invocation policy categories and risk levels.
- [ ] Add workflow for planning/research/summarization tasks.

## Phase 2: Memory Layer
- [ ] Add semantic retrieval interface.
- [ ] Add nightly memory distillation job scaffold.
- [ ] Add session summarization and procedural memory storage.
- [ ] Add cross-model ingestion (Claude/Opus/Gemini/user outcomes) with provenance tags.

## Phase 3: Hardening
- [ ] Add injection-resilience checks in tool pipeline.
- [ ] Add stronger action firewall and approval hooks.
- [ ] Add reliability checks and recovery runbook.
- [ ] Add no-regression eval gates for memory/policy/model routing changes.
- [ ] Add encrypted backup + restore workflow for full memory/state portability.

## Phase 4: Omni Access + Broad Capability
- [ ] Add authenticated API interface for phone + desktop access.
- [ ] Add capability tiers (read-only, bounded write, privileged).
- [ ] Add task-intent validator so execution cannot drift beyond user-requested scope.

## Phase 5: Jarvis-Like Multimodal Stack
- [ ] Add image generation/edit pipeline adapter.
- [ ] Add code-generation specialist routing and verification loop.
- [ ] Add video generation/remix adapter workflow.
- [ ] Add 3D generation/conversion pipeline with validation checks.
- [ ] Add cross-modal task planner (text -> toolchain graph -> outputs).

## Phase 6: Voice + Automation Core
- [ ] Add voice input/output orchestration layer (STT + TTS + tool calls).
- [ ] Add automation runtime for scheduled and event-triggered tasks.
- [ ] Add human approval hooks for privileged or irreversible actions.
- [ ] Add execution receipts: what was done, why, and with which tools.

## Phase 7: Capability Growth + Optimization
- [ ] Expand golden-task suites for reasoning, coding, and multimodal quality.
- [ ] Add tamper-evident eval chain and strict/no-thinking scoring modes.
- [ ] Add subscription/repair/energy optimization agents with ROI tracking.
- [ ] Add promotion policy: improve score + pass safety gates or no deploy.
