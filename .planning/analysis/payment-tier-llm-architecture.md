# Payment Tier & LLM Access Architecture — Jarvis

> Research date: March 2026 | Sources: 30+ web searches, current Jarvis gateway code

---

## 1. Current API Pricing Table (March 2026)

### Tier 1 — Frontier / Highest Intelligence

| Provider | Model | Input $/1M tok | Output $/1M tok | Context | Notes |
|----------|-------|:-:|:-:|:-:|-------|
| Anthropic | Claude Opus 4.6 | $5.00 | $25.00 | 200K | Best coding, multi-agent coordination |
| OpenAI | GPT-5.4 | $2.50 | $15.00 | 1M | Broad enterprise, computer use |
| OpenAI | GPT-5.2 Pro | $21.00 | $168.00 | 1M | Extreme reasoning (niche) |
| Google | Gemini 3.1 Pro | $2.00 | $12.00 | 1M | Multimodal, long context |

### Tier 2 — High Quality / Best Value

| Provider | Model | Input $/1M tok | Output $/1M tok | Context | Notes |
|----------|-------|:-:|:-:|:-:|-------|
| Anthropic | Claude Sonnet 4.6 | $3.00 | $15.00 | 200K | Strong coding, fast |
| OpenAI | GPT-5.3 Codex | $2.00–3.00 | $8.00–12.00 | 128K | Code-focused |
| OpenAI | GPT-4o | $2.50 | $10.00 | 128K | Versatile |
| Mistral | Devstral-2 | $0.40 | $2.00 | 128K | European, multilingual |
| Groq | Kimi K2 | $1.00 | $3.00 | 128K | Ultra-fast inference |

### Tier 3 — Budget / High Throughput

| Provider | Model | Input $/1M tok | Output $/1M tok | Context | Notes |
|----------|-------|:-:|:-:|:-:|-------|
| Anthropic | Claude Haiku 4.5 | $0.80 | $4.00 | 200K | Fast, cheap |
| OpenAI | GPT-4o Mini | $0.15 | $0.60 | 128K | Best value mainstream |
| Google | Gemini 2.5 Flash | $0.15 | $0.60 | 1M | Very cheap, multimodal |
| Google | Gemini 2.5 Flash-Lite | $0.075 | $0.30 | 1M | Cheapest major provider |
| Mistral | Devstral-small-2 | $0.10 | $0.30 | 128K | Tiny, fast |
| DeepSeek | V3.2 | $0.28 | $0.42 | 64K | Best price/perf ratio |
| DeepSeek | R1 (Reasoner) | $0.55 | $1.10 | 64K | Cheap reasoning |

### Tier 4 — Free APIs (Rate-Limited)

| Provider | Model | Free Limits | Notes |
|----------|-------|-------------|-------|
| Google | Gemini 2.5 Pro | 5 RPM, 100 RPD | No credit card, commercial OK |
| Google | Gemini 2.5 Flash | 10 RPM, 250 RPD | Best free option for throughput |
| Google | Gemini 2.5 Flash-Lite | 15 RPM, 1000 RPD | Highest free volume |
| Groq | Developer Plan | Pay-as-you-go, free trial | Free API key, limited credits |
| DeepSeek | Free Tier | 1–5M tokens/month | Generous for personal use |
| OpenRouter | Free Plan | 50 req/day, 25+ models | Good for testing |

### Tier 5 — Local / Free (Ollama)

| Model | Params | VRAM Needed | Quality | Speed |
|-------|--------|-------------|---------|-------|
| Qwen3 8B | 8B | 6–8 GB | Good general | 40+ tok/s |
| Llama 3.3 8B | 8B | 6–8 GB | Strong general | 40+ tok/s |
| Gemma 3 12B | 12B | 10–12 GB | Very good | 25–35 tok/s |
| Qwen3 14B | 14B | 10–12 GB | Very good | 20–30 tok/s |
| Gemma 3 27B | 27B | 16–24 GB | Excellent | 10–20 tok/s |
| Qwen3 32B | 32B | 16–24 GB | Near-cloud | 8–15 tok/s |
| Llama 3.3 70B | 70B | 48+ GB | Frontier-local | 5–10 tok/s |

