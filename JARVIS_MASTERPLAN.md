# JARVIS Master Plan (Secure Local-First Agent)

Date: 2026-02-19
Owner: Conner

## 1) MCP Status Check (Current State)

### Verified in this environment
- Codex session-level MCP resources: none currently registered (`list_mcp_resources` and `list_mcp_resource_templates` returned empty).
- Codex app connectors are available (Deep Research, GitHub, Shopping connectors are discoverable).
- Local Codex config exists at `C:\Users\Conner\.codex\config.toml` with app/tooling features enabled.
- VS Code MCP config exists at `C:\Users\Conner\AppData\Roaming\Code\User\mcp.json` and currently defines `io.github.upstash/context7` via `npx` (requires `CONTEXT7_API_KEY`).

### Meaning
- MCP capability is present in your toolchain.
- External MCP servers are not actively wired into this Codex session right now.
- You have at least one MCP server config in VS Code, but it is not enough for a production-grade Jarvis stack.
- Gemini CLI MCP check: one server (`nanobanana`) is configured and connected.
- Kimi CLI MCP check: no MCP servers are configured (`C:\Users\Conner\.kimi\mcp.json`).

## 2) Core Requirements (Jarvis-level)

1. Extremely high intelligence for daily collaboration.
2. Learns and improves over time from your interactions.
3. Runs on a home laptop (at least a strong local mode).
4. Strong security controls so it cannot exfiltrate/leak on its own.
5. Tool ecosystem via MCP with strict governance.

## 3) Model Research Summary (What matters)

### Frontier models (cloud/API)
- OpenAI GPT-5.2 family: latest flagship in GPT-5 line with reasoning controls, tool support, and migration guidance for agentic tasks.
- Anthropic Claude Sonnet 4.6 / Opus 4.6: very strong long-context and agentic performance claims, with Sonnet 4.6 offering 1M-context beta and Opus 4.6 benchmark claims on terminal/computer-use.
- Google Gemini line: active Gemini 3 + Gemini 2.5 ecosystem with strong multimodal and long-context offerings.

### Open/local models (self-hostable)
- Qwen3 family: supports thinking/non-thinking modes, broad multilingual coverage, strong open model ecosystem, practical sizes for local inference.
- Gemma 3 family: explicitly optimized for single-accelerator/smaller deployments with multiple sizes.
- Mistral open stack (including new generations and edge variants): viable for local/on-prem and enterprise controls.
- DeepSeek API + open-weight ecosystem: strong price/perf and active reasoning + agent updates.

### Learning methods (to avoid hype traps)
- Retrieval + long-term memory is mandatory for daily learning without catastrophic forgetting.
- Safe continuous improvement should be done with offline adapter training (LoRA/QLoRA), not live self-modifying weights.
- Preference optimization and evaluator loops improve behavior; ad-hoc autonomous self-retraining is risky and unstable.

## 4) Final Stack Recommendation

### 4.1 Best-of-best control plane (primary intelligence)
- Primary brain: `GPT-5.2` for orchestration/reasoning/tool routing.
- Secondary verifier brain: `Claude Sonnet 4.6` (or `Opus 4.6` for hardest tasks) for cross-checking and disagreement arbitration.

Why this pairing:
- High capability ceiling.
- Strong agent/tool pathways.
- Better reliability when a second model audits high-impact actions.

### 4.2 Local autonomy plane (runs on laptop)
- Detected hardware profile:
  - GPU: `NVIDIA GeForce RTX 4060 Ti (8GB VRAM)`
  - RAM: `32GB`
  - CPU: `AMD Ryzen 7 5700 (8C/16T)`
- Primary local model for this machine: `Qwen3 8B` quantized (`Q4_K_M` or `Q5_K_M`).
- Secondary local model option: `Gemma 3 12B` quantized when quality is preferred over latency.
- Runtime: Ollama first for operational simplicity; vLLM optional later when scaling concurrency.
- Cloud burst path for hard tasks: route selected requests to `GPT-5.2` or `Sonnet 4.6` with strict data filters.

### 4.3 Memory/learning plane
- Vector memory: long-term semantic retrieval (project docs, prior decisions, personal preferences).
- Episodic memory: per-session logs with summaries and outcomes.
- Procedural memory: reusable plans/scripts/tool recipes.
- Daily learning loop:
  1. Capture interactions.
  2. Distill stable preferences and patterns.
  3. Update memory indexes nightly.
  4. Weekly adapter retrain candidate (LoRA/QLoRA) in isolated pipeline.
  5. Promote only after eval pass.

