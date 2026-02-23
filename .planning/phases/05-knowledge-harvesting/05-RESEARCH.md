# Phase 5: Knowledge Harvesting - Research

**Researched:** 2026-02-23
**Domain:** Multi-source LLM API integration, knowledge deduplication, budget control
**Confidence:** HIGH

## Summary

Phase 5 builds a multi-source knowledge harvesting system that queries MiniMax, Kimi, and Gemini APIs to extract knowledge on user-specified topics, plus ingests learning outputs from Claude Code and Codex sessions. All harvested knowledge flows through the existing EnrichedIngestPipeline (Phase 1) and gets validated against the KnowledgeGraph (Phase 2), with cost tracking via the existing CostTracker (Phase 3).

The key architectural insight is that all three external APIs (MiniMax, Kimi, Gemini) support OpenAI-compatible chat completion endpoints, meaning we can build a single `HarvesterProvider` base class using the `openai` Python client with different `base_url` and `api_key` values. For Gemini, Google's own `google-genai` SDK is the official approach but the OpenAI-compatible interface also works. Claude Code and Codex sessions are JSONL files stored locally, so those are file-parsing tasks rather than API integrations.

**Primary recommendation:** Use the OpenAI Python SDK as the unified client for MiniMax and Kimi (both support OpenAI-compatible endpoints). Use `google-genai` for Gemini (official SDK, better free-tier integration). Parse Claude Code and Codex session JSONL files directly from their local storage paths. Feed all harvested content through the existing `EnrichedIngestPipeline.ingest()` method for deduplication and fact extraction.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| HARV-01 | Knowledge harvester can query MiniMax API to extract and distill knowledge on specified topics | OpenAI-compatible endpoint at `api.minimax.io/v1`, model `MiniMax-M2.5`, $0.30/$1.20 per Mtok. Free credits for new accounts. |
| HARV-02 | Knowledge harvester can query Kimi API to extract and distill knowledge on specified topics | OpenAI-compatible endpoint at `api.moonshot.cn/v1` (Moonshot direct) or `integrate.api.nvidia.com/v1` (NVIDIA NIM free). Model `kimi-k2.5` at $0.60/$2.50 per Mtok. NVIDIA NIM route is free with no payment required. |
| HARV-03 | Knowledge harvester can ingest learning outputs from Claude Code sessions | JSONL files at `~/.claude/projects/<path>/sessions/<uuid>.jsonl`. Each line is a JSON object with `type`, `message`, `timestamp`. Filter for assistant responses containing knowledge. |
| HARV-04 | Knowledge harvester can ingest learning outputs from Codex sessions | JSONL files at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Similar line-by-line JSON structure with message content and metadata. |
| HARV-05 | Knowledge harvester can query Gemini API (free tier) to extract and distill knowledge on specified topics | `google-genai` SDK, model `gemini-2.5-flash`, free tier: 10 RPM, 250 RPD, 250K TPM. No credit card required. |
| HARV-06 | Harvested knowledge is deduplicated, validated against existing facts, and ingested through the standard memory pipeline | Use existing `EnrichedIngestPipeline.ingest()` which handles SHA-256 content-hash dedup, embedding generation, branch classification, and KG fact extraction with contradiction quarantine. Add semantic dedup layer (cosine similarity > 0.92 = duplicate). |
| HARV-07 | Cost tracking per API source with configurable budget limits per day/month | Extend existing `CostTracker` with budget limit table and pre-query budget check. Add `harvest_budgets` SQLite table with per-provider daily/monthly caps. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openai | >=1.0.0 | Unified client for MiniMax and Kimi APIs | Both APIs expose OpenAI-compatible endpoints; avoids adding 2 separate SDKs |
| google-genai | >=1.0.0 | Gemini API client | Official Google SDK, GA since May 2025, best free-tier support |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| (no new deps) | - | JSONL parsing for Claude Code/Codex | stdlib `json` module handles JSONL line-by-line |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| openai SDK for MiniMax | `minimax` official SDK | Extra dependency; OpenAI SDK already in ecosystem and covers both MiniMax+Kimi |
| openai SDK for Kimi | `httpx` direct HTTP | More control but more boilerplate; OpenAI SDK handles retries and streaming |
| google-genai for Gemini | openai SDK with Gemini's OpenAI-compat endpoint | Gemini's OpenAI-compat mode is less documented; native SDK is better for free tier auth |
| NVIDIA NIM for Kimi | Moonshot direct API | NVIDIA NIM is free but may have availability limits; Moonshot direct is $0.60/Mtok. Support both as fallback. |