### Prompt Caching Pricing (Anthropic Claude)

| Operation | Cost vs Normal | Example (Opus 4.6) |
|-----------|---------------|---------------------|
| Cache Write | 125% of input | ~$6.25/M tok |
| Cache Read (hit) | 10% of input | ~$0.50/M tok |
| Net savings on repeated context | **Up to 90%** | |

### Aggregator: OpenRouter

| Plan | Fee | Models | Limits |
|------|-----|--------|--------|
| Free | $0 | 25+ models, 4 providers | 50 req/day |
| Pay-as-you-go | 5.5% platform fee | 300+ models, 60 providers | No markup on token prices |
| Enterprise | Volume discount | Full catalog | SLAs, dedicated support |

---

## 2. Architecture A — Free Tier (Local Only)

### Goal
Maximum intelligence at zero cost. Full privacy guarantee.

### Hardware Tiers

**Entry (8 GB VRAM — e.g., RTX 3060, M1 8GB):**
- Primary: Qwen3 8B or Llama 3.3 8B (Q4_K_M quantization)
- Capability: Summarization, Q&A, basic coding, routine tasks
- Speed: ~40 tok/s, responsive for conversation

**Mid (12–16 GB VRAM — e.g., RTX 4070, M2 Pro 16GB):**
- Primary: Gemma 3 12B or Qwen3 14B
- Capability: Good coding, multi-step reasoning, creative writing
- Speed: ~25 tok/s, good UX

**High (24 GB VRAM — e.g., RTX 4090, M3 Max 36GB):**
- Primary: Gemma 3 27B or Qwen3 32B
- Capability: Near-cloud quality on most tasks
- Speed: ~12 tok/s, usable for complex tasks

**Ultra (48+ GB VRAM — dual GPU or M4 Ultra):**
- Primary: Llama 3.3 70B
- Capability: Frontier-class local intelligence
- Speed: ~8 tok/s

### Free Cloud Supplements

**Google Gemini Free Tier:**
- Flash-Lite: 15 RPM / 1000 RPD — use for overflow & multimodal
- Flash: 10 RPM / 250 RPD — use for moderate complexity
- Pro: 5 RPM / 100 RPD — reserve for truly complex queries
- Strategy: Route 90% local, escalate to Gemini free tier for tasks beyond local model capability
- Risk: Google reduced quotas by 50–80% in Dec 2025; can change without notice

**Groq Free Trial:**
- Free API key with limited credits on sign-up
- Ultra-fast inference (1000+ tok/s) — good for latency-sensitive tasks
- Not reliable as long-term free source; credits deplete

**DeepSeek Free Tier:**
- 1–5M free tokens/month
- V3.2 quality is excellent for the price ($0.28/M even when paid)
- Good supplemental source for reasoning tasks

**OpenRouter Free Plan:**
- 50 req/day across 25+ models
- Good for testing/fallback, not primary

### Free Tier Architecture

```
User Query
    │
    ├─── Privacy keywords detected? ──→ Local Ollama (always)
    │
    ├─── Simple/routine task? ──→ Local Ollama
    │
    ├─── Moderate complexity? ──→ Local Ollama (try first)
    │         │
    │         └── Quality insufficient? ──→ Gemini Flash free tier
    │
    └─── High complexity? ──→ Gemini Pro free tier (5 RPM limit)
              │
              └── Rate limited? ──→ DeepSeek free tier
                       │
                       └── Also limited? ──→ Local Ollama (best effort)
```

---

## 3. Architecture B — Premium Tier (Paid Cloud)

### Option 1: Pay-Per-Use (Recommended Default)

User loads credits into Jarvis account. Each query deducts actual API cost + small operational margin.

**Pricing Pass-Through:**
- User pays exact provider cost + 0% markup (Jarvis is a personal tool, not a business)
- Cost shown before and after each query
- Daily/weekly/monthly spending summaries
- Budget cap with automatic fallback to free tier when exceeded