## 5) Security Architecture (Non-negotiable)

### Zero-trust controls
1. Default-deny network egress for local agents.
2. MCP tool allowlist only (no unrestricted shell/browser in production mode).
3. Per-tool scoped credentials (short-lived tokens).
4. Action firewall for sensitive operations (payments, email sends, file deletes, remote exec).
5. Human approval gates for high-impact actions.

### Prompt-injection containment
1. Treat all external content as untrusted.
2. Separate retrieval text from executable tool instructions.
3. Enforce structured action plans validated by policy engine before execution.
4. Add classifier/filters for malicious instruction patterns.
5. Keep model from directly executing raw instructions from web/email/docs.

### Runtime isolation
1. Run risky tools inside sandboxed workers (container sandbox minimum; microVM for critical workflows).
2. Use read-only mounts by default.
3. Use per-task temp workspaces and automatic teardown.
4. Immutable audit logs and signed action traces.

## 6) MCP Blueprint for Jarvis

### MCP server groups
1. Core local: filesystem (scoped), calendar/tasks, notes, local DB.
2. Research: search, docs, citation retrieval.
3. DevOps: git, CI read-only first.
4. Personal ops: email/messaging (send behind approval gate).

### Governance
1. Every MCP server gets: owner, purpose, risk level, allowed actions, logging policy.
2. No server added without threat review and test harness.
3. Capability tiers: read-only -> constrained write -> privileged actions.

## 7) Build Roadmap (Execution)

### Phase 0 (Week 1): Secure skeleton
1. Stand up local orchestrator with Qwen3 + Ollama.
2. Implement policy engine and approval UI/CLI gate.
3. Configure first MCP servers (read-only only).
4. Add full action/event audit trail.

### Phase 1 (Weeks 2-3): Intelligence layer
1. Add GPT-5.2 primary orchestration API path.
2. Add Sonnet 4.6 verifier path for high-risk actions.
3. Add disagreement resolution + confidence thresholding.
4. Ship first "daily assistant" workflows (planning, research, summarization).

### Phase 2 (Weeks 4-5): Learning layer
1. Memory schemas (episodic/semantic/procedural).
2. Nightly memory distillation jobs.
3. Preference learning from explicit user feedback.
4. Eval harness with regression tests for behavior drift.

### Phase 3 (Weeks 6-8): Hardening
1. Prompt-injection red-team suite.
2. Policy break-glass + kill switch.
3. Sandboxed execution for risky tools.
4. Performance tuning for continuous daily use.

## 8) Success Criteria

1. Quality: >= 90% task acceptance on your daily workflow benchmark.
2. Safety: zero unapproved high-risk actions.
3. Reliability: >= 99% uptime in scheduled active window.
4. Learning: measurable weekly gain on your custom eval set.
5. Transparency: every action has trace + rationale + tool provenance.

## 9) Immediate Next Actions

1. Inventory your hardware (CPU, GPU VRAM, RAM, storage).
2. Decide strictness profile: `Paranoid`, `Balanced`, or `Builder`.
3. Select initial MCP servers for Phase 0 (max 5).
4. Stand up baseline local stack and run first eval suite.

## 10) External Cross-Validation (Gemini + Kimi via Bash)

### What we ran
1. `gemini -p ...` independent recommendation prompt.
2. `kimi --quiet --prompt ...` independent recommendation prompt.
3. Date+source follow-up prompts for both.

### Result quality
1. Both models agreed on a hybrid architecture (local model + optional cloud fallback).
2. Both emphasized memory/RAG-first learning before aggressive weight updates.
3. Gemini output referenced older model lineup in one run (useful directionally, not final authority).
4. Kimi output was closer to recent open-model landscape, but still included some uncertain release-date claims.

### Decision rule
1. Keep only claims confirmed by official sources.
2. Treat Gemini/Kimi outputs as advisory signals, not canonical model-release truth.
3. Use local benchmark harness + your workload evals as final selector.

---
This plan intentionally avoids unsafe "fully autonomous self-modification." Instead, it uses controlled learning loops with auditability, policy enforcement, and promotion gates.
