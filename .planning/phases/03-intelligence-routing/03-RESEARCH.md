# Phase 3: Intelligence Routing - Research

**Researched:** 2026-02-22
**Domain:** LLM model gateway, intent classification, cost tracking
**Confidence:** HIGH

## Summary

Phase 3 builds a unified model gateway that routes queries to the right model for the job: Claude Opus for complex reasoning, Claude Sonnet for routine summarization, and local Ollama for simple or private tasks. The codebase already has a primitive `ModelRouter` in `router.py` (27 lines, routes by risk/complexity enum) and Ollama HTTP integration in `task_orchestrator.py` (via urllib). This phase replaces the primitive router with an intent-based classifier, adds Anthropic API support via the official SDK, implements a fallback chain (cloud -> local), and adds per-query cost tracking in SQLite.

The project explicitly declares LangChain and LiteLLM as out of scope ("massive overhead for a 2-provider routing problem, direct SDKs preferred"). The implementation uses the `anthropic` Python SDK directly for cloud calls and the `ollama` Python library for local calls, with a thin gateway abstraction owned by the project. Intent classification uses the project's existing embedding service (nomic-embed-text-v1.5, already loaded for memory search) with cosine similarity against pre-computed route descriptors -- no additional ML dependencies required.

**Primary recommendation:** Build a `ModelGateway` class that wraps `anthropic.Anthropic` and `ollama.Client` behind a unified `complete(messages, model_hint)` interface, with an `IntentClassifier` that reuses the existing `EmbeddingService` to route queries by semantic similarity to predefined complexity exemplars, and a `CostTracker` that logs every query to a new SQLite table in the existing `jarvis_memory.db`.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INTL-01 | Model gateway provides unified interface to Ollama (local), Anthropic API (Claude Opus/Sonnet), and other cloud APIs | ModelGateway class wrapping anthropic SDK + ollama library behind common interface. Existing Ollama HTTP pattern in task_orchestrator.py provides foundation. |
| INTL-02 | Intent classifier routes queries by complexity: Opus for complex reasoning/coding, Sonnet for routine summarization, local Ollama for simple/private tasks | IntentClassifier using existing EmbeddingService (nomic-embed-text-v1.5) with cosine similarity against pre-computed route exemplars. Privacy detection for local-only routing. |
| INTL-03 | Fallback chain handles API failures gracefully (cloud unavailable -> local Ollama) | Anthropic SDK has built-in retry (2 retries, exponential backoff). Gateway catches APIConnectionError/APIStatusError and falls back to Ollama. Notification mechanism via return metadata. |
| INTL-04 | Cost tracking per-query stored in SQLite for budget monitoring | New `query_costs` table in existing jarvis_memory.db. Anthropic SDK returns `message.usage.input_tokens` and `output_tokens` directly. Pricing lookup table maps model -> cost/MTok. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | >=0.81.0 | Official Anthropic Python SDK for Claude API calls | Official SDK, typed responses, built-in retry, streaming support, token counting in response.usage |
| ollama | >=0.4.0 | Official Ollama Python client for local model calls | Official client, handles connection errors cleanly, replaces raw urllib calls in task_orchestrator.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| sqlite3 (stdlib) | built-in | Cost tracking table in existing jarvis_memory.db | Always -- no new dependency, reuses existing DB |
| sentence-transformers | (already installed) | EmbeddingService for intent classification | Reuse existing nomic-embed-text-v1.5 embeddings for route similarity |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct anthropic SDK | LiteLLM | LiteLLM adds unified interface for 100+ providers but project explicitly declares it out of scope. Two providers (Anthropic + Ollama) do not justify the overhead. |
| Direct ollama library | Raw urllib (existing pattern) | ollama library adds proper error types (ResponseError, ConnectionError), model listing, and async support. Marginal dependency for significant code quality improvement. |
| Embedding-based intent classification | semantic-router library | semantic-router is a dedicated intent routing library but adds a heavy dependency (requires its own embeddings, routes, etc). Project already has EmbeddingService with cosine similarity -- 50 lines of code achieves the same result without a new dependency. |
| Embedding-based intent classification | LLM-based classification (send query to small model first) | Adds latency (500ms+) and cost to every query. Embedding similarity is near-instant once embeddings are loaded (already loaded for memory search). |