**Cost Projections (pay-per-use):**

| Usage Level | Queries/Day | Avg Tokens/Query | Model Mix | Monthly Cost |
|-------------|:-----------:|:----------------:|-----------|:------------:|
| Light | 10 | 2K in / 1K out | 80% local, 20% Haiku | ~$1–2 |
| Moderate | 30 | 3K in / 2K out | 60% local, 30% Sonnet, 10% Opus | ~$8–15 |
| Heavy | 80 | 5K in / 3K out | 40% local, 40% Sonnet, 20% Opus | ~$30–60 |
| Power | 150+ | 8K in / 5K out | 30% local, 40% Sonnet, 30% Opus | ~$80–150 |

**Budget Alerts:**
- Warning at 50%, 75%, 90% of monthly budget
- Hard cap option: stops cloud calls at budget limit, falls back to local
- Soft cap option: warns but allows override for important queries

### Option 2: Monthly Subscription Tiers

For users who prefer predictable billing:

| Tier | Monthly | Included Credits | Overage Rate | Best For |
|------|:-------:|:----------------:|:------------:|----------|
| Starter | $5 | $5 API credit | Pay-per-use | Casual cloud use |
| Pro | $20 | $25 API credit | Pay-per-use | Daily driver |
| Max | $50 | $75 API credit | Pay-per-use | Heavy coding/research |

Note: Subscription adds a 20–50% bonus vs straight credit purchase, incentivizing commitment.

### Smart Routing for Cost Optimization

```
User Query
    │
    ├─── Privacy? ──→ Local (always free)
    │
    ├─── Complexity classifier score
    │         │
    │         ├── Low (0.0–0.3) ──→ Local Ollama ($0)
    │         ├── Medium (0.3–0.6) ──→ Haiku 4.5 ($0.80/$4) or Gemini Flash ($0.15/$0.60)
    │         ├── High (0.6–0.8) ──→ Sonnet 4.6 ($3/$15) or GPT-5.4 ($2.50/$15)
    │         └── Frontier (0.8–1.0) ──→ Opus 4.6 ($5/$25)
    │
    └─── User preference override
              ├── "always local" ──→ Local Ollama
              ├── "best quality" ──→ Opus/GPT-5.4
              └── "cheapest cloud" ──→ Haiku/Flash-Lite
```

### Prompt Caching Strategy

Jarvis should aggressively use prompt caching to reduce costs:

1. **System prompt caching**: Jarvis personality + context = large, stable prefix → cache write once, read on every query (90% savings)
2. **Conversation context caching**: Multi-turn conversations keep growing prefix → cache each turn
3. **Knowledge context caching**: When querying with large documents/knowledge base content
4. **Estimated savings**: 40–60% reduction on typical Jarvis workload (system prompt alone is ~2K tokens reused every query)

---

## 4. Architecture C — BYOK (Bring Your Own Key)

### Design Principles
- User provides their own API keys — zero Jarvis markup
- Keys stored securely (DPAPI on Windows, Keychain on macOS, encrypted at rest)
- User pays provider directly
- Jarvis provides cost tracking but no billing

### Supported Key Types

| Provider | Key Format | Storage | Validation |
|----------|-----------|---------|------------|
| Anthropic | `sk-ant-*` | DPAPI encrypted | Test HEAD /v1/models |
| OpenAI | `sk-*` | DPAPI encrypted | Test GET /v1/models |
| Google AI | API Key | DPAPI encrypted | Test generateContent |
| Groq | `gsk_*` | DPAPI encrypted | Test /openai/v1/models |
| Mistral | API Key | DPAPI encrypted | Test /v1/models |
| DeepSeek | API Key | DPAPI encrypted | Test /v1/models |
| OpenRouter | `sk-or-*` | DPAPI encrypted | Test /api/v1/models |

### CLI Subscription Routing (Already Partially Implemented!)