**Installation:**
```bash
pip install openai>=1.0.0 google-genai>=1.0.0
```

Note: `openai` is a lightweight package (~200KB) already compatible with the project. The `anthropic` SDK is already installed but MiniMax/Kimi do not use it.

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
  harvesting/
    __init__.py          # Package exports: KnowledgeHarvester, HarvestCommand
    providers.py         # Base HarvesterProvider + MiniMax, Kimi, Gemini providers
    session_ingestors.py # Claude Code + Codex JSONL parsers
    harvester.py         # Orchestrator: topic -> multi-source query -> dedup -> ingest
    budget.py            # BudgetManager: per-provider daily/monthly limits
  handlers/
    harvest_handlers.py  # Command Bus handlers for HarvestCommand, IngestSessionCommand
```

### Pattern 1: Provider Abstraction with OpenAI SDK
**What:** Single base class wrapping the OpenAI SDK with per-provider config (base_url, api_key, model name, pricing).
**When to use:** For all three external API providers (MiniMax, Kimi, Gemini-via-openai or native SDK).
**Example:**
```python
# Source: MiniMax and Kimi both use OpenAI-compatible endpoints
from openai import OpenAI

class HarvesterProvider:
    """Base class for knowledge harvesting providers."""

    def __init__(self, name: str, api_key: str, base_url: str,
                 model: str, input_cost_per_mtok: float, output_cost_per_mtok: float):
        self.name = name
        self.model = model
        self.input_cost_per_mtok = input_cost_per_mtok
        self.output_cost_per_mtok = output_cost_per_mtok
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def query(self, topic: str, system_prompt: str, max_tokens: int = 2048) -> HarvestResult:
        """Query this provider for knowledge on a topic."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Explain everything you know about: {topic}"},
            ],
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = (input_tokens * self.input_cost_per_mtok +
                output_tokens * self.output_cost_per_mtok) / 1_000_000
        return HarvestResult(
            provider=self.name, text=text, model=self.model,
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
        )


class MiniMaxProvider(HarvesterProvider):
    def __init__(self, api_key: str):
        super().__init__(
            name="minimax", api_key=api_key,
            base_url="https://api.minimax.io/v1",
            model="MiniMax-M2.5",
            input_cost_per_mtok=0.30,
            output_cost_per_mtok=1.20,
        )


class KimiProvider(HarvesterProvider):
    """Kimi via Moonshot direct API."""
    def __init__(self, api_key: str):
        super().__init__(
            name="kimi", api_key=api_key,
            base_url="https://api.moonshot.cn/v1",
            model="kimi-k2.5",
            input_cost_per_mtok=0.60,
            output_cost_per_mtok=2.50,
        )


class KimiNvidiaProvider(HarvesterProvider):
    """Kimi via NVIDIA NIM (free tier)."""
    def __init__(self, api_key: str):
        super().__init__(
            name="kimi_nvidia", api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            model="moonshotai/kimi-k2-5",
            input_cost_per_mtok=0.0,  # Free via NVIDIA NIM
            output_cost_per_mtok=0.0,
        )
```

### Pattern 2: Gemini Native SDK Provider
**What:** Separate provider class using `google-genai` SDK since Gemini's native SDK has better free-tier auth.
**When to use:** For Gemini queries specifically.
**Example:**
```python
# Source: https://ai.google.dev/gemini-api/docs/quickstart
from google import genai

class GeminiProvider:
    """Gemini knowledge harvester using native google-genai SDK."""

    def __init__(self, api_key: str | None = None):
        self.name = "gemini"
        self.model = "gemini-2.5-flash"
        self.input_cost_per_mtok = 0.0   # Free tier
        self.output_cost_per_mtok = 0.0   # Free tier
        # Client reads GEMINI_API_KEY env var if api_key is None
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def query(self, topic: str, system_prompt: str, max_tokens: int = 2048) -> HarvestResult:
        response = self._client.models.generate_content(
            model=self.model,
            contents=f"{system_prompt}\n\nExplain everything you know about: {topic}",
        )
        text = response.text or ""
        # Gemini free tier does not report usage tokens in response
        return HarvestResult(
            provider=self.name, text=text, model=self.model,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
        )
```

### Pattern 3: Session File Ingestor
**What:** Parse JSONL session files from Claude Code and Codex local storage, extract assistant-generated knowledge content.
**When to use:** For HARV-03 and HARV-04 (session ingestion).
**Example:**
```python
import json
from pathlib import Path

class ClaudeCodeIngestor:
    """Parse Claude Code session JSONL files for knowledge extraction."""

    SESSION_BASE = Path.home() / ".claude" / "projects"

    def ingest_session(self, session_path: Path) -> list[str]:
        """Extract knowledge-bearing assistant messages from a session file."""
        contents = []
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Claude Code format: {"type": "assistant", "message": {"role": "assistant", "content": [...]}}
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message", {})
                content = message.get("content", [])
                if isinstance(content, str):
                    contents.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if len(text) > 100:  # Skip short tool outputs
                                contents.append(text)
        return contents

    def find_sessions(self, project_path: str | None = None) -> list[Path]:
        """Find all session JSONL files, optionally filtered by project."""
        if project_path:
            base = self.SESSION_BASE / project_path / "sessions"
        else:
            base = self.SESSION_BASE
        return sorted(base.rglob("*.jsonl"))


class CodexIngestor:
    """Parse Codex CLI session JSONL files for knowledge extraction."""

    SESSION_BASE = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"

    def ingest_session(self, session_path: Path) -> list[str]:
        """Extract knowledge-bearing content from a Codex session log."""
        contents = []
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Codex stores assistant messages with content arrays
                content = entry.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if len(text) > 100:
                                contents.append(text)
                elif isinstance(content, str) and len(content) > 100:
                    contents.append(content)
        return contents

    def find_sessions(self, days_back: int = 7) -> list[Path]:
        """Find recent session files."""
        return sorted(self.SESSION_BASE.rglob("rollout-*.jsonl"))
```

### Pattern 4: Knowledge Harvester Orchestrator
**What:** Coordinates multi-provider queries, deduplication, and ingestion for a single topic.
**When to use:** Main entry point for the harvesting workflow.
**Example:**
```python
@dataclass(frozen=True)
class HarvestCommand:
    """Command to harvest knowledge about a topic from multiple sources."""
    topic: str
    providers: list[str] | None = None  # None = all configured providers
    max_tokens: int = 2048

@dataclass
class HarvestResult:
    provider: str
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

class KnowledgeHarvester:
    """Orchestrates multi-source knowledge harvesting."""

    SYSTEM_PROMPT = (
        "You are a knowledge extraction assistant. Provide factual, structured, "
        "and comprehensive information about the requested topic. "
        "Present information as clear statements of fact. "
        "Avoid opinions, speculation, and conversational filler."
    )

    def __init__(self, providers, pipeline, budget_manager, cost_tracker):
        self._providers = {p.name: p for p in providers}
        self._pipeline = pipeline  # EnrichedIngestPipeline
        self._budget = budget_manager
        self._cost_tracker = cost_tracker

    def harvest(self, cmd: HarvestCommand) -> dict:
        """Query multiple providers and ingest results."""
        results = []
        provider_names = cmd.providers or list(self._providers.keys())

        for name in provider_names:
            provider = self._providers.get(name)
            if not provider:
                continue

            # Budget check before query
            if not self._budget.can_spend(name):
                results.append({"provider": name, "status": "budget_exceeded"})
                continue

            try:
                result = provider.query(cmd.topic, self.SYSTEM_PROMPT, cmd.max_tokens)
                # Log cost
                self._cost_tracker.log(
                    model=result.model, provider=result.provider,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd, route_reason=f"harvest:{cmd.topic}",
                )
                self._budget.record_spend(name, result.cost_usd)

                # Ingest through standard pipeline
                record_ids = self._pipeline.ingest(
                    source=f"harvest:{name}",
                    kind="semantic",
                    task_id=f"harvest:{cmd.topic}",
                    content=result.text,
                    tags=["harvested", name, cmd.topic.replace(" ", "_")[:30]],
                )
                results.append({
                    "provider": name, "status": "ok",
                    "records_created": len(record_ids),
                    "cost_usd": result.cost_usd,
                })
            except Exception as exc:
                results.append({"provider": name, "status": "error", "error": str(exc)})

        return {"topic": cmd.topic, "results": results}
```

### Anti-Patterns to Avoid
- **Building separate storage for harvested knowledge:** Always use `EnrichedIngestPipeline.ingest()`. Never store harvested content in a parallel data path. The existing pipeline handles dedup, embedding, branch classification, and fact extraction.
- **Synchronous multi-provider queries without timeouts:** Each API call can take 5-30 seconds. Use `concurrent.futures.ThreadPoolExecutor` for parallel queries with per-call timeout. Do NOT use asyncio (the codebase is synchronous).
- **Hardcoding API keys:** Use environment variables (`MINIMAX_API_KEY`, `KIMI_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`) following the existing `ANTHROPIC_API_KEY` pattern.
- **Trusting harvested facts at high confidence:** Harvested content should enter the pipeline at confidence 0.50 (lower than the 0.72 default for direct ingestion). It needs multi-source corroboration to reach lock threshold.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OpenAI-compatible API client | Custom HTTP client with retries | `openai` Python SDK | Handles retries, rate limits, streaming, auth |
| Gemini API client | Raw `httpx` calls | `google-genai` SDK | Handles auth, token counting, free tier limits |
| Content deduplication | Custom string matching | Existing SHA-256 content-hash in `EnrichedIngestPipeline` + embedding cosine for semantic dedup | Pipeline already handles exact dedup; add cosine check for near-duplicates |
| Fact extraction from harvested text | New fact parser | Existing `FactExtractor` via `EnrichedIngestPipeline` | Pipeline already calls `_extract_facts()` on every chunk |
| Contradiction detection | New contradiction checker | Existing `KnowledgeGraph.add_fact()` with auto-quarantine | Locked facts trigger quarantine automatically |
| Cost tracking | New cost database | Existing `CostTracker.log()` | Already logs per-query costs in SQLite; extend with budget limits |
| Rate limiting per provider | Custom sliding window | `time.sleep()` with simple interval tracking | At 5-15 RPM Gemini limits, a simple last-call timestamp per provider is sufficient. No need for complex token bucket. |

**Key insight:** The biggest advantage of the Phase 1-3 foundation is that harvested content can be treated identically to any other ingested content. The `EnrichedIngestPipeline.ingest()` method handles chunking, SHA-256 dedup, embedding, branch classification, and KG fact extraction. The harvesting system's job is purely: (1) query external APIs, (2) format the response as content text, (3) call `pipeline.ingest()`.

## Common Pitfalls

### Pitfall 1: API Key Configuration Sprawl
**What goes wrong:** Five different API providers means five environment variables, and forgetting any one causes a runtime crash.
**Why it happens:** No graceful degradation for missing API keys.
**How to avoid:** Initialize providers only when their API key is set. Log a warning for missing keys. The harvester should work with any subset of providers (even just one).
**Warning signs:** `KeyError` or `None` passed to API client constructor.

### Pitfall 2: Budget Overrun from Parallel Queries
**What goes wrong:** Three providers queried simultaneously all succeed, but the combined cost exceeds the daily budget.
**Why it happens:** Budget check happens before each query, but all three pass the check at the same time.
**How to avoid:** Use pessimistic budget reservation: estimate max cost before query, reserve it, then adjust after actual cost is known. For harvesting's modest query volume (not real-time), sequential queries per topic are simpler and avoid this entirely.
**Warning signs:** Daily cost exceeding configured limit by 2-3x.

### Pitfall 3: Claude Code Session Path Varies by Platform
**What goes wrong:** Hardcoding `~/.claude/projects/` fails on Windows where the actual path may differ.
**Why it happens:** Windows uses different home directory structure.
**How to avoid:** Use `Path.home() / ".claude" / "projects"` which resolves correctly on Windows. The project already runs on Windows 11. Also check for `CLAUDE_CONFIG_DIR` environment variable.
**Warning signs:** `FileNotFoundError` when scanning for sessions.

### Pitfall 4: Ingesting Low-Quality Filler Text
**What goes wrong:** LLM responses contain conversational filler ("That's a great question!", "I hope this helps!"), disclaimers, and repetitive preambles that pollute the knowledge base.
**Why it happens:** LLMs are trained to be conversational, not to produce clean knowledge statements.
**How to avoid:** Use a knowledge-extraction system prompt that instructs the model to output only factual statements. Post-process: strip common filler patterns before ingestion. Set a minimum content quality threshold (e.g., at least 3 sentences, at least 200 characters of substantive content).
**Warning signs:** Knowledge graph filling with low-value nodes like "I'd be happy to help" as fact labels.

### Pitfall 5: Gemini Free Tier Rate Limit Exhaustion
**What goes wrong:** Harvesting bursts use all 250 daily Gemini requests in one session, leaving no capacity for the rest of the day.
**Why it happens:** Gemini's free tier allows only 250 requests per day for `gemini-2.5-flash` (reduced from earlier quotas in Dec 2025).
**How to avoid:** Budget manager should track request counts per day in addition to cost. For Gemini specifically, limit harvesting to ~50 requests per topic batch, reserving capacity for other uses.
**Warning signs:** 429 rate limit errors from Gemini API.

### Pitfall 6: Semantic Near-Duplicates from Multiple Providers
**What goes wrong:** MiniMax, Kimi, and Gemini all return overlapping knowledge about the same topic. The SHA-256 dedup only catches exact duplicates, so three slightly different phrasings of the same fact all get stored.
**Why it happens:** Content-hash dedup catches identical text only. Different wording of the same knowledge creates distinct hashes.
**How to avoid:** Add a semantic dedup pre-check: before ingesting a harvested chunk, compute its embedding and check cosine similarity against recent harvested records. If cosine > 0.92 (HIGH confidence threshold for near-duplicate), skip the chunk. This uses the existing `EmbeddingService.embed()`.
**Warning signs:** Knowledge graph showing 3x duplicate facts after multi-provider harvest on the same topic.

## Code Examples

### Verified: MiniMax Chat Completion via OpenAI SDK
```python
# Source: https://platform.minimax.io/docs/api-reference/text-openai-api
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_MINIMAX_API_KEY",
    base_url="https://api.minimax.io/v1",
)

response = client.chat.completions.create(
    model="MiniMax-M2.5",
    messages=[
        {"role": "system", "content": "Extract factual knowledge."},
        {"role": "user", "content": "Explain quantum computing basics."},
    ],
    max_tokens=2048,
)
print(response.choices[0].message.content)
# Usage: response.usage.prompt_tokens, response.usage.completion_tokens
```

### Verified: Kimi Chat Completion via Moonshot API
```python
# Source: https://kimi-k25.com/blog/kimi-k2-5-api
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_KIMI_API_KEY",
    base_url="https://api.moonshot.cn/v1",
)

response = client.chat.completions.create(
    model="kimi-k2.5",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing basics."},
    ],
    temperature=0.7,
    max_tokens=2048,
)
print(response.choices[0].message.content)
```

### Verified: Kimi via NVIDIA NIM (Free)
```python
# Source: https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k2-5
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_NVIDIA_API_KEY",
    base_url="https://integrate.api.nvidia.com/v1",
)

response = client.chat.completions.create(
    model="moonshotai/kimi-k2-5",
    messages=[
        {"role": "system", "content": "You are Kimi, an AI assistant."},
        {"role": "user", "content": "Explain quantum computing basics."},
    ],
    max_tokens=4096,
    extra_body={"thinking": {"type": "disabled"}},  # Instant mode for faster response
)
print(response.choices[0].message.content)
```

### Verified: Gemini via google-genai SDK
```python
# Source: https://ai.google.dev/gemini-api/docs/quickstart
from google import genai

# Reads GEMINI_API_KEY env var automatically, or pass api_key=
client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Explain everything you know about quantum computing basics.",
)
print(response.text)
```

### Budget Manager Schema
```python
# SQLite table for per-provider budget tracking
"""
CREATE TABLE IF NOT EXISTS harvest_budgets (
    provider TEXT NOT NULL,
    period TEXT NOT NULL,            -- 'daily' or 'monthly'
    limit_usd REAL NOT NULL,
    limit_requests INTEGER NOT NULL DEFAULT 0,  -- 0 = no request limit
    PRIMARY KEY (provider, period)
);