**Installation:**
```bash
pip install "anthropic>=0.81.0" "ollama>=0.4.0"
```

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
├── gateway/                 # NEW: Intelligence routing module
│   ├── __init__.py
│   ├── models.py            # ModelGateway: unified interface to all providers
│   ├── classifier.py        # IntentClassifier: routes queries by complexity
│   ├── costs.py             # CostTracker: per-query SQLite cost logging
│   └── pricing.py           # Static pricing table for cost calculation
├── commands/
│   └── task_commands.py     # Updated: RouteCommand enhanced with query text
├── handlers/
│   └── task_handlers.py     # Updated: RouteHandler uses new gateway
├── router.py                # REPLACED by gateway/classifier.py
└── ...
```

### Pattern 1: Unified Model Gateway
**What:** A single `ModelGateway` class provides `complete(messages, model=None, max_tokens=1024)` that dispatches to the correct provider based on model name prefix or explicit routing.
**When to use:** Every query that needs LLM completion -- CLI, mobile API, daemon, daily brief.
**Example:**
```python
# Source: Anthropic SDK docs + Ollama Python docs
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError
from ollama import Client as OllamaClient, ResponseError

class ModelGateway:
    def __init__(self, anthropic_api_key: str | None, ollama_host: str = "http://127.0.0.1:11434"):
        self._anthropic = Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None
        self._ollama = OllamaClient(host=ollama_host)
        self._cost_tracker: CostTracker | None = None

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
    ) -> GatewayResponse:
        provider = self._resolve_provider(model)
        try:
            if provider == "anthropic":
                return self._call_anthropic(messages, model, max_tokens)
            elif provider == "ollama":
                return self._call_ollama(messages, model, max_tokens)
        except (APIConnectionError, APIStatusError) as exc:
            # Fallback to local Ollama on cloud failure
            return self._fallback_to_ollama(messages, max_tokens, reason=str(exc))

    def _call_anthropic(self, messages, model, max_tokens) -> GatewayResponse:
        response = self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        text = response.content[0].text
        usage = response.usage  # Usage(input_tokens=N, output_tokens=N)
        if self._cost_tracker:
            self._cost_tracker.log(model=model, input_tokens=usage.input_tokens,
                                   output_tokens=usage.output_tokens)
        return GatewayResponse(text=text, model=model, provider="anthropic",
                               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)

    def _call_ollama(self, messages, model, max_tokens) -> GatewayResponse:
        response = self._ollama.chat(model=model, messages=messages)
        text = response.message.content
        # Ollama does not charge money, but track for analytics
        if self._cost_tracker:
            self._cost_tracker.log(model=model, input_tokens=0, output_tokens=0, cost_usd=0.0)
        return GatewayResponse(text=text, model=model, provider="ollama",
                               input_tokens=0, output_tokens=0)
```

### Pattern 2: Embedding-Based Intent Classification
**What:** Classify query complexity by computing cosine similarity between the query embedding and pre-computed exemplar embeddings for each route (complex, routine, simple/private).
**When to use:** When the user sends a query without specifying a model -- the classifier picks the best model.
**Example:**
```python
import numpy as np

class IntentClassifier:
    ROUTES = {
        "complex": [
            "write a Python script that implements a binary search tree with balancing",
            "analyze this codebase and suggest architectural improvements",
            "help me debug this race condition in my threading code",
            "explain the tradeoffs between CQRS and event sourcing",
            "review this security policy and identify vulnerabilities",
        ],
        "routine": [
            "summarize this article for me",
            "rewrite this paragraph to be more concise",
            "what are the key points from this meeting transcript",
            "translate this text to French",
            "format this data as a table",
        ],
        "simple_private": [
            "what's on my calendar today",
            "what medications do I take",
            "remind me about my doctor appointment",
            "what did I have for dinner yesterday",
            "show me my recent bills",
        ],
    }

    MODEL_MAP = {
        "complex": "claude-opus-4-5-20250929",
        "routine": "claude-sonnet-4-5-20250929",
        "simple_private": "qwen3:14b",  # local Ollama
    }

    def __init__(self, embed_service):
        self._embed = embed_service
        self._route_embeddings = self._precompute_routes()

    def _precompute_routes(self) -> dict[str, np.ndarray]:
        result = {}
        for route, exemplars in self.ROUTES.items():
            embeddings = [self._embed.embed(ex) for ex in exemplars]
            result[route] = np.mean(embeddings, axis=0)  # centroid
        return result

    def classify(self, query: str) -> tuple[str, str]:
        """Returns (route_name, model_name)."""
        query_emb = self._embed.embed(query)
        best_route = max(
            self._route_embeddings,
            key=lambda r: self._cosine_sim(query_emb, self._route_embeddings[r])
        )
        return best_route, self.MODEL_MAP[best_route]

    @staticmethod
    def _cosine_sim(a, b) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