Jarvis already has `gateway/cli_providers.py` with support for routing through CLI subscriptions. This is a powerful BYOK variant:

| CLI Provider | Subscription | Models Available | How It Works |
|-------------|-------------|-----------------|-------------|
| Claude Code | Max ($100–200/mo) | Opus 4.6, Sonnet 4.6, Haiku | Direct CLI invocation, no API key |
| OpenAI Codex | Pro plan | GPT-5.3, GPT-5.4 | CLI invocation |
| Gemini CLI | Free (Google account) | Gemini 2.5 Pro/Flash | CLI invocation |
| Kimi CLI | Free tier available | Kimi K2 | CLI invocation |

**CLIProxyAPI / claude-code-gateway:**
- Community tools like `CLIProxyAPI` and `claude-code-gateway` can expose Claude Max subscriptions as OpenAI-compatible API endpoints
- Jarvis could integrate these as a BYOK option: "Use my Claude Max subscription"
- Runs locally, no data shared with third parties
- Supports streaming, multiple models
- ⚠️ Anthropic's ToS may prohibit third-party OAuth use — CLI direct invocation (already in Jarvis) is safer

### BYOK Architecture

```
User Configuration (encrypted config file):
    │
    ├── anthropic_key: "sk-ant-..." (DPAPI encrypted)
    ├── openai_key: "sk-..." (DPAPI encrypted)
    ├── google_key: "AIza..." (DPAPI encrypted)
    ├── openrouter_key: "sk-or-..." (DPAPI encrypted)
    ├── cli_subscriptions:
    │     ├── claude_code: true (auto-detected)
    │     ├── codex_cli: true (auto-detected)
    │     └── gemini_cli: true (auto-detected)
    └── preferred_routing: "auto" | "cheapest" | "fastest" | "best_quality"

Gateway Resolution Order:
    1. Check if user has BYOK key for preferred provider → use it
    2. Check if CLI subscription available → use it (free for user)
    3. Fall back to Jarvis-managed API keys (premium tier)
    4. Fall back to free tier APIs (Gemini free, DeepSeek free)
    5. Fall back to local Ollama
```

### Key Security

- **Storage**: DPAPI encryption on Windows (already used for widget config per CLAUDE.md)
- **Memory**: Keys loaded into memory only when needed, zeroed after use
- **Rotation**: Support for key rotation with encrypted backup
- **Audit**: Log API usage (cost, tokens) without logging the key itself
- **Deletion**: Secure wipe of key material on user request

---

## 5. Architecture D — Hybrid Smart Routing (Decision Matrix)

### Complexity Classification (Already in `gateway/classifier.py`)

Jarvis already has an `IntentClassifier` with embedding-based routing. Extend it with cost awareness:

| Signal | Weight | Detects |
|--------|--------|---------|
| Privacy keywords | ABSOLUTE | Forces local, no override |
| Embedding similarity to route exemplars | 0.4 | Task type (math, code, routine, creative) |
| Query length | 0.1 | Short = routine, long = complex |
| Conversation depth (turn count) | 0.1 | Deep = need context, cloud better |
| Explicit user model request | ABSOLUTE | User says "use Claude" = use Claude |
| Budget remaining | 0.2 | Low budget → prefer cheaper |
| Time of day / urgency | 0.1 | Background tasks → cheapest; urgent → fastest |
| Historical feedback on model quality | 0.1 | Learn which model works best per task type |

### Decision Matrix

| Task Type | User Pref: "Always Local" | User Pref: "Auto" | User Pref: "Best Quality" | User Pref: "Cheapest" |
|-----------|:-------------------------:|:-----------------:|:-------------------------:|:---------------------:|
| Private/Personal | Local | Local | Local | Local |
| Simple Q&A | Local | Local | Haiku/Flash | Local |
| Summarization | Local | Local | Sonnet | Gemini Flash-Lite |
| Routine coding | Local | Haiku or Local | Sonnet | Local |
| Complex coding | Local | Sonnet | Opus | Haiku |
| Architecture design | Local | Opus | Opus | Sonnet |
| Math/reasoning | Local | Sonnet/Codex | Opus | DeepSeek R1 |
| Creative writing | Local | Gemini CLI | Opus | Gemini Flash |
| Research/grounding | Local | Gemini CLI | Gemini Pro | Gemini Flash |
| Multi-step agents | Local | Opus | Opus | Sonnet |

