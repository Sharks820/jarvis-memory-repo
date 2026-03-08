# Security & Optimization Audit — March 2026

## Scope
- Task A: Cross-LLM Context Continuity (`conversation_state.py`)
- Task C: Voice Pipeline Telemetry (`voice_telemetry.py`, `voice_pipeline.py`)
- Full codebase optimization scan (daemon, memory, gateway, mobile API)

---

## SECURITY FINDINGS

### S1 — HIGH: Conversation State Persisted as Plaintext JSON (No Encryption at Rest)

**Location:** `engine/src/jarvis_engine/conversation_state.py` lines 887–901 (`save()` method)

**Description:** `ConversationStateManager.save()` writes `conversation_state.json` to `.planning/runtime/` as unencrypted JSON using `json.dumps()` + `os.replace()`. The file contains:
- Rolling conversation summaries (may include financial amounts, health data, personal names)
- Anchor entities (names, dates, dollar amounts, file paths, URLs)
- Prior decisions and unresolved goals
- Full model switch history

The `conversation_timeline.db` SQLite database is also unencrypted and stores `summary_snippet` (first 200 chars of every conversation turn) plus `entities_extracted`.

**Impact:** If the machine is compromised or the filesystem is accessed, all conversation history is readable in plaintext. The sync subsystem uses Fernet encryption for transport, but the at-rest conversation state does not.

**Recommended Fix:** Encrypt `conversation_state.json` and `conversation_timeline.db` using the same Fernet/PBKDF2 approach used by `sync/transport.py`. Derive key from the signing key via `derive_fernet_key()`. Alternatively, use SQLCipher for the timeline DB (like the Android app does for Room).

---

### S2 — HIGH: No PII Filtering on anchor_entities Before Storage

**Location:** `engine/src/jarvis_engine/conversation_state.py`, `extract_entities()` (lines 359–436) and `update_turn()` (lines 619–690)

**Description:** The entity extraction pipeline captures:
- Dollar amounts (`_RE_AMOUNT` — `$50,000`, etc.)
- Dates including slash-format (`1/15/1990` — could be birthdate)
- People names with prefixes (`Dr. John Smith`)
- URLs (may contain tokens, session IDs, private paths)
- File paths (may expose directory structure)

There are **no PII filters** — no phone number regex, no SSN pattern, no credit card pattern, no email pattern. If a user mentions "My SSN is 123-45-6789" or "call me at 555-123-4567", these would be stored in `anchor_entities` as capitalized sequences or amount patterns, persisted to plaintext JSON, and exposed via `GET /conversation/state`.

**Impact:** Sensitive PII stored persistently in plaintext, exposed through the API endpoint.

**Recommended Fix:** Add PII detection regex patterns (phone numbers, SSNs, email addresses, credit card numbers) and either:
1. Exclude matches from `anchor_entities`, or
2. Mask them before storage (e.g., `***-**-6789`, `***-***-4567`)

---

### S3 — MEDIUM: GET /conversation/state Returns Raw Conversation Content in summary_snippet

**Location:** `engine/src/jarvis_engine/mobile_routes/intelligence.py` line 203–211 (`_handle_get_conversation_state`)

**Description:** The endpoint returns `recent_timeline` which includes `summary_snippet` — the first 200 characters of every conversation turn, unredacted. Combined with `anchor_entities` (full names, amounts, dates) and `rolling_summary`, this endpoint effectively leaks substantial conversation content.

The endpoint IS HMAC-protected (`self._validate_auth(b"")` check), which is good. However, the data richness is excessive for a dashboard endpoint.

**Impact:** A compromised mobile device (which has the signing key) gets raw conversation snippets, entity lists, and decision history.

**Recommended Fix:** Redact or truncate `summary_snippet` to remove PII before sending. Consider a `?detail=full` flag that requires master password for full content.

---

### S4 — MEDIUM: Conversation State JSON Replay Buffer On Disk (Not Memory)

**Location:** `engine/src/jarvis_engine/conversation_state.py` lines 36–39, 887–901

**Description:** The "30-turn replay buffer" (`ConversationTimeline`) is backed by SQLite on disk (`conversation_timeline.db`), not in-memory. Every turn is persisted immediately to disk via `self._db.commit()` (line 250). The rolling summary (up to 2000 chars) and all anchor entities are also written to `conversation_state.json` after every turn.

