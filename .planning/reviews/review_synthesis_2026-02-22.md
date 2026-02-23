# External Review Synthesis (Gemini + Claude + Kimi)

## Sources
- `gemini_research_web_memory_voice_2026-02-22.md`
- `claude_analysis_websearch_voiceguard_2026-02-22.md`
- `kimi_analysis_websearch_voiceguard_2026-02-22.short.md` (tool-trace heavy; extracted consistent themes only)

## High-confidence findings
1. Sensitive-memory redaction bug in `_sanitize_memory_content` (regex escaping).
2. Wake-word keyword input needed strict sanitization before PowerShell interpolation.
3. `/command` returning HTTP 400 for routed failures degraded UX observability.
4. Daemon needed fault isolation and circuit-breaker semantics for true 24/7 operation.
5. Learning mission fetch path needed parallelization + cache for performance.
6. Mobile/Desktop reliability required explicit sync + self-heal backend primitives.

## Implemented in this pass
1. Fixed credential redaction regex in `main.py`.
2. Added strict hotword keyword validation in `desktop_widget.py`.
3. Changed mobile API `/command` to always return 200 with structured `ok/intent/reason` payload.
4. Added daemon resilience:
   - mission/sync/self-heal/autopilot exception isolation
   - consecutive-failure tracking
   - circuit breaker (`daemon_circuit_breaker_open=true`, return code `3`)
5. Added web research engine:
   - `web_research.py`
   - `main.py` command `web-research`
   - voice intent routing for web research
6. Added resilience backend:
   - `resilience.py`
   - commands: `mobile-desktop-sync`, `self-heal`
   - periodic daemon hooks for auto sync + auto self-heal
7. Optimized learning missions:
   - parallel fetch (thread pool)
   - bounded page cache (`_fetch_page_cached`)
8. Added/updated tests for all new paths and reliability behavior.

## Remaining hardening backlog (next)
1. Bind HMAC signature to path/method to prevent cross-endpoint replay of same signed body.
2. Add endpoint rate limiting on `/command` and `/ingest`.
3. Add DNS-rebind-resistant fetch mode for web research (single-resolve connect).
4. Add optional TLS termination path for non-loopback mobile bind.