### Cost-Quality Optimization Algorithm

```python
def select_model(query, user_prefs, budget_state, available_providers):
    """
    Returns (model, provider, estimated_cost, estimated_quality)
    """
    # 1. Privacy gate — non-negotiable
    if contains_privacy_keywords(query):
        return (local_model, "ollama", 0.0, local_quality)

    # 2. User explicit override
    if user_prefs.forced_model:
        return (user_prefs.forced_model, ...)

    # 3. Classify complexity
    complexity = classifier.score(query)  # 0.0 to 1.0
    task_type = classifier.categorize(query)  # math, code, routine, etc.

    # 4. Build candidate list based on available providers
    candidates = []
    for model in MODEL_CATALOG:
        if model.provider not in available_providers:
            continue
        score = (
            model.quality_for_task[task_type] * quality_weight(user_prefs)
            + model.cost_efficiency * cost_weight(user_prefs, budget_state)
            + model.speed * speed_weight(user_prefs)
        )
        candidates.append((model, score))

    # 5. Budget gate — if near limit, penalize expensive models
    if budget_state.remaining < budget_state.monthly * 0.1:
        for c in candidates:
            if c.model.cost > CHEAP_THRESHOLD:
                c.score *= 0.3  # heavily penalize

    # 6. Return best candidate
    return max(candidates, key=lambda c: c.score)
```

### User-Configurable Preferences

```yaml
# ~/.jarvis/routing.yaml
routing:
  mode: "auto"  # auto | always_local | best_quality | cheapest | fastest
  budget:
    monthly_cap: 30.0  # USD
    hard_cap: true  # stop cloud when exceeded vs soft warning
    alerts: [50, 75, 90]  # percent thresholds
  privacy:
    always_local_topics: ["medical", "financial", "personal", "passwords"]
    never_cloud_providers: []  # e.g., ["openai"] if user distrusts
  model_preferences:
    coding: "claude-sonnet"  # preferred model per task type
    math: "codex-cli"
    creative: "gemini-cli"
    routine: "local"
  fallback_chain: ["cli_subscription", "byok_keys", "free_apis", "local"]
```

---

## 6. Architecture E — Learning Acceleration (Cloud Teaches Local)

### Concept
Use expensive cloud model outputs to improve local model quality over time, reducing long-term cloud dependency.

### Strategy 1: Supervised Fine-Tuning with LoRA

**How it works:**
1. When cloud model is used, save (query, cloud_response) pairs locally
2. Periodically fine-tune local model using these pairs as training data
3. Use LoRA (Low-Rank Adaptation) — only trains small adapter layers (~1–5% of parameters)
4. QLoRA variant works with 4-bit quantized models, needs only 6–8 GB VRAM to train

**Implementation:**
- Collect 500–2000 high-quality (query, response) pairs per domain
- Train LoRA adapter for 1–3 epochs on consumer GPU (30–60 minutes)
- Save adapter separately (~50–200 MB per domain)
- Load domain-specific adapters dynamically based on query type

**Expected Improvement:**
- Local model with LoRA adapters trained on cloud outputs: +10–25% quality improvement on domain tasks
- After sufficient training data, can handle 80% of queries that previously needed cloud

### Strategy 2: Preference-Based Training (DPO/RLHF-lite)

**How it works:**
1. For the same query, collect both local and cloud responses
2. User (or automatic quality scorer) labels which is better
3. Use Direct Preference Optimization (DPO) to train local model to prefer cloud-quality outputs
4. More sample-efficient than pure supervised fine-tuning

### Strategy 3: Prompt Distillation