This is more of a design concern than a vulnerability — it means conversation content accumulates on disk without bounds (the timeline table grows forever with no pruning).

**Impact:** Unbounded growth of conversation data on disk. Combined with S1 (no encryption), this creates a growing plaintext record of all conversations.

**Recommended Fix:** Add a retention policy — prune timeline entries older than N days (e.g., 30 days). Add encryption per S1.

---

### S5 — MEDIUM: Entity Extraction Not Hardened Against Prompt Injection

**Location:** `engine/src/jarvis_engine/conversation_state.py`, `extract_entities()` line 359

**Description:** If a malicious LLM response (from a compromised provider) contains crafted text like:
```
Dr. Robert'); DROP TABLE conversation_timeline;--
```
The entity extraction uses regex only and stores the result as a set member (not SQL). The entities are serialized to JSON (safe). However, the `summary_snippet` (first 200 chars of content) is stored directly in SQLite via parameterized queries (line 244–252), which IS safe against SQL injection.

The more realistic concern is **context poisoning**: a malicious provider could inject false entities, fake decisions, or fake unresolved goals that persist across provider switches and contaminate future conversations.

**Impact:** A compromised provider could inject persistent false context that propagates to all future LLM interactions.

**Recommended Fix:** Add a confidence/provenance field to entities and decisions. Tag each with the source provider. Consider a "quarantine" flag for entities from providers with low trust scores (integrate with existing `memory_provenance.py` trust levels).

---

### S6 — LOW: GET /voice/latency Properly Authenticated

**Location:** `engine/src/jarvis_engine/mobile_routes/intelligence.py` line 396

**Description:** `_handle_get_voice_latency` calls `self._validate_auth(b"")` — it **does** require HMAC authentication. This is correct.

**Finding: PASS** — No vulnerability here.

---

### S7 — LOW: Voice State Transitions Cannot Be Externally Spoofed

**Location:** `engine/src/jarvis_engine/voice_telemetry.py`

**Description:** `VoiceTelemetry` is an in-memory singleton. Stage transitions are set via `mark_stage()` which is called from the voice pipeline code internally. There is no HTTP endpoint to set stage transitions externally. The `emit_stage_transition()` method only writes to the activity feed (read-only from external perspective).

**Finding: PASS** — State transitions are internal-only.

---

### S8 — LOW: Audio Buffers Not Explicitly Zeroed After Processing

**Location:** `engine/src/jarvis_engine/stt_backends.py` lines 280–340

**Description:** `_capture_audio_loop()` stores audio as `numpy.ndarray` frames in a local list variable. When the function returns, the list goes out of scope and is garbage collected. However:
1. The numpy arrays are not explicitly zeroed (`np.zeros_like()` or `del`)
2. Python's garbage collector doesn't guarantee immediate memory zeroing
3. The OS may retain the audio data in freed memory pages

For a personal assistant on a single-user machine, this is LOW severity. It would be MEDIUM+ in a multi-tenant or server context.

**Recommended Fix:** After recording completes and transcription is done, explicitly zero the audio buffers: `for frame in frames: frame[:] = 0`. Also zero the concatenated audio array after STT processing.

---

### S9 — LOW: SLO Alert System Not a Practical DoS Vector

**Location:** `engine/src/jarvis_engine/voice_telemetry.py` lines 350–380

**Description:** SLO violations are tracked with a `_consecutive_slo_breaches` counter that requires 10 consecutive breaches before emitting an alert. The alert resets the counter. The `_slo_violations` list is capped at 200 entries. All telemetry is internal (no external HTTP trigger).

An attacker would need to control the voice pipeline's actual latency (which requires being on the local machine), making this not a practical DoS vector.

**Finding: PASS** — Properly bounded and internally-only triggered.

---

## OPTIMIZATION FINDINGS

### O1 — HIGH Impact: Conversation Timeline Table Missing Pruning (Unbounded Growth)

**Location:** `engine/src/jarvis_engine/conversation_state.py`, `ConversationTimeline`

**Description:** The `conversation_timeline` SQLite table grows indefinitely — every conversation turn is appended with no retention policy. For an active assistant, this could reach 10,000+ rows within a few months.

