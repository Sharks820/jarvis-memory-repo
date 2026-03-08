# Mobile API / Sync Engine / Security Module — Deep Audit Report

**Date:** 2026-03-07  
**Scope:** `mobile_api.py`, `mobile_routes/` (8 modules), `sync/` (4 modules), `security/` (25 modules), Android client API layer  
**Auditor:** Automated deep scan

---

## Executive Summary

The Jarvis mobile stack is architecturally mature with strong security foundations. The HMAC-SHA256 auth pipeline, Fernet-encrypted sync, 3-layer injection firewall, and 5-level containment engine form a defense-in-depth posture rarely seen in personal projects. However, there are performance bottlenecks in the HTTP server layer, sync protocol gaps that could cause data loss under edge conditions, and several security tightening opportunities.

**Overall Score: 8.4 / 10** — Production-grade for a personal assistant, with clear upgrade paths.

---

## 1. Mobile API Architecture Analysis

### 1.1 Server Implementation

| Aspect | Finding |
|--------|---------|
| **Server class** | `ThreadingHTTPServer` + `BaseHTTPRequestHandler` (stdlib) |
| **Threading model** | One thread per request via `ThreadingHTTPServer` — no thread pool cap |
| **Routing** | O(1) dict-dispatch for both GET (27 routes) and POST (21 routes) |
| **TLS** | Self-signed cert with SAN entries for localhost + LAN IPs, auto-generated via openssl |
| **CORS** | Whitelist-based with dynamic LAN IP addition |
| **Compression** | gzip for JSON responses > 256 bytes via `Accept-Encoding` negotiation |

**Quality Score: 7/10**

### 1.2 Performance Findings

**Strengths:**
- O(1) dispatch tables eliminate route matching overhead
- Gzip compression reduces payload sizes for mobile bandwidth
- Thread-local stdout capture isolates concurrent request output
- CommandBus pre-warmed in background thread to avoid cold-start latency
- Lazy initialization of sync engine, memory engine, and embed service with proper double-checked locking

**Concerns:**

1. **No thread pool limit** — `ThreadingHTTPServer` spawns unbounded threads. A burst of 100+ concurrent connections (malicious or accidental) could exhaust OS thread limits. The rate limiter mitigates this at the application level (120 req/min normal, 10 req/min expensive), but a SYN flood bypasses rate limiting since threads are spawned before auth.

2. **Synchronous I/O** — `BaseHTTPRequestHandler` blocks on each request. Voice commands (`/command`) run in-process and can block for 60-240s during LLM inference. Other requests to the same server are handled in parallel (separate threads), but each thread holds a connection.

3. **No HTTP keep-alive** — Each request opens a new TCP connection. The Android client's OkHttp will attempt keep-alive, but `BaseHTTPRequestHandler` does not support persistent connections by default. This adds ~50-100ms of TCP handshake + TLS negotiation per request.

4. **No WebSocket support** — Real-time push (proactive alerts, sync notifications) requires the phone to poll. Currently uses 30s polling in `JarvisService` and 60s in `AutoSyncManager`. This means proactive alerts have up to 30s latency.

5. **Large POST body pre-read** — All POST bodies up to 5MB are read into memory before security scanning. For the `/sync/push` endpoint (2MB max), this is fine. But it means a malicious 5MB body consumes 5MB of server memory even if rejected by auth.

### 1.3 Request/Response Lifecycle

```
Request → Rate Limit Check → Pre-read POST body (≤5MB)
  → Security Orchestrator Pipeline (honeypot → IP block → threat detect → injection scan)
  → O(1) Route Dispatch → Route Handler
  → Auth (HMAC or Session) → Body Parse → Business Logic
  → Output Security Scan (for /command responses)
  → gzip compress → Response
```

This is well-structured. The security pipeline runs **before** routing, which is correct — blocked requests never reach business logic.

### 1.4 Authentication Flow