CREATE TABLE IF NOT EXISTS harvest_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    cost_usd REAL NOT NULL DEFAULT 0.0,
    request_count INTEGER NOT NULL DEFAULT 1,
    topic TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_harvest_spend_provider_ts
    ON harvest_spend(provider, ts);
"""
```

### Semantic Dedup Check
```python
def is_semantic_duplicate(
    new_embedding: list[float],
    engine: MemoryEngine,
    threshold: float = 0.92,
    recent_hours: int = 24,
) -> bool:
    """Check if content is semantically near-duplicate of recent records.

    Uses existing MemoryEngine's vector search to find closest match.
    If cosine similarity exceeds threshold, content is a near-duplicate.
    """
    # Use the engine's existing vector search
    results = engine.search_by_vector(new_embedding, limit=5)
    for result in results:
        if result.get("score", 0.0) > threshold:
            return True
    return False
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| google-generativeai SDK | google-genai SDK | May 2025 (GA) | Old package deprecated; must use new `google-genai` |
| MiniMax custom REST API | MiniMax OpenAI-compatible API | 2025 | Can use `openai` SDK directly, no custom client needed |
| Kimi v1 API (moonshot-v1-*) | Kimi K2.5 via OpenAI-compat | Jan 2026 | New K2.5 model, same OpenAI-compat interface |
| Gemini free tier generous limits | Reduced free tier (Dec 2025) | Dec 2025 | 50-80% reduction in free tier quotas; plan for 250 RPD on Flash |

**Deprecated/outdated:**
- `google-generativeai` PyPI package: Deprecated, use `google-genai` instead
- MiniMax v1 group/token auth: Replaced by standard API key auth with OpenAI-compatible endpoint
- Kimi `moonshot-v1-8k` / `moonshot-v1-32k` models: Replaced by `kimi-k2.5` (256K context)

## API Pricing Summary

| Provider | Model | Input $/Mtok | Output $/Mtok | Free Tier | Rate Limits (Free) |
|----------|-------|-------------|---------------|-----------|-------------------|
| MiniMax | MiniMax-M2.5 | $0.30 | $1.20 | New account credits (~1000 credits) | 20 RPM, 1M TPM |
| Kimi (Moonshot) | kimi-k2.5 | $0.60 | $2.50 | ~$1 min recharge; $5 bonus at $5 recharge | ~3 RPM (trial) |
| Kimi (NVIDIA NIM) | moonshotai/kimi-k2-5 | $0.00 | $0.00 | Free, no payment required | TBD (NVIDIA manages) |
| Gemini | gemini-2.5-flash | $0.00 | $0.00 | Full free tier, no credit card | 10 RPM, 250 RPD, 250K TPM |
| Gemini | gemini-2.5-flash-lite | $0.00 | $0.00 | Full free tier, no credit card | 15 RPM, 1000 RPD, 250K TPM |
| Claude (existing) | claude-sonnet-4-5 | $3.00 | $15.00 | Via existing Anthropic key | Per API plan |

**Cost-optimized strategy:** Use Gemini free tier as primary harvesting source (250 free requests/day), NVIDIA NIM Kimi as secondary (free), MiniMax as tertiary (cheap at $0.30/$1.20 per Mtok). Reserve Anthropic/Claude for complex reasoning tasks via existing ModelGateway -- do NOT use it for bulk knowledge harvesting.

## Open Questions

1. **NVIDIA NIM rate limits for Kimi K2.5**
   - What we know: NVIDIA offers free access to Kimi K2.5, no payment required
   - What's unclear: Exact rate limits for free tier on NVIDIA NIM (not documented publicly)
   - Recommendation: Implement with exponential backoff; if rate-limited, fall back to Moonshot direct API

2. **Claude Code session JSONL exact schema on Windows**
   - What we know: Sessions stored in `~/.claude/projects/<path>/sessions/<uuid>.jsonl` with `{type, message, timestamp}` structure
   - What's unclear: Whether Windows path encoding differs (e.g., URL-encoded project paths on Windows)
   - Recommendation: Test actual file paths on the project's Windows 11 machine; fall back to glob patterns

3. **Codex CLI session log completeness**
   - What we know: Sessions at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
   - What's unclear: Whether the JSONL includes full assistant response text or just tool calls
   - Recommendation: Parse and inspect actual session files; filter for entries with substantial text content

4. **Gemini free tier token counting**
   - What we know: `google-genai` SDK response may not include usage metadata on free tier
   - What's unclear: Whether `response.usage_metadata` is populated for free tier requests
   - Recommendation: Fall back to character-based estimation (~4 chars per token) if usage metadata unavailable

## Sources

### Primary (HIGH confidence)
- [MiniMax OpenAI-Compatible API Docs](https://platform.minimax.io/docs/api-reference/text-openai-api) - endpoint URL, model names, request format
- [Google Gemini API Quickstart](https://ai.google.dev/gemini-api/docs/quickstart) - google-genai SDK usage
- [Google Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing) - free tier model availability
- [Kimi K2.5 API Guide](https://kimi-k25.com/blog/kimi-k2-5-api) - Moonshot endpoint, pricing, OpenAI-compat usage
- [NVIDIA NIM Kimi K2.5 API Reference](https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k2-5) - NVIDIA endpoint, model name, code examples
- [Claude Code Headless Mode](https://code.claude.com/docs/en/headless) - programmatic access patterns
- [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) - session file format details
- [Codex CLI Features](https://developers.openai.com/codex/cli/features/) - session log location

### Secondary (MEDIUM confidence)
- [Gemini Free Tier Rate Limits Guide](https://www.aifreeapi.com/en/posts/gemini-api-free-tier-rate-limits) - specific RPM/RPD numbers for free tier
- [MiniMax Pricing](https://pricepertoken.com/pricing-page/provider/minimax) - per-token pricing
- [Kimi K2.5 on OpenRouter](https://openrouter.ai/moonshotai/kimi-k2.5) - alternative access route pricing

### Tertiary (LOW confidence)
- MiniMax free account credits amount (~1000) - single source, may vary
- NVIDIA NIM rate limits - not publicly documented
- Codex JSONL exact schema fields - limited documentation, needs runtime verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all three APIs have official docs confirming OpenAI-compatible or native SDK interfaces
- Architecture: HIGH - provider abstraction pattern is straightforward; integration with existing pipeline is well-defined
- API pricing/limits: MEDIUM - free tier limits may change; NVIDIA NIM limits undocumented
- Session file parsing: MEDIUM - Claude Code format well-documented; Codex format less documented, needs runtime testing
- Pitfalls: HIGH - based on direct API documentation and codebase analysis

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (30 days - APIs are stable, free tier limits may shift)
