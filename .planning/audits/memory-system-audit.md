# Memory System Audit

**Date:** 2026-03-07  
**Scope:** `engine/src/jarvis_engine/memory/` + `brain_memory.py`, `memory_store.py`, `auto_ingest.py`, `memory_snapshots.py`  
**Auditor:** Automated deep scan

---

## Architecture Overview

Jarvis memory is a **dual-path** system:

1. **SQLite path** (`memory/engine.py`): The modern backend — SQLite + FTS5 full-text + sqlite-vec KNN, WAL mode, 64 MB cache, 256 MB mmap. Schema: `records` (14 columns, 6 indexes), `fts_records` (FTS5 virtual table), `vec_records` (vec0 virtual table, 768-dim float), `facts` table, `schema_version` table.

2. **Legacy JSONL path** (`brain_memory.py`): File-based records.jsonl + index.json + facts.json. Still actively used by `auto_ingest.py` — every memory is dual-written to both paths.

**Ingestion pipeline:** `auto_ingest.py` → `ingest.py` (old MemoryStore append) + `brain_memory.py` (JSONL) in parallel. The enriched pipeline (`memory/ingest.py`) feeds the SQLite path with chunking + embeddings + classification.

**Search:** Hybrid RRF (Reciprocal Rank Fusion) combining FTS5 rank + sqlite-vec cosine distance, plus recency decay (168h half-life) and frequency boost (log1p access_count). Context packets for LLM prompts built via keyword token overlap in `brain_memory.py`.

**Tiers:** Three-tier hierarchy (hot/warm/cold/archive) based on recency (<48h=hot), access count (≥3=warm), confidence (≥0.85=warm), age (>90d=cold).

---

## Per-Component Quality Scores

| Component | File(s) | Score | Rationale |
|---|---|---|---|
| Storage Engine | `memory/engine.py` | **82** | Solid schema, WAL+ACID, content-hash dedup, batch ops. Missing: partial-update, full-text column weights, compound indexes. |
| Hybrid Search | `memory/search.py` | **75** | RRF fusion is sound, recency+frequency boosts present. Missing: weight tuning, tier filtering, branch filtering, score normalization, MMR diversity. |
| Embeddings | `memory/embeddings.py` | **78** | Lazy load, thread-safe LRU cache (1024), batch inference. Missing: GPU/ONNX acceleration, cache persistence, warm-up, adaptive model selection. |
| Ingest Pipeline | `memory/ingest.py` | **72** | Chunking, sanitization, credential redaction, fact extraction. Missing: overlap between chunks, dedup across chunks, importance scoring at ingest. |
| Classification | `memory/classify.py` | **65** | Cosine-to-centroids approach is clean. Missing: multi-label, dynamic branch creation, confidence calibration, user-feedback loop. |
| Tier Management | `memory/tiers.py` | **70** | Simple rules, batch update. Missing: importance rescoring, decay curves, archive eviction, promotion from cold on access. |
| Context Builder | `brain_memory.py` | **55** | Legacy keyword-overlap scorer, no embeddings, no token budget enforcement, 3-per-branch cap is rigid, 2400 char hard limit. |
| Auto-Ingest | `auto_ingest.py` | **68** | Fire-and-forget background thread, dedup hash file. Missing: queue backpressure, retry with backoff, enriched pipeline integration. |
| Memory Store | `memory_store.py` | **60** | Simple JSONL append + reverse-seek tail. Adequate for event log, not for primary memory. |
| Migration | `memory/migration.py` | **80** | Resumable checkpoints, count verification, batch processing. Solid migration tooling. |
| Snapshots | `memory_snapshots.py` | **77** | HMAC-signed zip snapshots, KG metrics capture, maintenance orchestration. Good operational tooling. |

**Weighted System Score: 71/100**

---

## Top 10 Findings (Ranked by Impact)

### 1. Dual-Write Without Unified Search (CRITICAL)
**Files:** `auto_ingest.py:82-96`, `brain_memory.py:build_context_packet`  
**Impact:** High — context packets for LLM prompts use the legacy JSONL keyword-overlap scorer, NOT the modern hybrid search. Memories ingested via the enriched pipeline are invisible to `build_context_packet`.  
**Fix:** Route `build_context_packet` through `hybrid_search()` from `memory/search.py`. Deprecate the JSONL-based context builder. This is the single highest-impact change.

### 2. Context Builder Uses Token-Overlap, Not Semantic Search (HIGH)
**File:** `brain_memory.py:265-310` (`build_context_packet`)  
**Impact:** High — the LLM prompt context is selected by counting shared word tokens between query and summary. This misses synonyms, paraphrases, and semantic relationships. A query about "medication schedule" won't match a memory about "prescription timing".  
**Fix:** Replace `_tokenize`-based scoring with `hybrid_search()` or at minimum embed the query and use cosine similarity against stored embeddings.