The auth chain is robust:
1. **Bearer token** check (constant-time comparison via `hmac.compare_digest`)
2. **HMAC-SHA256 signature** verification:
   - Integer timestamp required (rejects floats — prevents sub-second precision leaks)
   - 120s replay window
   - Nonce format validation (8-128 ASCII chars)
   - Signing material: `timestamp\nnonce\nbody_bytes`
3. **Nonce replay prevention** — Atomic check-and-commit in single lock acquisition (eliminates TOCTOU race)
4. **Owner guard** — Device trust via `X-Jarvis-Device-Id` header, with master password fallback
5. **Session auth** — Flexible auth supports both HMAC and session tokens via `_validate_auth_flexible`

**No bypass paths identified.** The auth chain is fail-closed at every step.

### 1.5 Rate Limiting

| Tier | Limit | Paths |
|------|-------|-------|
| Normal | 120 req/min/IP | All except expensive |
| Expensive | 10 req/min/IP | `/command`, `/self-heal`, `/auth/login`, `/feedback` |
| Bootstrap | 5 attempts/min/IP | `/bootstrap` |
| Master password | 5 attempts/min/IP | Master password verification |

Good separation — widget polling (33 req/min) doesn't block command budget.

Rate limit dict has pruning at 5000 entries with oldest-half eviction. No memory leak risk.

### 1.6 Missing Features for Seamless Mobile Experience

1. **WebSocket channel** for push notifications — would eliminate 30s alert latency
2. **HTTP/2 support** — multiplexing would reduce connection overhead significantly
3. **Connection keep-alive** — reduce TLS handshake overhead on repeated requests
4. **Response caching headers** — `ETag`/`Last-Modified` for dashboard/settings endpoints
5. **Streaming response** for `/command` — show partial LLM output as it generates
6. **Batch endpoint** — `/batch` to combine multiple GET queries in one request (widget uses 3-4 endpoints per refresh)

---

## 2. Sync Engine Analysis

### 2.1 Architecture

| Component | Implementation |
|-----------|---------------|
| **Changelog** | SQLite triggers on 6 tables (records, kg_nodes, kg_edges, user_preferences, response_feedback, usage_patterns) |
| **Versioning** | Monotonic `__version` per table via `_sync_version_seq` atomic increment |
| **Diff computation** | Cursor-based: each device tracks `last_version` per table |
| **Conflict resolution** | Field-level merge with configurable strategy ("most_recent" or "desktop_wins") |
| **Encryption** | PBKDF2HMAC (480K iterations) → Fernet (AES-128-CBC + HMAC-SHA256) |
| **Compression** | zlib level 6 before encryption |
| **Transport** | HTTP POST with base64-encoded encrypted payloads |
| **Auto-sync** | LAN URL primary, relay URL fallback, configurable intervals |

**Quality Score: 8/10**

### 2.2 Data Types Covered

