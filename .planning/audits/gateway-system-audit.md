# Gateway (LLM Routing) System — Deep Audit Report

**Date:** 2026-03-07  
**Scope:** `engine/src/jarvis_engine/gateway/` + `router.py`, `_constants.py`, `voice_pipeline.py`  
**Auditor:** Automated deep scan

---

## 1. Architecture Diagram

```
                         ┌──────────────────────────────┐
                         │      Voice Pipeline          │
                         │  (voice_pipeline.py)         │
                         │  - ConversationState         │
                         │  - _classify_and_route()     │
                         │  - Web-augmented dispatch    │
                         └──────────┬───────────────────┘
                                    │ QueryCommand
                         ┌──────────▼───────────────────┐
                         │      QueryHandler            │
                         │  (handlers/task_handlers.py) │
                         │  - Model selection           │
                         │  - Message assembly          │
                         │  - Privacy routing flag      │
                         └──────────┬───────────────────┘
                                    │ gateway.complete()
                    ┌───────────────▼───────────────────────┐
                    │         IntentClassifier               │
                    │  (gateway/classifier.py)               │
                    │  - Embedding-based route classification │
                    │  - 6 routes: math_logic, complex,      │
                    │    routine, creative, web_research,     │
                    │    simple_private                       │
                    │  - Privacy keyword detection            │
                    │  - Feedback-weighted similarity         │
                    └───────────────┬───────────────────────┘
                                    │ (route, model, confidence)
                    ┌───────────────▼───────────────────────┐
                    │          ModelGateway                   │
                    │  (gateway/models.py)                   │
                    │  - Provider resolution                 │
                    │  - Temperature derivation              │
                    │  - Fallback chain orchestration        │
                    │  - Cost logging via CostTracker        │
                    │  - Audit logging via GatewayAudit      │
                    └──┬──────┬──────┬──────┬──────┬────────┘
                       │      │      │      │      │
              ┌────────▼─┐ ┌─▼────┐ │ ┌────▼───┐ ┌▼────────────┐
              │ Anthropic │ │ Groq │ │ │Mistral │ │   Z.ai      │
              │ SDK       │ │ API  │ │ │ API    │ │   API       │
              │(claude-*) │ │kimi- │ │ │devstr- │ │  glm-4.7*   │
              └───────────┘ │k2    │ │ │al-*    │ └─────────────┘
                            └──────┘ │ └────────┘
                                     │
              ┌──────────────────────▼──────────────────────┐
              │           CLI Providers                      │
              │  (gateway/cli_providers.py)                  │
              │  ┌─────────┐ ┌────────┐ ┌────────┐ ┌──────┐│
              │  │claude   │ │codex   │ │gemini  │ │kimi  ││
              │  │CLI      │ │CLI     │ │CLI     │ │CLI   ││
              │  │(Opus4.6)│ │(GPT5.3)│ │        │ │      ││
              │  └─────────┘ └────────┘ └────────┘ └──────┘│
              └────────────────────────────────────────────┘
                                     │
              ┌──────────────────────▼──────────────────────┐
              │              Ollama (Local)                   │
              │  Default: gemma3:4b                          │
              │  Privacy-safe fallback                       │
              └──────────────────────────────────────────────┘

  Supporting Modules:
  ┌────────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │  CostTracker       │  │  GatewayAudit    │  │  Pricing         │
  │  (costs.py)        │  │  (audit.py)      │  │  (pricing.py)    │
  │  SQLite batched    │  │  JSONL log       │  │  Static table    │
  │  write buffer      │  │  5MB rotation    │  │  $/Mtok rates    │
  └────────────────────┘  └──────────────────┘  └──────────────────┘

  Also:
  ┌──────────────────────────┐  ┌──────────────────────────┐
  │ ModelRouter (router.py)  │  │ ResponseFeedbackTracker  │
  │ risk/complexity routing  │  │ (learning/feedback.py)   │
  │ cloud_burst path         │  │ LEARN-02 quality penalty │
  └──────────────────────────┘  └──────────────────────────┘
```

---

## 2. Per-Component Quality Scores