**How it works:**
1. Cloud model generates detailed chain-of-thought for complex queries
2. Use these detailed reasoning traces as training data for local model
3. Local model learns to reason step-by-step, not just produce final answers
4. Most effective for math/logic tasks

### Legal & Licensing Considerations

⚠️ **This is the most legally uncertain area.** Key concerns:

| Concern | Risk Level | Mitigation |
|---------|:----------:|------------|
| **OpenAI ToS**: Outputs cannot be used to train competing models | HIGH | OpenAI ToS Section 2(c) restricts this. Do NOT fine-tune on GPT outputs for redistribution. For personal use on personal models, risk is minimal but technically violates ToS. |
| **Anthropic ToS**: Similar restrictions on output usage for model training | MEDIUM | Anthropic is somewhat more permissive for personal/research use, but check current ToS. |
| **Google Gemini**: Generally permissive for API outputs | LOW | Google's terms are more relaxed, but verify per model. |
| **Open-weight models** (Llama, Qwen, Gemma): Vary by license | LOW | Apache 2.0 (Qwen, Gemma) = fully permissive. Llama Community License = commercial OK with revenue limits. |
| **EU AI Act**: Fine-tuned models inherit risk classification | LOW | Personal assistant = minimal risk category. |
| **Copyright of outputs**: AI outputs generally not copyrightable | LOW | Training on non-copyrightable outputs is likely fine. |

**Recommended Approach:**
- ✅ Fine-tune on outputs from open-weight models (Gemma, Qwen) — no legal risk
- ✅ Fine-tune on Gemini API outputs — generally permissive
- ⚠️ Fine-tune on Claude outputs — check ToS, personal use likely OK
- ❌ Avoid fine-tuning on OpenAI outputs for anything beyond personal use
- ✅ Use preference learning (DPO) where local model is judged against cloud — this is learning from feedback, not copying outputs
- ✅ Use cloud models to generate training prompts/scenarios, then have local model generate its own responses

### Learning Pipeline Architecture

```
Cloud Query (when premium used)
    │
    ├── Save: (query, response, model, quality_score, task_type)
    │         → ~/.jarvis/training_data/
    │
    ├── Quality Filter:
    │     - Only save responses scored > 0.7 by automatic evaluator
    │     - Deduplicate similar queries
    │     - Strip any PII before saving
    │
    └── Periodic Training Job (weekly or on 500 new samples):
          │
          ├── Select domain with most training data
          ├── Train LoRA adapter (30–60 min on GPU)
          ├── Evaluate on held-out test set
          ├── If improvement > 5%, deploy adapter
          └── Update routing classifier:
                "domain X now handled locally with adapter"
```

### Projected Impact

| Phase | Cloud Dependency | Local Quality | Monthly Cost (Moderate User) |
|-------|:----------------:|:-------------:|:----------------------------:|
| Week 1 (no training) | 40% cloud | Baseline | ~$12 |
| Month 1 (first adapters) | 30% cloud | +10% | ~$8 |
| Month 3 (mature adapters) | 20% cloud | +20% | ~$5 |
| Month 6+ (well-trained) | 10% cloud | +25% | ~$2 |

---

## 7. Cost Projections Summary

### Monthly Cost by Tier and Usage Level

| Usage | Free Tier | BYOK (keys) | BYOK (CLI sub) | Premium Pay-Per-Use | Premium Subscription |
|-------|:---------:|:-----------:|:-:|:-:|:-:|
| Light (10 q/day) | $0 | $1–3 | $0* | $1–2 | $5 (Starter) |
| Moderate (30 q/day) | $0 | $5–15 | $0* | $8–15 | $20 (Pro) |
| Heavy (80 q/day) | $0 | $20–50 | $0* | $30–60 | $50 (Max) |
| Power (150+ q/day) | $0 | $50–120 | $0* | $80–150 | Custom |

*CLI subscriptions have their own monthly cost (Claude Max ~$100–200/mo, but user already pays that)

### Cost per Query by Model (Average 3K input / 2K output tokens)

