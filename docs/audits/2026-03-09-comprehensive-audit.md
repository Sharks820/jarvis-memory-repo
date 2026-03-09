# Comprehensive Audit & Bug Scan Report

**Date:** 2026-03-09
**Scope:** Full codebase — 189 Python modules, 134 Kotlin files, 167 test files
**Baseline:** v5.0 Phase 1 at claimed 55% completion
**Test baseline:** 5077 passing, 4 skipped, 0 failures (as of 2026-03-07)

---

## Part 1: Is 55% Accurate?

### Verdict: 55% is GENEROUS — True completion is closer to 35-40%

The 55% figure reflects Phase 1 of v5.0 only. When measured against the full v5.0
roadmap (50 requirements across 6 phases), the picture changes significantly.

### v5.0 Phase-by-Phase Reality Check

| Phase | Status | Requirements | Actually Done | Notes |
|-------|--------|-------------|---------------|-------|
| Phase 1: Reliability Core | In Progress | REL-01..08, PERF-01..04 (12) | ~6 of 12 | 14-01 tasks A-D complete, 14-02 tasks A-H only partially implemented |
| Phase 2: Context Continuity | Not Started | CTX-01..06 (6) | 0 of 6 | `conversation_state.py` exists (1503 lines) but no integration with actual provider switching |
| Phase 3: Learning Missions | Not Started | LM-01..08, OBS-01..04 (12) | 0 of 12 | MissionStep exists but pause/resume/restart/schedule endpoints missing |
| Phase 4: Voice Accuracy | Not Started | STT-09..14 (6) | 0 of 6 | `voice_telemetry.py` exists but no real-room benchmark or correction loop |
| Phase 5: Mobile Tasking | Not Started | MOB-06..12 (7) | 0 of 7 | No async task delivery pipeline |
| Phase 6: Security Expansion | Not Started | SEC-01..06 (6) | 0 of 6 | Security module is strong from v3.0 but v5.0 requirements not addressed |

**Requirements completed: ~6 of 50 = 12% of v5.0**
**Phase 1 progress: ~50% of Phase 1 (6 of 12 requirements substantively addressed)**

### What the 55% Actually Means

The 55% in STATE.md refers to Phase 1 progress only, not overall v5.0. This is
reasonable as a Phase 1 metric, but the true overall v5.0 progress is ~12%.
Additionally, within Phase 1:

- **14-01 Plan (Reliability Baseline):** Steps 1-3 COMPLETE, Step 4 (debt gate) IN PROGRESS
- **14-02 Plan (Continuity + Voice + Missions):** Design doc written, but only partial implementation:
  - Task A (Context Continuity): Module written, tests written (771 lines), but NOT integrated into actual provider switching
  - Task B (Date Grounding): Partially done (temporal.py, voice_context.py exist)
  - Task C (Voice UX): voice_telemetry.py (718 lines) + widget_orb.py (321 lines) exist, tests exist (791 lines)
  - Task D (Mission Transparency): MissionStep exists, step-driven progress partially wired
  - Task E (Self-Diagnosis): self_diagnosis.py (648 lines) + tests (750 lines) exist, but approval pipeline not connected
  - Task F (Memory Hygiene): memory_hygiene.py (437 lines) + tests (457 lines) exist, but no scheduled runs
  - Task G (Citation/Voice Link): Speech compaction for URLs implemented
  - Task H (Desloppify): Baseline captured (33.2), but score hasn't moved to target of 50+

**Honest Phase 1 assessment: ~45-50%** — infrastructure is built but integration/verification is incomplete.

### v5.0 Completion Gate Status

| Gate Criterion | Status |
|---------------|--------|
| 8-hour soak run: zero crashes | NOT TESTED — no soak harness exists |
| Cross-provider context handoff | MODULE EXISTS but no integration test with real providers |
| Mission UI shows truthful progress | PARTIAL — MissionStep exists, but no pause/resume/schedule |
| Voice accuracy acceptance set | NOT STARTED — no real-room benchmark |
| Mobile-to-desktop task loop | NOT STARTED |
| Security scans meet thresholds | FAILING — bandit: 165 findings (1 high), mypy: 105 errors |