| Component | File | Score | Notes |
|-----------|------|-------|-------|
| **ModelGateway** | `models.py` (37.5 KB) | **8.5/10** | Excellent fallback chain, clean provider dispatch. Lacks streaming, caching, parallel execution. |
| **IntentClassifier** | `classifier.py` (18.7 KB) | **8/10** | Embedding-based routing with cached centroids and feedback loop. Solid but static exemplars. |
| **CostTracker** | `costs.py` (11.9 KB) | **7.5/10** | Batched writes, thread-safe. No budget caps, no alerts, no anomaly detection. |
| **Pricing** | `pricing.py` (2.2 KB) | **7/10** | Correct startswith prefix matching. Static table — no auto-update, missing CLI provider pricing. |
| **GatewayAudit** | `audit.py` (7 KB) | **8/10** | JSONL with rotation, efficient tail reads, good summary. Could add structured analytics. |
| **CLI Providers** | `cli_providers.py` (26.9 KB) | **8.5/10** | Robust subprocess handling, prompt compaction, continuity checkpoints. Windows-aware. |
| **ModelRouter** | `router.py` (0.7 KB) | **5/10** | Minimal risk/complexity router, likely vestigial. Not integrated with IntentClassifier flow. |
| **Voice Pipeline** | `voice_pipeline.py` | **8/10** | Good conversation state, web augmentation, model continuity tracking. Token budgets are low. |
| **Constants** | `_constants.py` | **9/10** | Clean centralisation of privacy keywords, model defaults, shared utilities. |

**Overall Gateway Score: 7.8/10** — Solid foundation with well-designed fallback chains and privacy enforcement. Key gaps: no streaming, no response caching, no speculative execution, no budget enforcement, no provider health tracking.

---

## 3. Routing Intelligence Analysis

### 3.1 Classification Method
The `IntentClassifier` uses **embedding cosine similarity** against pre-computed route centroids (15 exemplars per route, 6 routes). This is a well-designed approach:

- **Strengths:**
  - Cached centroid embeddings (SHA-256 keyed `.npz` files) — avoids re-embedding on startup
  - Pre-computed query norm for efficient per-route comparison
  - Privacy keyword regex takes absolute precedence (correct security posture)
  - Feedback-weighted similarity via `ResponseFeedbackTracker` (LEARN-02) adjusts routing based on satisfaction rates
  - Available-model-aware fallback resolution

- **Weaknesses:**
  - **Static exemplars** — 15 hand-crafted examples per route. No dynamic exemplar expansion from real queries.
  - **No online learning** — the centroid cache doesn't evolve with usage patterns. High-frequency misroutes aren't auto-corrected.
  - **Single-vector centroid** — averaging 15 diverse exemplars into one centroid loses intra-route variance. A query might be "math_logic" but sit closer to "complex" centroid due to mean compression.
  - **Confidence threshold (0.35)** is a magic number with no adaptive tuning.
  - **No sub-task decomposition** — a query like "summarize this code and find the bug" should route differently for each sub-task.

### 3.2 Route-to-Model Mapping
| Route | Primary Model | Rationale |
|-------|--------------|-----------|
| `math_logic` | codex-cli (GPT-5.3) | Strong math reasoning |
| `complex` | claude-cli (Opus 4.6) | Best coding/architecture |
| `routine` | kimi-k2 (Groq API) | Fast, cheap for simple tasks |
| `creative` | gemini-cli | Good creative writing |
| `web_research` | gemini-cli | Built-in grounding/search |
| `simple_private` | local Ollama (gemma3:4b) | Never leaves device |

This mapping is **well-considered** for the available providers. The fallback chains per route are also sensible.

### 3.3 Feedback Loop (LEARN-02)
The `ResponseFeedbackTracker.get_route_quality()` penalizes routes with poor satisfaction:
- Minimum 5 samples before penalty activates
- Similarity scaled by `0.5 + 0.5 * satisfaction_rate`
- This is a **good primitive** but limited — it penalizes the route, not the model. A bad model assigned to a good route will drag down the route score rather than triggering model replacement.

### 3.4 What's Missing
| Gap | Impact | Priority |
|-----|--------|----------|
| No A/B testing between providers | Can't empirically compare quality | High |
| No per-model quality tracking (only per-route) | Wrong model in a route goes undetected | High |
| No query difficulty estimation | Simple math queries waste GPT-5.3 | Medium |
| No sub-task routing | Compound queries get single-model treatment | Medium |
| No exemplar evolution from production data | Centroids may drift from actual usage | Medium |
| No confidence calibration | 0.35 threshold may be too high/low | Low |

---

## 4. Performance Analysis

### 4.1 Connection Management
- **httpx.Client** with `timeout=60s` — shared instance for all cloud API calls (**good**: connection pooling)
- **Anthropic SDK** — separate client with `timeout=60s`
- **Ollama Client** — separate with `timeout=120s`
- **CLI Providers** — subprocess per call (no pooling possible)
- ✅ `close()` properly cleans up all resources with `_closed` flag