**Estimated Improvement:** Prevent unbounded storage growth; keep timeline queries fast.
**Implementation Complexity:** LOW — add a `prune(max_age_days=30)` method called from the daemon loop.

---

### O2 — HIGH Impact: Daemon Loop VACUUM Frequency Appropriate, But ANALYZE Could Run More Often

**Location:** `engine/src/jarvis_engine/daemon_loop.py` lines 533–548

**Description:** VACUUM runs every 500 cycles (~25 hours at 180s intervals) and ANALYZE every 100 cycles (~5 hours). These are reasonable. However, WAL checkpoint is only done inside `MemoryEngine.wal_checkpoint()` and is never called from the daemon loop.

**Estimated Improvement:** Prevent WAL file growth; maintain consistent read performance.
**Implementation Complexity:** LOW — add `engine.wal_checkpoint()` call every 50 daemon cycles.

---

### O3 — HIGH Impact: Embedding Model Lazy-Load Cold Start (~5-10 seconds)

**Location:** `engine/src/jarvis_engine/memory/embeddings.py` `_ensure_model()`

**Description:** `SentenceTransformer("nomic-ai/nomic-embed-text-v1.5")` loads on first `embed()` call. This blocks the first search/ingest for 5-10 seconds. The daemon pre-warms the CommandBus but doesn't explicitly pre-warm the embedding model.

**Estimated Improvement:** Eliminate first-query cold start latency.
**Implementation Complexity:** LOW — call `bus.ctx.embed_service.embed("warmup")` in `_start_bus_prewarm()`.

---

### O4 — MEDIUM Impact: Gateway httpx Client Connection Pooling Already Good

**Location:** `engine/src/jarvis_engine/gateway/models.py` line 157

**Description:** `self._http = httpx.Client(timeout=60.0)` — shared httpx client with default connection pooling. This is already optimal. The client is shared across all cloud provider calls.

**Finding: ALREADY OPTIMIZED** — Good pattern.

---

### O5 — MEDIUM Impact: Hybrid Search Debounced Access Updates — Good Pattern

**Location:** `engine/src/jarvis_engine/memory/search.py` lines 60–80

**Description:** Access count updates are batched (100 IDs or 10 seconds) to avoid a DB write per search. This is a well-implemented optimization.

**Finding: ALREADY OPTIMIZED** — Good pattern.

---

### O6 — MEDIUM Impact: FTS5 Search Missing Prefix Index for Autocomplete

**Location:** `engine/src/jarvis_engine/memory/engine.py` FTS5 table definition

**Description:** The FTS5 table uses the default tokenizer. For voice queries which are often partial/abbreviated, a `prefix='2,3'` configuration on the FTS5 table could improve recall for short queries.

**Estimated Improvement:** Better search recall for short/partial queries.
**Implementation Complexity:** MEDIUM — requires FTS5 table recreation with `prefix` option in a schema migration.

---

### O7 — MEDIUM Impact: Embedding Cache Size Could Be Larger

**Location:** `engine/src/jarvis_engine/memory/embeddings.py` line 28

**Description:** `_CACHE_MAXSIZE = 1024` — the embedding cache stores 1024 entries. Each entry is ~3KB (768 floats × 4 bytes). Total cache: ~3MB. For a machine with 32GB RAM, this could easily be 4096 or 8192 without issue, improving hit rates for repeated queries.

**Estimated Improvement:** 2-4x cache capacity = fewer model.encode() calls.
**Implementation Complexity:** LOW — change constant or make it configurable via env var.

---

### O8 — MEDIUM Impact: ConversationStateManager Saves After Every Single Turn

**Location:** `engine/src/jarvis_engine/conversation_state.py` line 690 (`self.save()`)

**Description:** `update_turn()` calls `self.save()` which does `json.dumps()` + file write + `os.replace()` on EVERY conversation turn. For a rapid back-and-forth conversation, this means 2 disk writes per exchange (user + assistant).

The `ConversationState` in `voice_pipeline.py` (line 66-68) already implements a save debounce (`_SAVE_DEBOUNCE_SECONDS = 5.0`), but `ConversationStateManager` does NOT debounce.

**Estimated Improvement:** Reduce disk I/O by 90% during active conversations.
**Implementation Complexity:** LOW — add debounce similar to `ConversationState._SAVE_DEBOUNCE_SECONDS`.