---

## Part 2: Comprehensive Bug & Error Scan

### Summary Counts

| Severity | Engine Core | Security | Memory/Knowledge | Gateway/Learning/Sync | Android | Handlers | Total |
|----------|------------|----------|------------------|-----------------------|---------|----------|-------|
| Critical | 0 | 4 | 0 | 3 | 0 | 0 | **7** |
| High | 0 | 7 | 0 | 4 | 3 | 0 | **14** |
| Medium | 3 | 7 | 5 | 6 | 5 | 7 | **33** |
| Low | 9 | 5 | 6 | 5 | 6 | 4 | **35** |
| **Total** | **12** | **23** | **11** | **18** | **14** | **11** | **89** |

---

### CRITICAL Issues (7) — Fix Immediately

**SEC-C1: Unauthenticated containment recovery for levels 1-3**
- File: `security/containment.py:328`
- Recovery for BLOCK and ISOLATE containments requires no master password. An attacker can undo their own containment.
- Fix: Require authentication for `recover()` at all containment levels >= 2.

**SEC-C2: No input validation on IP addresses across security module**
- Files: `containment.py`, `ip_tracker.py`, `threat_detector.py`, `orchestrator.py`
- Arbitrarily long strings as "IP addresses" cause unbounded memory growth in tracking dicts/sets.
- Fix: Validate IP format and cap length on all entry points.

**SEC-C3: PII (email addresses) logged unmasked**
- File: `security/identity_shield.py:319`
- Project policy masks phone numbers but emails are written to logs in full.
- Fix: Apply same masking policy to email addresses.

**SEC-C4: HMAC key stored in plaintext in memory after rotation**
- File: `security/containment.py:366`
- Rotated HMAC signing key persists as a plaintext Python string. Memory dump exposes it.
- Fix: Use a secure memory buffer or zeroize after use.

**GW-C1: CostTracker buffer causes duplicate cost records on flush failure**
- File: `gateway/costs.py:120-134`
- If `commit()` fails, buffer isn't cleared. Next flush re-inserts the same entries.
- Fix: Clear buffer entries that were successfully written, or use INSERT OR IGNORE.

**GW-C2: Sync compact_changelog deletes unseen entries for new devices**
- File: `sync/changelog.py:486-514`
- When a new device starts syncing, compaction deletes entries the device hasn't seen.
- Fix: Register new devices in `_sync_cursor` at version 0 on first contact.

**GW-C3: Health tracker blind to cumulative fallback chain latency**
- File: `gateway/models.py:808-812`
- `chain_latency_ms` is logged but never recorded, making health-based routing blind to fallback cost.
- Fix: Record chain_latency_ms in health tracker alongside per-provider latency.

---

### HIGH Issues (14) — Fix Soon

**SEC-H1: Prompt injection firewall bypassed by Unicode confusables**
- File: `security/injection_firewall.py:391-406`
- Fullwidth Latin characters bypass all regex patterns. No Unicode NFKD normalization.
- Fix: Apply `unicodedata.normalize('NFKD', text)` before pattern matching.

**SEC-H2: Firewall bypass via multi-layer encoding**
- File: `security/injection_firewall.py:318-380`
- Only decodes one layer; double-encoding bypasses detection.
- Fix: Iteratively decode until stable.

**SEC-H3: SQL injection pattern false positives trigger containment**
- File: `security/threat_detector.py:24-39`
- Matches "Please SELECT the best option" as SQL injection. Triggers auto-containment.
- Fix: Require SQL structural context (quotes, semicolons, parentheses) around keywords.

**SEC-H4: Race condition in Feodo blocklist refresh**
- File: `security/threat_intel.py:269-302`
- Thundering herd of redundant downloads on concurrent freshness checks.
- Fix: Hold lock through the network call or use a flag.