### 4.2 Streaming Support
❌ **No streaming at all.** Every provider call blocks until full response is received.
- `_call_anthropic()` uses `messages.create()` (sync, not `.stream()`)
- `_call_openai_compat()` uses `self._http.post()` (sync)
- `_call_ollama()` uses `self._ollama.chat()` (sync, not `stream=True`)
- CLI providers inherently block on subprocess completion

**Impact:** For a voice assistant, time-to-first-token (TTFT) is critical for perceived responsiveness. A 2000-token response takes ~3-8s to generate but could start speaking within ~200ms with streaming.

### 4.3 Parallel/Speculative Execution
❌ **No parallel provider queries.** Each request goes to exactly one provider, then falls through the chain sequentially on failure.

**Missed opportunity:** For latency-sensitive voice queries, fire requests to 2 providers simultaneously and return the first response. Cost overhead is minimal since the slower request can be cancelled.

### 4.4 Response Caching
❌ **No deterministic query caching.** Identical questions (e.g., "what time is it in Tokyo?") always trigger a full LLM call.

### 4.5 Token Counting
⚠️ **Token counts come from provider responses** (post-hoc). No pre-flight token estimation to prevent context window overflow or optimize prompt size.
- No tiktoken or equivalent tokenizer
- `_compact_messages_for_cli()` uses character-based heuristics, not token counts
- `_MAX_MESSAGE_CHARS = 2000` is conservative but not token-accurate

### 4.6 Latency Profile (Estimated)

| Provider | Typical Latency | TTFT (if streaming) | Notes |
|----------|----------------|---------------------|-------|
| Groq (kimi-k2) | 300-800ms | ~100ms | Fastest API |
| Anthropic (Haiku) | 500-1500ms | ~200ms | Fast, cheap |
| Anthropic (Opus) | 3-15s | ~500ms | Slow, highest quality |
| CLI (claude) | 5-30s | N/A | Subprocess overhead + LLM time |
| CLI (codex) | 5-20s | N/A | Subprocess overhead |
| CLI (gemini) | 3-15s | N/A | Subprocess overhead |
| Ollama (gemma3:4b) | 1-10s | ~200ms | Local, varies by hardware |

### 4.7 Specific Latency Recommendations

1. **Add streaming to Anthropic and Ollama calls** — reduces perceived latency by 3-10x for voice output
2. **Add streaming to Groq/OpenAI-compat calls** — `stream: true` in payload, iterate SSE chunks
3. **Implement speculative execution** for voice queries — fire Groq API + Ollama simultaneously, return fastest
4. **Add response cache** with content-hash keys for deterministic queries (factual lookups, translations)
5. **Pre-warm CLI provider subprocesses** — keep a warm process pool for claude-cli/codex-cli to eliminate subprocess startup latency (~500ms saved per call)

---

## 5. Cost Optimization Analysis

### 5.1 Current Cost Tracking
✅ `CostTracker` logs every completion with model, provider, token counts, and USD cost  
✅ Batched writes (10 entries or 30s flush interval) — efficient for SQLite  
✅ `summary()` and `local_vs_cloud_summary()` provide aggregated views  
✅ `pricing.py` uses correct prefix-length-sorted startswith matching  

### 5.2 What's Missing

| Gap | Impact | Priority |
|-----|--------|----------|
| **No budget enforcement** | Runaway costs if a bug loops LLM calls | **Critical** |
| **No daily/monthly cost cap** | No spending guardrails | **Critical** |
| **No cost alerts/thresholds** | Owner unaware of cost spikes | High |
| **No cost-aware routing** | Doesn't prefer cheaper models when quality is equivalent | High |
| **No token waste detection** | Sending 10K context tokens for a "hi" query | Medium |
| **No CLI provider cost tracking** | claude-cli costs are only captured if CLI returns `total_cost_usd` | Medium |
| **Pricing table is static** | Must manually update when providers change rates | Low |
| **No cost prediction** (pre-flight) | Can't estimate cost before committing to a provider | Medium |

### 5.3 Cost-Aware Routing Gaps
The classifier picks models by **task type only**, never by cost. Examples:
- A "routine" query like "format this as a table" routes to Groq kimi-k2 ($1/$3 per Mtok) — **already cheap, good choice**
- But if kimi-k2 is unavailable, fallback goes to gemini-cli → claude-cli → kimi-cli — **no cost ranking in fallback order**
- The `_best_cloud_model()` method picks Groq first (fastest), not cheapest
- No mechanism to route "complex but small" queries to cheaper models