| Model | Cost/Query | Quality | Speed |
|-------|:----------:|:-------:|:-----:|
| Local Ollama | $0.000 | ★★★ | ★★★★ |
| Gemini Flash-Lite | $0.001 | ★★★ | ★★★★★ |
| DeepSeek V3.2 | $0.002 | ★★★½ | ★★★★ |
| GPT-4o Mini | $0.002 | ★★★½ | ★★★★ |
| Gemini 2.5 Flash | $0.002 | ★★★★ | ★★★★★ |
| Haiku 4.5 | $0.010 | ★★★★ | ★★★★ |
| Sonnet 4.6 | $0.039 | ★★★★½ | ★★★★ |
| GPT-5.4 | $0.038 | ★★★★½ | ★★★★ |
| Gemini 3.1 Pro | $0.030 | ★★★★½ | ★★★★ |
| Opus 4.6 | $0.065 | ★★★★★ | ★★★ |

---

## 8. Implementation Recommendations for Jarvis

### Phase 1 — Immediate (Existing Infrastructure)
- [x] Local Ollama routing (already done)
- [x] Anthropic API with key (already done)
- [x] CLI provider routing (already done in `gateway/cli_providers.py`)
- [x] Complexity-based routing (already done in `gateway/classifier.py`)
- [x] Cost tracking (already done in `gateway/costs.py`)
- [ ] Add Gemini free tier as supplemental provider in `OPENAI_COMPAT_PROVIDERS`
- [ ] Add DeepSeek as supplemental provider
- [ ] Update `gateway/pricing.py` with current 2026 prices

### Phase 2 — BYOK & Budget Controls
- [ ] BYOK key storage using existing DPAPI encryption pattern
- [ ] Budget cap system with alerts (daily/monthly)
- [ ] Cost estimation before query execution ("This will cost ~$0.04, proceed?")
- [ ] User routing preferences config (`~/.jarvis/routing.yaml`)

### Phase 3 — OpenRouter Integration
- [ ] Single OpenRouter key gives access to 300+ models
- [ ] 5.5% platform fee but massive model variety
- [ ] Good fallback when specific provider is down
- [ ] Simplifies BYOK (one key instead of many)

### Phase 4 — Learning Acceleration
- [ ] Training data collection pipeline (save cloud query/response pairs)
- [ ] LoRA fine-tuning automation (weekly job)
- [ ] Adapter management (load/unload per domain)
- [ ] Routing classifier update based on local model improvements

### Key Design Decisions

1. **No Jarvis markup on API costs** — This is a personal tool, not a SaaS. User pays provider cost directly.
2. **Privacy is non-negotiable** — Privacy keywords always route local, no override possible.
3. **CLI subscriptions are the best BYOK** — Already implemented, zero API key management, user leverages existing subscriptions.
4. **OpenRouter as universal fallback** — One key, 300+ models, 5.5% fee is acceptable for convenience.
5. **LoRA training is the long game** — Reduces cloud dependency over months, but requires GPU and careful legal compliance.
6. **Budget controls are essential** — Without caps, costs can spiral. Always default to hard cap with local fallback.

---

## 9. OpenRouter as Unified Backend

OpenRouter deserves special mention as potentially the simplest path to multi-model access:

**Advantages:**
- Single API key for 300+ models across 60+ providers
- OpenAI-compatible SDK — drop-in replacement
- No markup on token prices (only 5.5% platform fee)
- Automatic fallback routing if a provider is down
- Free tier (50 req/day) for testing
- Supports prompt caching pass-through

**For Jarvis, OpenRouter could simplify the gateway to:**
```
Local Ollama → OpenRouter (all cloud models) → CLI fallback
```

Instead of managing 6+ provider-specific integrations, Jarvis could use OpenRouter as the single cloud backend with user's BYOK OpenRouter key.

**Tradeoff:** Slightly higher cost (5.5% fee) vs much simpler codebase and maintenance.

---

*End of research document. All pricing sourced from March 2026 web searches. Prices change frequently — verify before implementation.*