**SEC-H5: Forensic evidence silently lost on write failure**
- File: `security/forensic_logger.py:55-61`
- Disk exhaustion causes forensic events to be silently dropped.
- Fix: Implement overflow buffer or crash-safe fallback path.

**SEC-H6: Alert chain dedup race condition**
- File: `security/alert_chain.py:86-148`
- Concurrent alerts can be incorrectly deduped during dispatch.
- Fix: Hold lock through dispatch completion.

**SEC-H7: Nonce replay cache lost on restart**
- File: `security/threat_detector.py:327`
- Uses `time.monotonic()` so cache is empty on restart. Previously-seen nonces replayable within 120s window.
- Fix: Persist nonce cache or use wall-clock timestamps.

**GW-H1: No retry/backoff for Anthropic rate limiting**
- File: `gateway/models.py:606-614`
- OpenAI path has 429 retry logic; Anthropic path has none. Wastes money on fallback.
- Fix: Add short-wait retry for Anthropic `RateLimitError`.

**GW-H2: Harvesting providers have zero retry logic**
- File: `harvesting/providers.py:96,244`
- Single-shot API calls. Transient failures kill entire harvest.
- Fix: Add retry with exponential backoff.

**GW-H3: KG metrics reads DB without holding any lock**
- File: `proactive/kg_metrics.py:90-155`
- Can cause SQLITE_BUSY errors or inconsistent reads under concurrent mutations.
- Fix: Acquire `kg.db_lock` before query batch.

**GW-H4: Circuit breaker `reset_stats()` doesn't reset `consecutive_failures`**
- File: `gateway/circuit_breaker.py:82-86`
- Half-open probe failure immediately jumps to max cooldown tier.
- Fix: Reset `consecutive_failures` in `reset_stats()`.

**AND-H1: Integer overflow in backoff calculation**
- File: `data/CommandQueueProcessor.kt:122`
- `maxOfflineQueueAgeHours * 60 * 60 * 1000` overflows Int for values > 596 hours.
- Fix: Use `Long` arithmetic: `hours * 60L * 60 * 1000`.

**AND-H2: TransactionDao getTotalSpendInRange ignores direction**
- File: `data/dao/TransactionDao.kt:36-39`
- Sums ALL amounts including credits/income, inflating "total spend."
- Fix: Add `AND direction = 'debit'` to query.

**AND-H3: Expired commands never purged from database**
- File: `data/CommandQueueProcessor.kt:168-174`
- Only `status = 'sent'` commands are purged. Expired commands accumulate forever.
- Fix: Add expired status to purge query.

---

### MEDIUM Issues (33) — Detailed in Appendix

Key themes:
- **Missing error messages:** 9 handler error paths return bare `return_code=2` with no diagnostic message
- **Lock ordering risks:** Migration bypasses `_db_lock`, consolidator has ABBA deadlock potential
- **FTS5 crash-unsafe rebuild:** Drop-then-recreate window with no recovery detection
- **Stale singletons:** `_shared_provenance` never refreshed when orchestrator replaced
- **Dead command fields:** `DiagnosticRunCommand.categories`, `IntelligenceDashboardCommand.output_path`, multiple `as_json` fields ignored by handlers
- **Memory provenance allows quarantining OWNER_INPUT records**
- **Non-atomic JSONL appends** in cost_tracking and kg_metrics
- **Android:** Negative notification IDs, coroutine scope leaks, SharedPreferences pollution

---

### LOW Issues (35) — Detailed in Appendix

Key themes:
- **Pricing returns $0.00 for unrecognized models** without warning
- **Feedback tracker excludes neutral entries** from sample count
- **Inconsistent decay constants** across learning modules
- **Android:** Pull-to-refresh hardcoded false, master password retained in memory, missing FK constraints
- **Stale access counts lost on shutdown** (search.py pending buffer)
- **Duplicate embedding computation on cache miss**

---

### Clean Patterns (verified absence of issues)