### 5.4 Token Waste
- System prompts include persona instructions, facts, memories, cross-branch insights, web search results — all concatenated without token budget awareness
- `_build_smart_context()` assembles context without measuring total token cost
- For simple factual queries, the system prompt may be 10x larger than necessary

---

## 6. Fallback Chain Analysis

### 6.1 Current Chain
```
Primary provider (resolved by _resolve_provider)
  → Other cloud APIs (groq → mistral → zai, skip failed)
    → CLI providers (claude-cli → gemini-cli → codex-cli → kimi-cli, skip failed)
      → Anthropic Haiku (if not already tried)
        → Local Ollama (gemma3:4b)
          → GatewayResponse(provider="none") — graceful failure
```

When `privacy_routed=True`:
```
Primary (forced Ollama)
  → GatewayResponse(provider="none") — no cloud fallback allowed
```

### 6.2 Strengths
✅ Comprehensive chain — 4 cloud APIs + 4 CLI tools + local = 9 providers  
✅ Privacy-routed queries **never** leak to cloud — correct enforcement  
✅ Each failed attempt is logged to GatewayAudit before continuing  
✅ `skip_provider` / `skip_ollama` prevents double-retrying the same failed provider  
✅ Graceful empty response (provider="none") rather than exceptions  

### 6.3 Weaknesses

| Issue | Details | Fix |
|-------|---------|-----|
| **No exponential backoff** | Failed providers are retried immediately on next request | Add circuit breaker with cooldown |
| **No provider health tracking** | A provider that's been failing for hours is still tried first | Add health/availability scoring |
| **No context preservation on failover** | CLI prompt compaction may differ from API message format | Standardize context before dispatch |
| **Sequential failover** | Each fallback attempt adds full latency before trying next | Parallel fallover for first 2 alternatives |
| **No rate limit awareness** | RateLimitError triggers fallback but doesn't track reset time | Parse Retry-After header |
| **CLI providers have 240s timeout** | A hung CLI blocks the entire request pipeline for 4 minutes | Add aggressive timeout + process kill |

### 6.4 "All Providers Fail" Scenario
When everything fails, returns `GatewayResponse(text="", provider="none")`. The voice pipeline then:
1. Checks for web search results as emergency fallback
2. If no web results: prints `intent=llm_unavailable` and speaks a generic error

This is **reasonable** but could be improved with cached recent responses for common queries.

---

## 7. Context Management Analysis

### 7.1 Conversation History
✅ `ConversationState` — thread-safe with RLock, debounced disk persistence  
✅ Configurable: 12 max turns (env-overridable 4-40), 2000 chars per message  
✅ Cross-LLM continuity: `conversation_continuity_instruction()` injects a bridging note when models switch  
✅ Integration with `conversation_state.py` for rolling summaries, anchor entities, unresolved goals  

### 7.2 Context Window Utilization
⚠️ **No context window awareness.** The system doesn't know or check provider context limits:
- Anthropic Opus: 200K tokens
- Groq kimi-k2: varies
- Ollama gemma3:4b: 8K tokens
- CLI providers: varies

A 12-turn conversation with rich system prompt could easily exceed gemma3:4b's 8K context on a privacy-routed query.

### 7.3 Context Truncation
- `_compact_messages_for_cli()`: Character-based compaction with checkpoint summaries — **good design**
  - 35% budget for system messages, rest for conversation
  - Always keeps last 3 turns
  - Dropped messages compressed into a "checkpoint" summary
  - Notifies ConversationState manager for entity/goal preservation
- **But**: This only applies to CLI providers. API calls have no equivalent context truncation.

### 7.4 System Prompt Efficiency
The system prompt includes:
1. Persona instructions ("You are Jarvis...")
2. Current date/time
3. Memory matches
4. Knowledge graph facts
5. Cross-branch insights
6. User preferences
7. Web search results (optional)
8. Continuity instructions
9. Conversation state injection (rolling summary, entities, goals, decisions)
10. Behavioral instructions

This is **comprehensive but bloated**. For a simple "what's 2+2?" query, the system prompt may be 2000+ tokens while the actual query is 5 tokens.

---

## 8. Missing Features & Upgrade Opportunities

### 8.1 Critical Gaps (Must-Have)

