# Session Memory Snapshot
Date: 2026-02-19

## What You Asked For
- Build a Jarvis-level assistant that is highly intelligent, learns continuously, and is secure.
- Verify MCP setup.
- Research best possible model stack deeply.
- Cross-check with Gemini and Kimi via Bash.
- Also query Claude Code for additional strategy.
- Prefer no recurring API dependency; local-first if possible.
- Keep major usability on mobile (Samsung S25), with laptop as main system if needed.

## What We Verified
- Codex session MCP resources/templates were empty in this session.
- VS Code MCP config exists (`context7` server definition).
- Gemini CLI available; Gemini MCP had `nanobanana` connected.
- Kimi CLI available; Kimi MCP had no servers configured.
- Claude CLI available and supports non-interactive `--print`.

## Research Conclusions
1. Best architecture is hybrid-capable but local-first.
2. For no recurring cost, use local model + memory + periodic local fine-tuning.
3. "Learning" should be memory distillation + controlled adapter training (QLoRA), not live self-modifying weights.
4. Teacher models (ChatGPT/Claude/Gemini/Kimi/Codex) can improve local bot via captured examples and distillation, but not direct weight transfer.
5. Need to follow provider terms for export/usage of outputs.

## Candidate Local Stack (No Mandatory API)
- Runtime: Ollama
- Primary local model: Qwen3 8B quantized
- Secondary local model: Gemma 3 class model (hardware permitting)
- Memory: LanceDB or FAISS + structured SQLite profile store
- STT/TTS: whisper.cpp + Piper
- Security: default-deny egress, tool allowlists, audit logs, sandboxed tool execution

## Cross-Model Roundtable Notes
- Gemini and Kimi both recommended memory-first plus optional training loops.
- Kimi emphasized weekly QLoRA pipeline and replay buffers.
- Gemini output mixed newer and older model references; treated as directional.
- One additional model response recommended policy-compliant export-based distillation (good fit for non-API constraints).

## Open Questions To Resolve Next
1. Final hardware profile of the target laptop (new device specs pending).
2. Exact mobile architecture for S25 (remote access vs partial on-device inference).
3. Legal-safe data ingestion workflow from subscription tools (ChatGPT Pro / Claude Max / CLI logs).
4. Strict security profile: paranoid vs balanced.

## Latest User Intent
"Save this memory to a new repo so we can resume later and continue planning."