```

### Pattern 3: Fallback Chain with User Notification
**What:** When cloud API is unavailable, automatically fall back to local Ollama and include a notification in the response metadata.
**When to use:** Every cloud API call should be wrapped in a fallback.
**Example:**
```python
@dataclass
class GatewayResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    fallback_used: bool = False
    fallback_reason: str = ""

def _fallback_to_ollama(self, messages, max_tokens, reason: str) -> GatewayResponse:
    fallback_model = "qwen3:14b"  # or configurable
    try:
        response = self._ollama.chat(model=fallback_model, messages=messages)
        return GatewayResponse(
            text=response.message.content,
            model=fallback_model,
            provider="ollama",
            fallback_used=True,
            fallback_reason=f"Cloud API unavailable: {reason}. Using local Ollama.",
        )
    except (ConnectionError, ResponseError) as exc:
        return GatewayResponse(
            text="",
            model="none",
            provider="none",
            fallback_used=True,
            fallback_reason=f"All providers failed. Cloud: {reason}. Local: {exc}",
        )
```

### Pattern 4: SQLite Cost Tracking
**What:** Log every LLM query to a `query_costs` table in the existing `jarvis_memory.db`.
**When to use:** After every successful LLM call.
**Example:**
```python
class CostTracker:
    PRICING = {
        # model_prefix -> (input_cost_per_mtok, output_cost_per_mtok)
        "claude-opus": (5.0, 25.0),
        "claude-sonnet": (3.0, 15.0),
        "claude-haiku": (1.0, 5.0),
    }

    def __init__(self, db_path: Path):
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        self._lock = threading.Lock()

    def _init_schema(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS query_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                route_reason TEXT NOT NULL DEFAULT '',
                fallback_used INTEGER NOT NULL DEFAULT 0,
                query_hash TEXT NOT NULL DEFAULT ''
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_costs_ts ON query_costs(ts);
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_costs_model ON query_costs(model);
        """)
        self._db.commit()

    def log(self, model: str, input_tokens: int, output_tokens: int,
            cost_usd: float | None = None, route_reason: str = "",
            fallback_used: bool = False, query_hash: str = "") -> None:
        if cost_usd is None:
            cost_usd = self._calculate_cost(model, input_tokens, output_tokens)
        with self._lock:
            self._db.execute(
                "INSERT INTO query_costs (model, provider, input_tokens, output_tokens, "
                "cost_usd, route_reason, fallback_used, query_hash) VALUES (?,?,?,?,?,?,?,?)",
                (model, self._provider_for(model), input_tokens, output_tokens,
                 cost_usd, route_reason, int(fallback_used), query_hash),
            )
            self._db.commit()

    def summary(self, days: int = 30) -> dict:
        """Return cost summary grouped by model for the last N days."""
        rows = self._db.execute("""
            SELECT model, COUNT(*) as queries, SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output, SUM(cost_usd) as total_cost
            FROM query_costs
            WHERE ts >= datetime('now', ?)
            GROUP BY model
            ORDER BY total_cost DESC
        """, (f'-{days} days',)).fetchall()
        return {
            "period_days": days,
            "models": [
                {"model": r[0], "queries": r[1], "input_tokens": r[2],
                 "output_tokens": r[3], "cost_usd": round(r[4], 6)}
                for r in rows
            ],
            "total_cost_usd": round(sum(r[4] for r in rows), 6),
        }
```

### Anti-Patterns to Avoid
- **Hand-rolling HTTP to Anthropic API:** The SDK handles auth headers, retry logic, rate limiting, streaming, and error types. Using raw urllib/requests loses all of this.
- **Using LLM to classify intent:** Sending every query to a small LLM first adds 500ms+ latency and cost. Embedding similarity is near-instant.
- **Single monolithic gateway function:** A 200-line function that handles Anthropic, Ollama, error handling, cost tracking, and fallback. Use composition (ModelGateway delegates to CostTracker, IntentClassifier).
- **Storing costs in a separate database:** The project already has jarvis_memory.db with WAL mode and write serialization. Adding a table is cleaner than a second DB file.
- **Hardcoding model names:** Model names change frequently (claude-sonnet-4-5, claude-opus-4-5). Use a config/mapping that can be updated without code changes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Anthropic API HTTP client | urllib.request with auth headers, retry, etc. | `anthropic.Anthropic` SDK | Built-in retry (2x exponential backoff), typed errors (RateLimitError, APIConnectionError), streaming, token counting. Edge cases like 529 overloaded, retry-after headers. |
| Ollama client connection handling | Raw HTTP to `localhost:11434/api/generate` | `ollama.Client` | Proper error types (ResponseError with status_code, ConnectionError), model listing, async support. Current task_orchestrator.py already does this -- replace it. |
| Token cost calculation | Manual per-model pricing lookup | Pricing table as simple dict mapping model prefix to (input_rate, output_rate) | Prices are simple enough that a dict works. No need for a library. But DO NOT hardcode in the gateway -- keep pricing data separate so it can be updated. |

**Key insight:** The Anthropic SDK does heavy lifting that looks simple but isn't -- automatic retries with exponential backoff, proper Content-Type handling, streaming event parsing, error classification (rate limit vs auth vs server error). The ollama library similarly handles connection state, model existence checking, and streaming. Both are thin wrappers (~5KB each) with no heavy transitive dependencies.

## Common Pitfalls

### Pitfall 1: Blocking on Embedding Load for Every Request
**What goes wrong:** IntentClassifier calls `embed_service.embed()` which lazy-loads the sentence-transformers model on first use. If classifier is recreated per-request, model loads every time (10-15 seconds).
**Why it happens:** Following the current `_get_bus()` pattern creates a fresh bus per call.
**How to avoid:** The `EmbeddingService` singleton must be shared across the gateway. The existing `create_app()` in `app.py` already creates it once and passes it to handlers. Do the same for IntentClassifier -- create it once during app bootstrap and inject into the handler.
**Warning signs:** First query is slow (~15s), subsequent queries are fast.

### Pitfall 2: Anthropic API Key Not Set
**What goes wrong:** `Anthropic(api_key=None)` will raise `AuthenticationError` on every call. If the user hasn't configured their API key, every cloud-routed query fails.
**Why it happens:** ANTHROPIC_API_KEY env var not set, or config not loaded.
**How to avoid:** Check at gateway construction time. If no API key, the gateway should operate in local-only mode (all queries go to Ollama) and log a warning. Never raise an error at import time.
**Warning signs:** Every cloud query fails with 401 Unauthorized.

### Pitfall 3: Ollama Not Running
**What goes wrong:** `ollama.Client.chat()` raises `ConnectionError` when the Ollama server isn't running. If this is the fallback provider, ALL queries fail.
**Why it happens:** User hasn't started Ollama, or it crashed.
**How to avoid:** Check Ollama connectivity at startup (e.g., `ollama.list()` in a try/except). If unavailable, log warning but don't crash. Fallback response should include clear instructions: "Ollama is not running. Start it with `ollama serve`."
**Warning signs:** ConnectionError on every local query.

### Pitfall 4: Cost Tracking DB Connection Per-Request
**What goes wrong:** Opening a new `sqlite3.connect()` for every cost log entry is slow and can cause `database is locked` errors under concurrency.
**Why it happens:** Following a per-request pattern instead of connection pooling.
**How to avoid:** CostTracker should hold a persistent connection (like MemoryEngine does) with WAL mode and busy_timeout. Use the existing `_write_lock` threading.Lock pattern for write serialization.
**Warning signs:** Intermittent "database is locked" errors.

### Pitfall 5: Privacy Leak -- Sending Private Queries to Cloud
**What goes wrong:** A query like "what medications do I take" gets routed to Claude Opus because the classifier is uncertain.
**Why it happens:** Classifier defaults to "complex" when confidence is low, and complex routes to cloud.
**How to avoid:** Add an explicit privacy-keyword check BEFORE embedding classification. Keywords like "calendar", "medication", "bill", "password", "personal" should force local routing. If embedding similarity is ambiguous (all routes <0.65 cosine similarity), default to local not cloud.
**Warning signs:** Private data appearing in Anthropic API logs.

### Pitfall 6: Stale Model Names in Config
**What goes wrong:** Hardcoded model names like "claude-3-opus-20240229" stop working when Anthropic deprecates them.
**Why it happens:** Model identifiers change every few months.
**How to avoid:** Store model names in a config file (not hardcoded in code). Use the latest stable model identifiers. Current models: `claude-opus-4-5-20250929`, `claude-sonnet-4-5-20250929`. Keep pricing table keyed by prefix (e.g., "claude-opus" matches any opus version).
**Warning signs:** 404 errors from Anthropic API.

## Code Examples

### Anthropic SDK: Create Message and Get Token Usage
```python
# Source: Context7 /anthropics/anthropic-sdk-python
import os
from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

message = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello, Claude"}],
)
print(message.content[0].text)
print(f"Input tokens: {message.usage.input_tokens}")
print(f"Output tokens: {message.usage.output_tokens}")
```

### Anthropic SDK: Error Handling with Fallback
```python
# Source: Context7 /anthropics/anthropic-sdk-python + official docs
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

client = Anthropic()
try:
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello"}],
    )
except APIConnectionError:
    # Server unreachable -- fall back to local
    pass
except RateLimitError:
    # 429 -- SDK already retried 2 times with backoff
    pass
except APIStatusError as e:
    # Other HTTP error (401, 500, etc.)
    print(f"Status {e.status_code}: {e.message}")
```

### Ollama Python: Chat and Error Handling
```python
# Source: Context7 /ollama/ollama-python
from ollama import Client, ResponseError

client = Client(host="http://127.0.0.1:11434")

try:
    response = client.chat(
        model="qwen3:14b",
        messages=[{"role": "user", "content": "What's on my calendar?"}],
    )
    print(response.message.content)
except ResponseError as e:
    if e.status_code == 404:
        print(f"Model not found: {e.error}")
    else:
        print(f"Ollama error: {e.error}")
except ConnectionError:
    print("Ollama is not running. Start with: ollama serve")
```

### Ollama: List Available Models
```python
# Source: Context7 /ollama/ollama-python
from ollama import Client

client = Client()
models = client.list()
for model in models.models:
    print(f"{model.model} - size={model.size}")
```

### Existing Codebase: How Task Orchestrator Calls Ollama (Current Pattern to Migrate)
```python
# Source: engine/src/jarvis_engine/task_orchestrator.py lines 356-394
# Current approach uses raw urllib -- to be replaced by ollama.Client
from urllib.request import Request, urlopen
import json

payload = {"model": model, "prompt": prompt, "stream": False, "options": options}
req = Request(
    url=f"{endpoint.rstrip('/')}/api/generate",
    method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps(payload).encode("utf-8"),
)
with urlopen(req, timeout=timeout_s) as resp:
    data = json.loads(resp.read().decode("utf-8"))
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Claude 3 models (claude-3-opus-20240229) | Claude 4.5/4.6 models (claude-opus-4-5-20250929) | Late 2025 | 67% cost reduction for Opus. New model IDs. |
| Raw HTTP to Anthropic API | anthropic Python SDK >=0.81.0 | Ongoing | Built-in retry, typed errors, streaming, token counting |
| Manual Ollama HTTP (urllib) | ollama Python library | 2024 | Proper error types, async support, model management |
| LLM-based query classification | Embedding-based semantic routing | 2025 | Near-instant classification (vs 500ms+ LLM call), no per-query cost |
| Single model for all queries | Tiered routing (Opus/Sonnet/local) | 2025 | 50-70% cost reduction in production systems |

**Deprecated/outdated:**
- Claude 3 model IDs (claude-3-opus-20240229, claude-3-sonnet-20240229): Replaced by Claude 4.5+ series. Use claude-opus-4-5-20250929 and claude-sonnet-4-5-20250929.
- anthropic SDK <0.40: Major API changes around messages vs completions. Use >=0.81.0.

## Open Questions

1. **Which Ollama model(s) to use as default local model?**
   - What we know: Project currently uses `qwen3:14b` and `qwen3-coder:30b` (in task_orchestrator.py). DEFAULT_FALLBACK_MODELS includes `qwen3:14b`, `qwen3:latest`, `deepseek-r1:8b`.
   - What's unclear: Whether the user's hardware can handle 14b+ parameter models for fast response times. Smaller models (e.g., qwen3:8b, phi-3:mini) might be better for simple private queries.
   - Recommendation: Default to `qwen3:14b` for general use (consistent with current codebase), make it configurable via env var `JARVIS_LOCAL_MODEL`. Add model availability check at startup.

2. **Should the CostTracker share the same DB connection as MemoryEngine?**
   - What we know: MemoryEngine uses a persistent sqlite3 connection with WAL mode and a write_lock. CostTracker also needs WAL and write serialization.
   - What's unclear: Whether a second connection to the same DB file could cause lock contention, or whether CostTracker should add its table to MemoryEngine's schema.
   - Recommendation: Add the `query_costs` table to the existing `jarvis_memory.db` schema in `MemoryEngine._init_schema()`. CostTracker gets a reference to the MemoryEngine and uses its connection + write_lock. This avoids a second connection and leverages the existing concurrency pattern.

3. **How to handle the transition from old router.py to new gateway?**
   - What we know: `router.py` (33 lines) is used by `RouteHandler` in `task_handlers.py`. The existing `RouteCommand` takes `risk` and `complexity` enums.
   - What's unclear: Whether any external consumers depend on the old route command interface.
   - Recommendation: Evolve `RouteCommand` to accept an optional `query` text field. When query is provided, use IntentClassifier. When only risk/complexity are provided, use the old logic (backward compat). Eventually deprecate the old risk/complexity path.

4. **Pre-compute route embeddings at startup vs lazy?**
   - What we know: Pre-computing takes ~100ms once EmbeddingService is loaded. Lazy loading delays first classification by ~100ms.
   - What's unclear: Whether the 100ms at startup matters.
   - Recommendation: Pre-compute during `create_app()` bootstrap. The EmbeddingService is already initialized there. 100ms is negligible during startup.

## Sources

### Primary (HIGH confidence)
- Context7 `/anthropics/anthropic-sdk-python` -- SDK API, message creation, token counting, error types, streaming
- Context7 `/ollama/ollama-python` -- Client API, chat/generate, error handling (ResponseError, ConnectionError), model listing
- Anthropic official pricing page (https://platform.claude.com/docs/en/about-claude/pricing) -- Current model pricing: Opus $5/$25, Sonnet $3/$15 per MTok

### Secondary (MEDIUM confidence)
- Anthropic SDK GitHub README (https://github.com/anthropics/anthropic-sdk-python) -- Retry behavior (2x automatic, exponential backoff), timeout defaults (10min), error hierarchy
- Ollama Python GitHub README (https://github.com/ollama/ollama-python) -- Error handling patterns, streaming API, async support
- WebSearch on LLM routing patterns -- Tiered routing achieves 50-70% cost reduction, embedding-based classification matches LLM-based at ~90% accuracy

### Tertiary (LOW confidence)
- Model version identifiers (claude-opus-4-5-20250929, claude-sonnet-4-5-20250929) -- These are the latest as of Feb 2026 but Anthropic may release new model IDs at any time. Keep configurable.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - anthropic SDK and ollama library are official, well-documented, verified via Context7
- Architecture: HIGH - Gateway pattern, embedding classification, and SQLite cost tracking are proven patterns. Codebase already uses all building blocks (CommandBus, EmbeddingService, SQLite, handlers).
- Pitfalls: HIGH - Based on direct codebase analysis (existing patterns in task_orchestrator.py, memory/engine.py) and SDK documentation for error types

**Research date:** 2026-02-22
**Valid until:** 2026-03-22 (30 days -- stable domain, model IDs may change)