| # | Feature | Description | Effort |
|---|---------|-------------|--------|
| 1 | **Budget Enforcement** | Daily/monthly cost cap with hard cutoff to local-only mode | 2-3 hours |
| 2 | **Streaming Responses** | Add `.stream()` for Anthropic, `stream: true` for Groq/OpenAI-compat, `stream=True` for Ollama | 4-6 hours |
| 3 | **Circuit Breaker** | Track provider failures, auto-skip providers in cooldown period | 2-3 hours |
| 4 | **Context Window Guards** | Per-model max token checks before dispatch, auto-truncate if over | 3-4 hours |

### 8.2 High-Impact Improvements

| # | Feature | Description | Effort |
|---|---------|-------------|--------|
| 5 | **Response Cache** | Content-hash → cached response for deterministic queries (TTL-based) | 3-4 hours |
| 6 | **Speculative Execution** | Fire 2 providers simultaneously for voice queries, return fastest | 4-6 hours |
| 7 | **Provider Health Scoring** | Track latency, error rate, quality per provider; weight routing decisions | 4-5 hours |
| 8 | **Anthropic Prompt Caching** | Use `cache_control` for system prompt blocks (up to 90% cost reduction) | 2-3 hours |
| 9 | **Cost-Aware Routing** | When quality is equivalent, prefer cheaper model; daily cost budget allocation | 3-4 hours |
| 10 | **Token Budget Planner** | Pre-flight token estimation; allocate budget: system(30%) + history(40%) + query(30%) | 4-5 hours |

### 8.3 Nice-to-Have Enhancements

| # | Feature | Description | Effort |
|---|---------|-------------|--------|
| 11 | **A/B Quality Testing** | Periodically send same query to 2 models, compare response quality | 3-4 hours |
| 12 | **Dynamic Exemplars** | Add high-confidence production queries to centroid pool | 3-4 hours |
| 13 | **Sub-task Decomposition** | Detect compound queries, route parts to different providers | 6-8 hours |
| 14 | **Provider-Specific Prompt Optimization** | Tailor prompts per model (e.g., strip persona for Codex, add chain-of-thought for math) | 3-4 hours |
| 15 | **Model-Specific Temperature Profiles** | Fine-tuned temperature/top_p per model and task type | 1-2 hours |
| 16 | **Async Gateway** | `asyncio`-based gateway for non-blocking concurrent requests | 6-8 hours |
| 17 | **Rate Limit Tracking** | Parse Retry-After headers, pre-skip rate-limited providers | 2-3 hours |
| 18 | **Cost Anomaly Detection** | Alert when per-query or daily cost exceeds 3σ of historical mean | 2-3 hours |

---

## 9. Specific Code Fixes Needed

### 9.1 Bug: `_derive_temperature` Does Not Account for All CLI Models
```python
# models.py line ~119
@staticmethod
def _derive_temperature(model, route_reason, temperature):
    if "codex" in model_lower or "math_logic" in reason_lower:
        return 0.2
    if "gemini" in model_lower or "creative" in reason_lower:
        return 0.85
    return 0.7
```
**Issue:** `claude-cli` and `kimi-cli` get default 0.7 regardless of route. Coding tasks via claude-cli should use ~0.3, not 0.7.

**Fix:** Add route-based temperature mapping:
```python
_ROUTE_TEMPERATURES = {
    "math_logic": 0.2,
    "complex": 0.3,
    "routine": 0.5,
    "creative": 0.85,
    "web_research": 0.5,
    "simple_private": 0.7,
}
```

### 9.2 Improvement: `_fallback_chain` Should Track Latency Per Attempt
Currently, each `_audit_failed_attempt` resets `t0`, but total fallback chain latency isn't reported as a single metric. Add cumulative fallback latency tracking.

### 9.3 Issue: `_MAX_TOKENS_BY_ROUTE` Values Are Very Low
```python
_MAX_TOKENS_BY_ROUTE = {
    "math_logic": 1024,
    "complex": 1024,
    "creative": 1024,
    "routine": 512,
    "simple_private": 1024,
    "web_research": 1024,
}
```
For complex coding tasks, 1024 tokens (~750 words) is often insufficient. Consider 2048 for `complex` and `math_logic`.

### 9.4 Issue: `ModelRouter` (router.py) Is Disconnected
The `ModelRouter` class has risk/complexity routing (`cloud_verifier`, `cloud_burst`, `local_primary`) that doesn't integrate with the `IntentClassifier` → `ModelGateway` pipeline. It appears vestigial from an earlier architecture. Either integrate it or deprecate it.