| Pattern | Status |
|---------|--------|
| Bare `except: pass` | **CLEAN** — 0 instances |
| TODO/FIXME/HACK/XXX | **CLEAN** — 0 instances |
| Hardcoded API keys/passwords | **CLEAN** — 0 instances |
| `eval()` or `exec()` | **CLEAN** — 0 instances |
| `shell=True` | **CLEAN** — 0 instances |
| SQL injection via f-strings | **CLEAN** — all queries parameterized |
| `open()` without context manager | **CLEAN** — 0 instances |
| Missing `await` on async | **CLEAN** — engine is synchronous |
| `time.sleep()` in async | **CLEAN** — all in sync contexts |
| Handler registration gaps | **CLEAN** — all commands have handlers |
| Command bus thread safety | **CLEAN** — RLock protected |
| Path traversal in handlers | **CLEAN** — `_check_path_within_root()` used consistently |

---

## Part 3: Optimization & Upgrade Opportunities

### Tier 1: High Impact, Low Risk

**OPT-01: Add Anthropic rate limit retry (matches OpenAI path)**
- Impact: Saves money, reduces unnecessary fallbacks
- Effort: ~20 lines in `gateway/models.py`
- Risk: None — identical pattern already exists for OpenAI

**OPT-02: Reset circuit breaker consecutive_failures on half-open**
- Impact: Prevents permanent max-cooldown lockout after recovery
- Effort: 1 line in `circuit_breaker.py`
- Risk: None

**OPT-03: Add harvester retry with exponential backoff**
- Impact: Dramatically improves harvest reliability
- Effort: ~30 lines, wrap existing calls
- Risk: None — pure resilience improvement

**OPT-04: Unicode NFKD normalization in injection firewall**
- Impact: Closes entire class of bypass vectors
- Effort: 2 lines + test
- Risk: Negligible — normalization is standard practice

**OPT-05: Register new sync devices at cursor 0 on first contact**
- Impact: Prevents data loss for new device onboarding
- Effort: ~10 lines in changelog.py
- Risk: Low

### Tier 2: Medium Impact, Medium Effort

**OPT-06: Implement soak test harness**
- Impact: Enables REL-01 and REL-02 verification
- Effort: New test script, ~200 lines
- Reason: This is the single biggest blocker to v5.0 Phase 1 completion

**OPT-07: Wire conversation_state.py into actual provider switching**
- Impact: Enables CTX-01 through CTX-06
- Effort: Integration in `voice_pipeline.py`, `gateway/cli_providers.py`, `daemon_loop.py`
- Reason: The module exists and is tested, but not connected to real provider switches

**OPT-08: Add mission pause/resume/restart/schedule endpoints**
- Impact: Enables LM-06, LM-07 and full lifecycle controls
- Effort: ~100 lines in mobile_routes + handlers
- Reason: Create/cancel exist but the rest of the lifecycle is missing

**OPT-09: FTS5 rebuild crash safety**
- Impact: Prevents total FTS data loss on crash during rebuild
- Effort: Add startup integrity check + rebuild-from-records recovery
- Risk: Low — purely defensive

**OPT-10: Atomic JSONL writes for cost_tracking and kg_metrics**
- Impact: Prevents corrupt metrics files under concurrent writes
- Effort: Use `os.open(O_WRONLY | O_CREAT | O_APPEND)` pattern from self_test.py
- Risk: None — pattern already exists in codebase

### Tier 3: Strategic Upgrades for "True Private AI Innovation"

**UPG-01: Local embedding model upgrade**
- Current: nomic-embed-text-v1.5 (768-dim)
- Upgrade to: nomic-embed-text-v2 or all-MiniLM-L12-v2 with quantization
- Impact: Better semantic search quality + lower memory footprint

**UPG-02: Structured output / tool-use for all LLM providers**
- Current: Free-text responses parsed with regex
- Upgrade: Use Anthropic tool_use, Ollama structured output, Groq JSON mode
- Impact: Eliminates parsing failures, enables richer automation