### 3. No Token Budget Management in Context Building (HIGH)
**File:** `brain_memory.py:287` (`max_chars=2400`)  
**Impact:** Medium-High — hard-coded 2400 character limit with no awareness of model token limits. No tiktoken counting means context may be truncated mid-sentence or leave tokens unused. Different models have different windows.  
**Fix:** Add token counting (tiktoken or similar), parameterize budget by model, implement priority-based truncation that respects sentence boundaries.

### 4. FTS5 Has No Column Weights or Tokenizer Config (MEDIUM)
**File:** `memory/engine.py:89-90` — `USING fts5(record_id, summary)`  
**Impact:** Medium — FTS5 indexes only `summary` and `record_id` (which is a hash, useless for search). No `tags`, `source`, `kind`, or `branch` columns in FTS5. No custom tokenizer (porter stemming, unicode61) configured. Default BM25 with no column weight boosting.  
**Fix:** Expand FTS5 to: `USING fts5(summary, tags, source, kind, tokenize='unicode61 remove_diacritics 2')`. Add column weights: `rank = bm25(fts_records, 10.0, 2.0, 1.0, 1.0)`.

### 5. RRF Weights Are Untuned — Equal FTS5 and Vector Weight (MEDIUM)
**File:** `memory/search.py:100-105`  
**Impact:** Medium — FTS5 and vector search contribute equally to RRF scores (`1/(rrf_k + rank + 1)` with same formula). For personal memory, semantic similarity should likely outweigh keyword matching. No A/B testing or relevance feedback mechanism.  
**Fix:** Add configurable weights: `fts_weight * 1/(rrf_k+rank+1)` and `vec_weight * 1/(rrf_k+rank+1)`. Default to 0.4 FTS / 0.6 vector. Add a relevance feedback log to tune over time.

### 6. Chunk Splitting Has No Overlap (MEDIUM)
**File:** `memory/ingest.py:173-210` (`_chunk_content`)  
**Impact:** Medium — chunks are split at sentence boundaries with zero overlap. Information spanning a chunk boundary will lose context in both chunks. A sentence like "The meeting with Dr. Smith about my prescription..." could be split from its context.  
**Fix:** Add a configurable overlap (e.g., 200 chars or 2 sentences) between consecutive chunks. Standard practice is 10-20% overlap.

### 7. Branch Classification Is Static Single-Label (MEDIUM)
**File:** `memory/classify.py:19-29` (`BRANCH_DESCRIPTIONS`)  
**Impact:** Medium — only 9 hardcoded branches with fixed descriptions. No multi-label support (a memory about "coding a health app" gets one label). No mechanism to add new branches or learn from user corrections. Threshold of 0.3 cosine similarity is arbitrary.  
**Fix:** Support multi-label classification (top-2 branches above threshold). Allow user-defined branches via config. Add a feedback mechanism to refine centroids over time.

### 8. No Importance Scoring at Ingest Time (MEDIUM)
**File:** `memory/ingest.py:130` — hardcoded `"confidence": 0.72`  
**Impact:** Medium — every memory gets the same initial confidence (0.72) regardless of content significance. A critical medical instruction and a casual chat observation are treated identically. No LLM-based importance assessment.  
**Fix:** Add an importance scorer at ingest time. Can be rule-based (medical/financial/security keywords → boost) or LLM-based (ask the model "rate importance 0-1"). Use this to set initial confidence and tier.

### 9. No Cold-Tier Promotion on Access (LOW-MEDIUM)
**File:** `memory/tiers.py:52-72` (`classify`)  
**Impact:** Low-Medium — tier maintenance reclassifies based on static rules, but there's no mechanism to immediately promote a cold record back to warm when it's accessed via search. The `update_access` call in search doesn't trigger re-tiering.  
**Fix:** In `hybrid_search`, after returning cold-tier results, enqueue a tier re-evaluation for those records. Or add a trigger: if `access_count` crosses threshold in `update_access`, auto-promote.

### 10. Embedding Cache Not Persisted Across Restarts (LOW)
**File:** `memory/embeddings.py:25-30` (`_CACHE_MAXSIZE = 1024`)  
**Impact:** Low — the 1024-entry LRU cache is in-memory only. Every engine restart (daemon restart, crash recovery) cold-starts embeddings. For a personal assistant that restarts daily, this wastes ~1-2 min of re-embedding common queries.  
**Fix:** Add optional disk-backed cache (SQLite table or shelve file) for the embedding cache. Warm up on startup with most-accessed record summaries.