### 9.5 Improvement: `_call_openai_compat` Missing Retry on 429
```python
if resp.status_code != 200:
    error_text = resp.text[:200]
    raise RuntimeError(...)
```
HTTP 429 (rate limit) should check `Retry-After` header and either wait or fast-fail to fallback with reason "rate_limited".

### 9.6 Issue: No Token Counting for CLI Providers
CLI providers (`claude-cli`, `codex-cli`, `gemini-cli`, `kimi-cli`) return `input_tokens=0, output_tokens=0` by default. Only `claude-cli` extracts `total_cost_usd` from JSON output. Other CLIs waste cost tracking opportunity.

---

## 10. Roadmap to World-Class Multi-LLM Routing

### Phase 1: Foundation Hardening (1-2 days)
- [ ] **Budget enforcement** — daily + monthly caps, auto-degrade to local-only
- [ ] **Circuit breaker** — per-provider error tracking, 5-minute cooldown after 3 consecutive failures
- [ ] **Context window guards** — per-model max token validation before dispatch
- [ ] **Rate limit awareness** — parse 429 Retry-After, skip rate-limited providers
- [ ] **Fix temperature derivation** — route-based temperature map

### Phase 2: Performance Leap (2-3 days)
- [ ] **Streaming responses** — Anthropic `.stream()`, Groq/OpenAI-compat SSE, Ollama `stream=True`
- [ ] **Response cache** — SHA-256 content hash → cached response, 1-hour TTL for factual queries
- [ ] **Speculative execution** — for voice: fire fastest API + one backup, cancel loser
- [ ] **Anthropic prompt caching** — `cache_control: {"type": "ephemeral"}` on system prompt blocks
- [ ] **Token budget planner** — pre-flight estimation, auto-truncate context to fit model window

### Phase 3: Intelligence Upgrade (2-3 days)
- [ ] **Provider health scoring** — rolling window of latency/error rate/quality per provider
- [ ] **Cost-aware routing** — when 2+ routes have similar confidence, pick cheapest adequate model
- [ ] **Dynamic exemplar expansion** — add high-confidence classified queries to centroid training set
- [ ] **Per-model quality tracking** — extend feedback loop from route-level to model-level
- [ ] **A/B quality testing** — shadow-mode: 5% of queries sent to alternative provider for comparison

### Phase 4: Advanced Features (3-5 days)
- [ ] **Async gateway** — `asyncio`-based for concurrent non-blocking requests
- [ ] **Sub-task decomposition** — detect compound queries, route parts to optimal providers
- [ ] **Provider-specific prompt optimization** — tailored system prompts per model family
- [ ] **Cost anomaly detection** — statistical alerting when spending deviates from norm
- [ ] **Warm CLI process pool** — keep subprocess connections alive for instant CLI dispatch
- [ ] **Model-specific parameter profiles** — optimized temperature, top_p, top_k per model and route

### Success Metrics
| Metric | Current (Estimated) | Target |
|--------|-------------------|--------|
| Voice TTFT | 1-5s | <500ms (streaming) |
| Average latency (routine) | 500-1500ms | <400ms (cache + Groq) |
| Fallback chain time | 5-30s (sequential) | <3s (parallel + circuit breaker) |
| Cost per 1K queries | ~$2-5 (uncontrolled) | <$1.50 (cost-aware routing + caching) |
| Privacy leak risk | None (enforced) | None (maintained) |
| Provider uptime awareness | None | 99%+ via health scoring |
| Budget enforcement | None | Hard cap with graceful degradation |

---

## 11. Summary Assessment

The Jarvis gateway is a **well-architected multi-LLM routing system** with several standout strengths:
- Privacy-first design with enforced local-only routing
- Comprehensive 9-provider fallback chain
- Embedding-based intent classification with feedback-weighted quality penalty
- Clean separation between classification, routing, cost tracking, and audit
- Robust CLI provider support with Windows-aware subprocess handling

The primary gaps are in **real-time performance** (no streaming), **cost governance** (no budget caps), and **adaptive intelligence** (no health scoring, no A/B testing, no dynamic learning). These are all addressable through incremental upgrades without architectural changes — the existing abstractions are clean enough to extend.

**Risk Assessment:** Low architectural risk. The gateway's modular design means streaming, caching, and health scoring can each be added independently without disrupting existing functionality.