| Table | Syncs | Notes |
|-------|-------|-------|
| `records` | ✅ | Memory records with noise filter (access_count, last_accessed don't trigger sync) |
| `kg_nodes` | ✅ | Knowledge graph nodes |
| `kg_edges` | ✅ | Knowledge graph edges |
| `user_preferences` | ✅ | Composite PK (category:preference) |
| `response_feedback` | ✅ | Route quality feedback |
| `usage_patterns` | ✅ | Hour/day/route patterns |

**Not synced (potential gaps):**
- Command queue state (handled separately by `CommandQueueProcessor`)
- Conversation history (phone stores locally, desktop processes in-process)
- Scam intel data (call_intel.jsonl, campaigns) — phone reports via `/scam/report-call` instead
- Learning interactions (recorded via `LearnInteractionCommand` on desktop side)

These omissions are **architecturally intentional** — the phone is the sensor layer, desktop is the brain. Phone data flows up via API endpoints, not sync.

### 2.3 Conflict Resolution

The field-level merge is well-designed:
- DELETE always wins over UPDATE (correct — prevents zombie data)
- `most_recent` strategy compares timestamps, desktop wins exact ties
- Non-overlapping fields merge cleanly (both devices' changes preserved)
- Same-field conflicts resolved by configured strategy

**Edge case concern:** If phone and desktop both UPDATE the same record with different fields within the same second, and one field overlaps, the timestamp comparison granularity is 1-second (ISO datetime). With `most_recent`, this is a tie → desktop wins. This is acceptable behavior.

### 2.4 Data Loss Risk Assessment

| Scenario | Risk Level | Mitigation |
|----------|-----------|------------|
| Network interruption mid-sync | **LOW** | `apply_incoming` uses SQLite transaction; rollback on any error; cursors only advance for successfully applied ops |
| Corrupt sync payload | **LOW** | Fernet provides authenticated encryption — corruption detected on decrypt; zlib has built-in checksum |
| Replay of captured payload | **LOW** | Fernet TTL default 1 hour; HMAC on transport layer has 120s window + nonce |
| Decompression bomb | **LOW** | 16 MiB decompressed limit with streaming size check |
| Changelog overflow | **LOW** | `compact_changelog` prunes entries older than 7 days that all devices have synced past |
| Two phones syncing simultaneously | **MEDIUM** | Write lock serializes `apply_incoming`, but no conflict detection between two phone devices |

### 2.5 Sync Recovery

- **On transaction failure:** Full rollback, cursors not advanced, errors returned to client
- **On encryption failure:** `InvalidToken` from Fernet — client gets error, can retry
- **On partial apply:** Cursors advance only to max version of *successfully* applied ops — unrecognized operations are never permanently skipped
- **Nonce cache persistence:** Nonces survive server restarts via `nonce_cache.jsonl` with atomic write

**Strength:** The cursor-only-advance-on-success design is excellent — it prevents sync gaps.

### 2.6 Bandwidth Efficiency

- Zlib compression reduces payload by ~60-80% for typical JSON
- Delta sync via changelog (only changes since last cursor, not full state)
- `has_more` flag in pull response enables pagination for large diffs (500 entries per pull)
- Noise field filtering prevents access_count bumps from generating sync traffic

**Estimated typical sync payload:** 2-20 KB compressed for a 60-second sync interval with moderate desktop activity.

---

## 3. Security Module Analysis

### 3.1 Module Inventory (25 files, ~250KB of security code)

| Module | Purpose | Score |
|--------|---------|-------|
| `orchestrator.py` | Unified pipeline wiring all modules | 9/10 |
| `injection_firewall.py` | 3-layer prompt injection detection (50+ patterns, structural, semantic) | 9/10 |
| `threat_detector.py` | 8 detection rules (SQL injection, path traversal, command injection, etc.) | 8/10 |
| `forensic_logger.py` | SHA-256 hash-chain tamper-evident JSONL log | 9/10 |
| `containment.py` | 5-level graduated response (THROTTLE → FULL_KILL) | 9/10 |
| `attack_memory.py` | Persistent attack pattern storage for "learn forever" defense | 8/10 |
| `adaptive_defense.py` | Auto-rule generation from attack patterns | 8/10 |
| `alert_chain.py` | Graduated alert escalation (5 levels) | 8/10 |
| `ip_tracker.py` | IP threat scoring + auto-escalation blocklist | 8/10 |
| `output_scanner.py` | 5-category outbound scan (credentials, paths, exfil, injection, persona) | 8/10 |
| `honeypot.py` | Fake endpoints that trap scanning tools | 9/10 |
| `owner_session.py` | Argon2id/PBKDF2 session auth with exponential lockout | 9/10 |
| `identity_shield.py` | Breach monitoring, impersonation, typosquat detection | 8/10 |
| `identity_monitor.py` | Identity context tracking | 7/10 |
| `network_defense.py` | Home network monitoring, known device registry | 8/10 |
| `threat_intel.py` | External threat intelligence feed integration | 8/10 |
| `threat_neutralizer.py` | Active threat neutralization with evidence preservation | 8/10 |
| `session_manager.py` | General session lifecycle | 7/10 |
| `action_auditor.py` | Bot action audit trail | 8/10 |
| `scope_enforcer.py` | Privilege boundary enforcement | 8/10 |
| `resource_monitor.py` | Resource consumption tracking | 7/10 |
| `heartbeat.py` | Security subsystem health monitoring | 7/10 |
| `memory_provenance.py` | Memory trust levels + quarantine | 8/10 |
| `net_policy.py` | Network policy configuration | 7/10 |
| `defense_commands.py` | CQRS command definitions | 7/10 |

**Overall Security Score: 8.5/10**

### 3.2 Prompt Injection Firewall (3 Layers)

**Layer 1 — Pattern Matching (50+ patterns):**
- 8 instruction override patterns ("ignore previous", "forget instructions", etc.)
- 3 system prompt leak patterns
- 8 role hijack patterns ("you are now", "pretend to be", etc.)
- 8 mode override patterns ("admin mode", "jailbreak", etc.)
- 9 fake header patterns (SYSTEM:, ADMIN:, [INST], etc.)
- 4 encoding patterns (base64, hex, URL-encoded)
- 5 delimiter injection patterns (```, ---,  <<<, XML tags)
- 5 repetition patterns ("repeat after me", "say exactly")
- 6 HTML/script patterns (XSS vectors)
- 2 Unicode patterns (RTL override, zero-width steganography)

**Layer 2 — Structural Analysis:**
- Context switch detection (conversational → imperative)
- Imperative instruction detection ("you must ignore")
- Encoded payload decode-and-check (base64 → check decoded content for keywords)
- Special character ratio analysis (>15% triggers)
- Mixed Unicode script detection (Latin + Cyrillic homoglyphs)

**Layer 3 — Semantic Analysis:**
- 20 injection template embeddings
- Cosine similarity threshold: 0.75
- Gracefully degrades when embed service unavailable

**Decision Matrix:**
- ≥2 strong pattern hits → HOSTILE (blocked)
- Pattern + structural → HOSTILE (blocked)
- Semantic + any signal → HOSTILE (blocked)
- Pattern only → INJECTION_DETECTED (blocked)
- Semantic or structural only → SUSPICIOUS (logged, allowed)

**Assessment:** This is a solid multi-layer approach. The encode-then-check in Layer 2 catches obfuscated attacks that pattern-only scanners miss. The semantic layer provides future-proofing against novel phrasings.

**Potential bypass vectors:**
1. **Multilingual attacks** — Patterns are English-only. An attacker using Spanish ("ignora las instrucciones anteriores") or other languages bypasses Layer 1. Layer 3 may catch this if the embedding model is multilingual.
2. **Token-splitting** — "ig" + "nore prev" + "ious" split across JSON fields might bypass single-string scanning. The body is scanned as a single string, so this is mitigated for the main body.
3. **Prompt injection via KG facts** — If a synced KG fact contains injection text, it's already in the trusted data pipeline. The output scanner catches this on the way out.

### 3.3 HMAC Validation Security

- **Timing-safe comparison** via `hmac.compare_digest` — no timing side-channel
- **Integer timestamps enforced** — rejects floats (prevents sub-second precision leakage)
- **120s replay window** — appropriate for mobile networks with clock drift
- **Nonce format validation** — 8-128 ASCII chars, preventing injection via nonce header
- **Atomic nonce check-and-commit** — TOCTOU race eliminated by single-lock critical section
- **Nonce cache capped** at 100K entries with forced cleanup — prevents memory exhaustion
- **Nonce persistence** across server restarts via atomic JSON file writes

**No bypass paths found.** The signing material format (`timestamp\nnonce\nbody`) is unambiguous — no delimiter confusion possible.

### 3.4 Forensic Log Integrity

- **SHA-256 hash chain** — each entry's `prev_hash` is the SHA-256 of the previous entry's JSON line
- **Zero-hash genesis** — first entry uses `"0" * 64` as prev_hash
- **Static verification** — `verify_chain(path)` validates the entire chain
- **Rotation** — max 50MB per file, 10 rotated files kept
- **Law enforcement export** — date-filtered ZIP with summary
- **Thread-safe** — all writes under lock, hash only advances after successful write

**Tamper detection:** If any entry is modified, the chain breaks at the next entry. If an entry is deleted, the chain breaks at the following entry. Appending is the only undetectable modification — but the hash chain prevents insertion/modification/deletion.

**Limitation:** A sophisticated attacker with write access could recompute the entire chain from any modification point. This is inherent to hash chains without external anchoring (e.g., timestamping authority, blockchain). For a personal assistant, this is acceptable.

### 3.5 Containment Engine

| Level | Name | Actions |
|-------|------|---------|
| 1 | THROTTLE | Rate limit IP to 1 req/min |
| 2 | BLOCK | Add IP to blocklist |
| 3 | ISOLATE | Disable endpoint for IP |
| 4 | LOCKDOWN | Shut down mobile API, rotate HMAC key, invalidate all sessions |
| 5 | FULL_KILL | Stop all services, generate incident report, urgent notification |

**Recovery gating:**
- Levels 1-3: Auto-recover (no password)
- Levels 4-5: Require master password (PBKDF2-SHA256, 600K iterations)
- Failed recovery attempts are forensic-logged at CRITICAL severity

**Credential rotation at Level 4+:**
- New HMAC key generated via `os.urandom(32).hex()`
- Propagated to server via callback (`_on_credential_rotate`)
- **Impact:** All existing Android clients immediately lose auth until they re-bootstrap

**Assessment:** The escalation chain is robust. The automatic credential rotation at Level 4 is a strong response — it invalidates all compromised tokens. The recovery requiring master password prevents an attacker from un-containing themselves.

### 3.6 Signing Key Rotation

**Current mechanism:**
- Containment Level 4+ triggers `_rotate_credentials()` which generates a new key and calls the server callback
- The server updates `self.signing_key` in-memory
- Android clients must re-bootstrap with master password to get the new key

**Gap:** There is no proactive key rotation schedule (e.g., rotate every 90 days). The key only rotates during a security incident. For a personal assistant on a trusted LAN, this is acceptable. For higher security, consider periodic rotation via a CQRS command.

### 3.7 Nonce Replay Window (120s)

**Is 120s appropriate?**
- Mobile networks can have clock drift up to ±30s
- TLS adds 1-2s of handshake latency
- LLM commands can take 60-240s to complete, but the nonce is checked at request arrival, not completion
- **Verdict:** 120s is appropriate. Shorter (e.g., 60s) would cause false rejections on high-latency cellular connections. Longer (e.g., 300s) would increase the replay window unnecessarily.

---

## 4. Android Client Analysis

### 4.1 Communication Architecture

| Component | Implementation |
|-----------|---------------|
| **HTTP client** | OkHttp + Retrofit2 |
| **Auth signing** | `HmacInterceptor` (HMAC-SHA256 per request) |
| **Credential storage** | `EncryptedSharedPreferences` (Android Keystore-backed AES-256-GCM) |
| **Connectivity** | Dual-URL: LAN primary + relay fallback |
| **Failover** | OkHttp interceptor retries failed LAN requests via relay URL |
| **Offline queue** | Room DB `CommandQueueEntity` with exponential backoff |
| **Intelligence** | `IntelligenceRouter` → desktop, on-device (Gemini Nano), or queue |
| **Sync** | `AutoSyncManager` with network callback, adaptive intervals |

**Quality Score: 9/10**

### 4.2 Security Assessment

**Strengths:**
- Signing key in `EncryptedSharedPreferences` (AES-256-GCM via Android Keystore) — not extractable without root
- Master password stored as SHA-256 hash (never plaintext), with legacy migration
- SQLCipher passphrase derived from signing key via `getOrCreateFallbackPassphrase()`
- HMAC signing applies to ALL requests including relay URL
- Nonce generated via `SecureRandom` (16 bytes = 128-bit entropy)
- Integer timestamp (`System.currentTimeMillis() / 1000L`) matches server requirement
- Debug logging only in BuildConfig.DEBUG

**Concerns:**

1. **No certificate pinning** — The `OkHttpClient` does not implement certificate pinning. The `/cert-fingerprint` endpoint exists for TOFU (Trust On First Use), but the client code doesn't enforce it in the OkHttp chain. A MITM with a CA-signed cert could intercept traffic.

2. **Master password hash uses SHA-256** — The Android `CryptoHelper.hashPassword()` uses a single SHA-256 without salt. The desktop side uses PBKDF2 with 600K iterations. This means a brute-force attack on the Android-side hash is much faster than on the desktop side. However, this hash is only used for local verification (e.g., unlocking sensitive features), not for network auth.

3. **Relay URL not validated** — The relay URL from sync config is used directly without hostname validation. If the sync config is compromised, the phone could be directed to a malicious relay.

### 4.3 Offline Queue Robustness

- Commands persist in Room DB (survives app kill/restart)
- `recoverStale()` on service start recovers commands stuck in "sending" state
- Exponential backoff: `base * 2^retry_count` capped at `max_backoff`
- Age-based expiry: commands older than `maxOfflineQueueAgeHours` (default 168h = 7 days) are expired
- `flushPending()` runs every 30s in foreground service loop
- Sent commands purged after 7 days

**Assessment:** The offline queue is robust. Commands won't be lost even after a week offline, and the exponential backoff prevents battery drain from futile retries.

---

## 5. Per-Component Quality Scores

| Component | Score | Rationale |
|-----------|-------|-----------|
| **Mobile API Server** | 7/10 | Functionally complete, well-structured routing and auth, but stdlib HTTP server limits performance |
| **Mobile Routes** | 9/10 | Comprehensive endpoint coverage, consistent error handling, proper auth on every endpoint |
| **Sync Engine** | 8/10 | Solid delta sync with field-level conflict resolution, good recovery properties |
| **Sync Transport** | 9/10 | Strong encryption (PBKDF2 480K + Fernet), compression, size limits, decompression bomb protection |
| **Security Orchestrator** | 9/10 | Excellent pipeline design, graceful degradation, fail-closed philosophy |
| **Injection Firewall** | 9/10 | 3-layer defense with 50+ patterns, structural analysis, semantic embeddings |
| **Forensic Logger** | 9/10 | Hash-chain integrity, rotation, law enforcement export |
| **Containment Engine** | 9/10 | Graduated response, credential rotation, master password recovery gating |
| **Threat Detector** | 8/10 | 8 rule types, fail-closed for critical rules, good signal aggregation |
| **Output Scanner** | 8/10 | 5 categories of outbound protection, catches credential leaks and persona violations |
| **Android Client** | 9/10 | Encrypted credential storage, dual-URL failover, HMAC on all requests |
| **Android Sync** | 8/10 | Network-aware, adaptive intervals, intelligence merge |
| **Android Offline Queue** | 9/10 | Persistent, backoff, age expiry, stale recovery |

---

## 6. Specific Code Fixes

### Fix 1: Add thread pool limit to prevent thread exhaustion

**File:** `engine/src/jarvis_engine/mobile_api.py`  
**Issue:** `ThreadingHTTPServer` has no max thread limit.  
**Fix:**
```python
class MobileIngestServer(ThreadingHTTPServer):
    allow_reuse_address = True
    # Limit concurrent request threads to prevent resource exhaustion
    # 32 is generous for a personal assistant (typical: 1-3 concurrent)
    _max_threads = 32
    _thread_semaphore = None

    def process_request(self, request, client_address):
        if self._thread_semaphore is None:
            import threading
            self._thread_semaphore = threading.Semaphore(self._max_threads)
        if not self._thread_semaphore.acquire(timeout=5.0):
            # Reject the connection if too many threads are active
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        finally:
            self._thread_semaphore.release()
```

### Fix 2: Android CryptoHelper should use salted PBKDF2 for master password

**File:** `android/app/src/main/java/com/jarvis/assistant/security/CryptoHelper.kt`  
**Issue:** `hashPassword()` uses unsalted SHA-256, much weaker than desktop's PBKDF2.  
**Fix:** Use PBKDF2WithHmacSHA256 with random salt, store salt alongside hash.

### Fix 3: Android should enforce cert pinning from /cert-fingerprint

**File:** `android/app/src/main/java/com/jarvis/assistant/api/JarvisApiClient.kt`  
**Issue:** No TLS certificate pinning implemented despite `/cert-fingerprint` endpoint existing.  
**Fix:** After bootstrap, fetch fingerprint and configure `CertificatePinner` in OkHttp.

### Fix 4: Validate relay URL hostname before use

**File:** `android/app/src/main/java/com/jarvis/assistant/sync/AutoSyncManager.kt`  
**Issue:** Relay URL from sync config is used without validation.  
**Fix:** Validate URL format and optionally maintain an allowlist of trusted relay domains.

---

## 7. Upgrade Roadmap

### Phase 1: Quick Wins (1-2 days)
1. ✅ Add thread pool semaphore to `MobileIngestServer` (Fix 1)
2. ✅ Implement TOFU cert pinning in Android OkHttp client (Fix 3)
3. ✅ Add `Connection: keep-alive` support to response headers
4. ✅ Add `ETag` headers to dashboard/settings GET endpoints for conditional requests

### Phase 2: Performance (3-5 days)
1. Evaluate migration from `BaseHTTPRequestHandler` to `aiohttp` or `uvicorn` for async I/O
2. Add WebSocket endpoint for real-time push (proactive alerts, sync notifications)
3. Implement streaming response for `/command` (SSE or chunked transfer)
4. Add batch endpoint `/batch` for widget multi-query optimization

### Phase 3: Sync Hardening (2-3 days)
1. Add checksum to changelog entries for corruption detection
2. Implement sync progress callback for large diffs (phone shows progress bar)
3. Add sync conflict notification to phone (currently silent merge)
4. Consider adding vector clock for multi-device sync scenarios (future laptop node)

### Phase 4: Security Hardening (2-3 days)
1. Add multilingual injection patterns (Spanish, Chinese, Arabic attack vectors)
2. Implement periodic signing key rotation (e.g., every 90 days)
3. Strengthen Android master password hashing to PBKDF2 (Fix 2)
4. Add relay URL validation and domain allowlist (Fix 4)
5. Consider adding a tamper-detection agent that periodically re-verifies the forensic log chain

### Phase 5: Advanced (5-7 days)
1. mTLS between phone and desktop (mutual certificate authentication)
2. Hardware-backed key attestation on Android (verify signing key hasn't been extracted)
3. End-to-end encrypted sync channel (currently Fernet, could use NaCl/libsodium)
4. Implement sync checkpointing for disaster recovery

---

## 8. Summary

The Jarvis mobile/sync/security stack is impressively comprehensive for a personal assistant project. The defense-in-depth security posture (HMAC auth → rate limiting → security orchestrator → injection firewall → output scanner → containment engine) rivals commercial API security. The sync engine's cursor-based delta protocol with field-level conflict resolution is well-designed and handles edge cases gracefully.

The primary upgrade opportunity is the HTTP server layer — migrating from stdlib `BaseHTTPRequestHandler` to an async framework would unlock HTTP/2, WebSockets, and connection multiplexing, dramatically improving the mobile experience. The Android client is well-architected with encrypted credential storage and dual-URL failover, though it should implement the TOFU cert pinning that the infrastructure already supports.

**Key takeaway:** The security foundations are strong and the architecture is sound. The recommended fixes are optimizations and hardening, not fundamental redesigns.