---

## Missing Features (vs. mem0 / Zep / Letta)

| Feature | mem0 | Zep | Letta | Jarvis | Gap |
|---|---|---|---|---|---|
| Graph memory (entity relationships) | ✅ | ✅ (Graphiti) | ❌ | ⚠️ KG exists separately | KG not integrated into memory retrieval |
| Temporal reasoning (time-aware queries) | ✅ | ✅ (temporal KG) | ❌ | ⚠️ recency decay only | No "what changed since Tuesday" queries |
| Automatic fact extraction from conversations | ✅ | ✅ | ✅ | ⚠️ regex + basic LLM | No structured entity/relation extraction pipeline |
| Memory consolidation (merge similar memories) | ✅ | ✅ | ❌ | ❌ | No dedup-merge of semantically similar records |
| Self-editing memory (agent modifies own memory) | ❌ | ❌ | ✅ | ❌ | Agent can't update/delete/correct memories |
| Multi-level memory (user/session/agent) | ✅ | ✅ | ✅ (core/archival/recall) | ⚠️ tiers only | No session-scoped vs. permanent distinction |
| Conflict resolution (contradicting facts) | ⚠️ | ✅ | ❌ | ⚠️ basic in brain_memory | No automated resolution; conflicts accumulate |
| Memory importance scoring | ❌ | ✅ | ❌ | ❌ | All records get confidence=0.72 |
| Cross-session context continuity | ✅ | ✅ | ✅ | ⚠️ via JSONL | No explicit session boundaries or handoff |
| Forgetting / decay / eviction | ❌ | ✅ | ✅ | ⚠️ cold tier exists | Cold records never deleted or archived |
| MMR / diversity in retrieval | ❌ | ❌ | ❌ | ❌ | Results can be redundant |
| Hybrid graph + vector retrieval | ✅ | ✅ (Graphiti) | ❌ | ❌ | KG search is separate from memory search |

---

## Quick Wins (High Impact, Low Effort)

1. **Route context building through hybrid_search** (~2h): In `build_context_packet`, call `hybrid_search()` instead of keyword-overlap scoring. Immediate quality boost for every LLM prompt.

2. **Add FTS5 tokenizer config** (~30min): Change FTS5 creation to `USING fts5(summary, tags, tokenize='unicode61 remove_diacritics 2')`. Requires schema migration.

3. **Add tier filter to hybrid_search** (~1h): Add optional `exclude_tiers=["cold","archive"]` parameter. Skip cold records in default search, saving compute.

4. **Weighted RRF** (~30min): Add `fts_weight=0.4, vec_weight=0.6` params to `hybrid_search()`. Single line change per score computation.

5. **Chunk overlap** (~1h): Add 200-char overlap in `_chunk_content()`. Prevents context loss at chunk boundaries.

6. **Cold-on-access promotion** (~30min): After `update_access_batch` in search, check if any returned records are cold-tier and enqueue a tier bump.

---

## Upgrade Roadmap (Phased)

### Phase 1: Unify Search Path (1-2 days)
- Rewrite `build_context_packet` to use `hybrid_search()` from `memory/search.py`
- Add token-budget-aware truncation (tiktoken)
- Add tier filtering to `hybrid_search`
- Deprecate JSONL keyword-overlap scorer

### Phase 2: Search Quality (2-3 days)
- Expand FTS5 schema (tags, source, custom tokenizer)
- Implement weighted RRF with configurable FTS/vector balance
- Add MMR (Maximal Marginal Relevance) diversity re-ranking
- Add chunk overlap in ingest pipeline
- Implement cold-on-access tier promotion

### Phase 3: Intelligence Layer (3-5 days)
- Add importance scoring at ingest (rule-based + optional LLM)
- Implement memory consolidation (merge semantically similar records)
- Add multi-label branch classification
- Integrate KG relationships into memory retrieval (graph-boosted search)
- Add temporal query support ("what happened last week")

### Phase 4: Agent Memory Features (5-7 days)
- Self-editing memory: let the LLM update/correct/delete memories via tool calls
- Session-scoped vs. permanent memory distinction
- Automated conflict resolution for contradicting facts
- Memory provenance tracking (which conversation created this memory)
- Forgetting policy: archive/evict cold records after configurable TTL

### Phase 5: Performance & Observability (2-3 days)
- Persist embedding cache to disk with warm-up
- Add search relevance logging and A/B framework for weight tuning
- ONNX runtime for embedding inference (~3x speedup)
- Add memory health dashboard metrics (cache hit rate, search latency p50/p99, tier distribution)