---

### O9 — MEDIUM Impact: CLI Provider Refresh Check on Every LLM Call

**Location:** `engine/src/jarvis_engine/gateway/models.py` `_refresh_cli_providers()` called from `_resolve_provider()`

**Description:** `_refresh_cli_providers()` runs `detect_cli_providers()` (which spawns subprocesses like `which claude`, `which codex`) on every LLM call if the refresh interval (5s default) has elapsed. While cached, the check itself involves `time.monotonic()` comparison on every single completion.

The current 5-second refresh is reasonable but could be extended to 30-60 seconds since CLI provider availability rarely changes.

**Estimated Improvement:** Fewer subprocess spawns; marginally faster routing.
**Implementation Complexity:** LOW — change default interval to 30s.

---

### O10 — LOW Impact: Missing `__slots__` on High-Volume Dataclasses

**Location:** Multiple files

**Description:** `ConversationSnapshot`, `TimelineEntry`, `ActivityEvent`, and `GatewayResponse` are dataclasses without `__slots__`. The voice telemetry `_UtteranceRecord` correctly uses `__slots__`. For high-volume objects (timeline entries, activity events), `__slots__` saves ~40 bytes per instance.

**Estimated Improvement:** Minor memory savings (~40 bytes × instance count).
**Implementation Complexity:** LOW — add `@dataclass(slots=True)` (Python 3.10+).

---

### O11 — LOW Impact: Gateway Responses Not Cached

**Location:** `engine/src/jarvis_engine/gateway/models.py`

**Description:** LLM responses are not cached. This is **intentionally correct** — caching LLM responses would cause stale answers for different contexts even with the same query text. The embedding cache (O7) handles the cacheable part of the pipeline.

**Finding: CORRECT DESIGN** — No caching needed for LLM completions.

---

### O12 — LOW Impact: Token Counting Relies on Provider-Reported Usage

**Location:** `engine/src/jarvis_engine/gateway/models.py` — `input_tokens`/`output_tokens` from API response

**Description:** Token counts come from the API response (`usage.prompt_tokens`, `usage.completion_tokens`). This is accurate for billing purposes. There's no pre-flight token estimation to avoid wasted context budget, but adding one would require a tokenizer dependency and add latency.

**Finding: ACCEPTABLE** — Provider-reported tokens are authoritative. Pre-flight estimation is a future optimization.

---

### O13 — LOW Impact: Mobile API Gzip Compression Already Implemented

**Location:** `engine/src/jarvis_engine/mobile_api.py` `_write_json()` method

**Description:** Responses >256 bytes are gzip-compressed when the client sends `Accept-Encoding: gzip`. This is already optimized.

**Finding: ALREADY OPTIMIZED**

---

## QUICK WINS (High Impact, Low Effort)

| # | Finding | Impact | Effort | Description |
|---|---------|--------|--------|-------------|
| 1 | O8 | HIGH | LOW | Add save debounce to ConversationStateManager (copy pattern from voice_pipeline.ConversationState) |
| 2 | O1 | HIGH | LOW | Add timeline pruning (DELETE WHERE timestamp < cutoff) called from daemon |
| 3 | O3 | HIGH | LOW | Pre-warm embedding model in `_start_bus_prewarm()` |
| 4 | S2 | HIGH | LOW | Add PII regex filters (phone, SSN, email, CC) to entity extraction |
| 5 | O7 | MED | LOW | Increase embedding cache from 1024 to 4096 |
| 6 | O9 | MED | LOW | Increase CLI provider refresh interval from 5s to 30s |

---

## Summary

**Security:** 5 actionable findings (2 HIGH, 2 MEDIUM, 1 LOW). The two HIGH findings (plaintext conversation state, no PII filtering) should be addressed before any public-facing deployment. Both endpoints (`/conversation/state`, `/voice/latency`) are properly HMAC-authenticated. Voice state transitions are internal-only. SLO alerting is properly bounded.

**Optimization:** 6 actionable findings (3 HIGH, 3 MEDIUM). Several good patterns already in place (hybrid search batching, access debounce, httpx pooling, gzip compression). The highest-impact quick wins are save debounce, timeline pruning, and embedding pre-warming.