**UPG-03: Streaming responses for mobile API**
- Current: Full response buffered before sending (REL-05 partial)
- Upgrade: Server-sent events or chunked transfer encoding
- Impact: Eliminates context loss on long responses, enables real-time progress

**UPG-04: SQLite WAL2 mode + PRAGMA optimize**
- Current: Standard WAL mode
- Upgrade: WAL2 for better concurrent read/write, periodic PRAGMA optimize
- Impact: Reduces SQLITE_BUSY errors, improves query planning

**UPG-05: Differential sync instead of full-changelog compaction**
- Current: Changelog-based sync with compaction risks
- Upgrade: Vector clock or hybrid logical clock for true causal ordering
- Impact: Eliminates the new-device data loss bug entirely

**UPG-06: On-device Whisper for offline STT**
- Current: Parakeet TDT 0.6B (local) + Deepgram Nova-3 (cloud)
- Upgrade: Add whisper.cpp as offline-only fallback
- Impact: True offline voice capability

**UPG-07: Knowledge graph migration to DuckDB or SQLite with JSON extension**
- Current: NetworkX in-memory + SQLite backing store
- Upgrade: DuckDB for analytical queries or SQLite JSON1 for graph traversal
- Impact: Removes NetworkX memory overhead, enables richer graph queries

**UPG-08: Android Kotlin Multiplatform for shared logic**
- Current: Separate Kotlin and Python implementations
- Upgrade: Share data models, crypto, and sync logic via KMP
- Impact: Eliminates drift between Android and desktop implementations

**UPG-09: Proactive intelligence via local RAG pipeline**
- Current: Direct LLM queries with context injection
- Upgrade: Full RAG with re-ranking (e.g., ColBERT or cross-encoder)
- Impact: Dramatically better recall from memory and knowledge graph

**UPG-10: End-to-end encryption for mobile-desktop communication**
- Current: HMAC-signed HTTP + Fernet-encrypted sync payloads
- Upgrade: Noise protocol or WireGuard tunnel for always-on encryption
- Impact: Zero-knowledge transport layer, no separate HMAC/Fernet concerns

---

## Part 4: Recommended Priority Actions

### Immediate (this week)
1. Fix SEC-C1: Unauthenticated containment recovery (security-critical)
2. Fix SEC-C2: IP address validation (security-critical)
3. Fix SEC-C3: Email PII masking (compliance)
4. Fix GW-C2: Sync changelog data loss for new devices
5. Fix GW-H4: Circuit breaker consecutive_failures reset (1-line fix)
6. Fix AND-H2: Transaction spend query (1-line fix)

### Short-term (next 2 weeks)
7. Fix SEC-H1: Unicode NFKD normalization in firewall
8. Fix GW-H1: Anthropic rate limit retry
9. Fix GW-H2: Harvester retry logic
10. Fix GW-H3: KG metrics locking
11. Wire conversation_state.py into real provider switching (OPT-07)
12. Add soak test harness (OPT-06)

### Medium-term (next month)
13. Fix all 33 MEDIUM issues
14. Add mission lifecycle endpoints (pause/resume/restart/schedule)
15. Implement streaming responses for mobile API
16. Drive bandit findings to 0 high, < 20 medium
17. Drive mypy errors to < 50

### Strategic (next quarter)
18. Implement Tier 3 upgrades based on user feedback
19. Drive desloppify strict score to 100
20. Complete v5.0 Phases 2-6

---

## Part 5: Updated CLAUDE.md Corrections

The Android scan revealed CLAUDE.md is out of date:
- States "Room DB at version 11 with 16 entities and 10 explicit Migration objects"
- Actual: version 12 with 21 entities
- This should be corrected to prevent migration errors.

---

*Report generated by comprehensive 7-agent parallel scan across all 323 source files.*
*Total issues found: 89 (7 Critical, 14 High, 33 Medium, 35 Low)*
*Clean pattern verifications: 12 categories confirmed clean*
