I now have a comprehensive view of the codebase. Here is the full engineering review.

---

# Jarvis Engine — Strict Engineering Review

## 1. Findings by Severity

### CRITICAL

**C1. PowerShell injection in `_detect_hotword_once` via `keyword` parameter**
`desktop_widget.py:145-161`

The `keyword` parameter is interpolated directly into a PowerShell script string with only a `.strip().lower()[:40]` filter. A malicious or accidental keyword like `jarvis'); Remove-Item -Recurse C:\* -Force; #` would escape the single-quoted PowerShell string and execute arbitrary commands. The truncation to 40 chars limits but does not prevent exploitation.

**Fix:** Validate `keyword` against `^[a-zA-Z0-9 ]+$` regex before interpolation, or pass it as a base64-encoded argument decoded inside PowerShell. Same issue exists in `_voice_dictate_once` (line 130) but `timeout_s` is already an int, so it's safe.

---

**C2. `_sanitize_memory_content` regex uses double-escaped backslashes — does nothing**
`main.py:79-80`

```python
cleaned = re.sub(r"(?i)(master\\s*password\\s*[:=]\\s*)(\\S+)", r"\1[redacted]", content)
cleaned = re.sub(r"(?i)(token\\s*[:=]\\s*)(\\S+)", r"\1[redacted]", cleaned)
```

The raw string literal `r"master\\s*password"` matches the literal text `master\s*password`, not `master password` or `masterpassword`. The regex `\s` is meant to match whitespace but `\\s` matches the literal character `\` followed by `s`. **Credentials are never redacted.** Any content ingested via `_auto_ingest_memory` retains raw master passwords and tokens in the memory store.

**Fix:** Use single backslashes: `r"(?i)(master\s*password\s*[:=]\s*)(\S+)"` and `r"(?i)(token\s*[:=]\s*)(\S+)"`.

---

**C3. Master password sent in plaintext over HTTP in mobile API & desktop widget**
`mobile_api.py:384-386`, `desktop_widget.py:98-112`

The `X-Jarvis-Master-Password` header and the `master_password` field in `/command` POST body are transmitted in cleartext over HTTP. The non-loopback guard (`mobile_api.py:539-548`) can be disabled via `JARVIS_ALLOW_INSECURE_MOBILE_BIND=true`, enabling network exposure.

**Fix:** When `JARVIS_ALLOW_INSECURE_MOBILE_BIND=true` is set, additionally disable master password acceptance over non-loopback connections, or require TLS wrapping.

---

### HIGH

**H1. `/health` and `/`, `/quick` endpoints bypass all authentication**
`mobile_api.py:393-403`

`GET /health` returns `{"ok": true}` with no auth check. While health checks are typically unauthenticated, the `/` and `/quick` endpoints serve the full Quick Panel HTML file (which contains the application UI) without any authentication. An attacker on the same network can access the full panel if bound to non-loopback.

**Fix:** Require auth on `/` and `/quick`, or at minimum only serve them on loopback addresses.

---

**H2. `GET /settings` validates auth with empty body but signature is over empty bytes**
`mobile_api.py:404-407`

`self._validate_auth(b"")` signs `timestamp + nonce + b""`. This means the HMAC signature for any GET endpoint with empty body is valid for _any_ GET endpoint. A captured `/settings` request signature can be replayed against `/dashboard` (within the nonce window). The nonce prevents exact replay, but the signature doesn't bind to the path.

**Fix:** Include the request path in the signing material: `ts + "\n" + nonce + "\n" + path + "\n" + body`.

---

**H3. Nonce store is unbounded in-memory `dict` with weak cleanup**
`mobile_api.py:46-48, 310-370`

`MAX_NONCES = 100_000` means up to 100k entries in memory. The `_cleanup_nonces` runs only every 30 seconds and only evicts expired entries. Under sustained load, the dict grows to 100k entries and then rejects all requests with "Replay cache saturated" — a denial-of-service vector. Legitimate requests are rejected.

**Fix:** Use a bounded data structure (e.g., `collections.OrderedDict` with LRU eviction) or reduce `REPLAY_WINDOW_SECONDS` / `MAX_NONCES` and add rate-limiting per source IP.

---

**H4. SSRF via DNS rebinding in `_is_safe_public_url`**
`learning_missions.py:174-198`

`_is_safe_public_url` resolves the hostname to check if IPs are private/loopback, then `urlopen` resolves it again. Between the two DNS lookups, an attacker-controlled DNS server can return a public IP first (passing the check) then a private IP (hitting an internal service). This is a classic TOCTOU DNS rebinding attack.

**Fix:** Resolve once and connect to the resolved IP directly, or use a connecting socket that binds to the resolved addresses.

---

**H5. `_run_voice_command` monkey-patches `main_mod.repo_root` without thread safety**
`mobile_api.py:108-124`

In the `ThreadingHTTPServer`, concurrent `/command` requests can interleave the monkey-patching of `main_mod.repo_root`. One thread sets it, another thread's command uses the wrong root, or the `finally` block restores it at the wrong time.

**Fix:** Use a lock around the monkey-patch block, or better, refactor `cmd_voice_run` to accept `repo_root` as a parameter instead of reading a global.

---

### MEDIUM

**M1. Voice command routing is order-dependent and ambiguous**
`main.py:1486-1743`

The giant `if/elif` chain for intent routing matches substrings. "safe mode status" matches both "safe mode off" (via `"safe mode"` check) and "runtime status" (via `"safe mode status"`). The order happens to work currently, but "enable safe mode on the gaming mode" would match `"safe mode on"` before `"gaming mode"`. Any new intent phrase added carelessly will silently shadow or be shadowed.

**Fix:** Move to a structured intent table with priority/specificity scoring, or at minimum add a unit test that asserts routing priority for all known ambiguous phrases.

---

**M2. `_load_auto_ingest_hashes` grows unbounded in the `seen` list before truncation**
`main.py:120-145`

```python
seen = _load_auto_ingest_hashes(dedupe_path)    # loads all
if dedupe_hash in seen:                          # O(n) list scan
    return ""
seen.append(dedupe_hash)
_store_auto_ingest_hashes(dedupe_path, seen)     # truncates to [-400:]
```

The `in` check is O(n) on a list. With 400 max entries this is fine, but the design loads and rewrites the entire file on every ingestion. Under concurrent access (daemon + mobile API), this is a race condition — two processes can load the same file, both append, and one overwrites the other's entry.

**Fix:** Use a file-lock (e.g., `fcntl.flock` / `msvcrt.locking`) around the read-modify-write cycle, or switch to a set-based lookup.

---

**M3. `output` Text widget grows unbounded in desktop widget**
`desktop_widget.py:374-377`

Every `_log` call inserts at position `"1.0"` and never trims. Over a 24/7 runtime, this Text widget will accumulate tens of thousands of lines, consuming increasing memory and making the UI sluggish.

**Fix:** After inserting, trim lines beyond a threshold (e.g., 500 lines):
```python
if int(self.output.index("end-1c").split(".")[0]) > 500:
    self.output.delete("500.0", tk.END)
```

---

**M4. `_animate_orb` runs every 120ms forever with no throttle when minimized**
`desktop_widget.py:533-544`

The animation continues ticking every 120ms even when the widget is iconified/minimized. Over 24 hours this is ~720,000 wasted redraws.

**Fix:** Check `self.state() == "iconic"` and skip the canvas update when minimized, or increase the interval.

---

**M5. `_pulse_phase` float overflow over long runtime**
`desktop_widget.py:534`

`self._pulse_phase += 0.22` runs every 120ms. After ~30 days, this reaches ~4.7 million. While Python floats don't overflow, `math.sin` precision degrades at extreme values.

**Fix:** Wrap the phase: `self._pulse_phase = (self._pulse_phase + 0.22) % (2 * math.pi)`.

---

**M6. `os.chmod(path, 0o600)` is a no-op on Windows**
`mobile_api.py:292`, `owner_guard.py:34`, `runtime_control.py:29`, `persona.py:70`, `main.py:106`

On Windows, `os.chmod` with Unix permissions only affects the read-only attribute. The `0o600` permission has no effect — security-sensitive files (owner_guard.json, control.json, gaming_mode.json) remain world-readable to all users on the machine.

**Fix:** Use Windows ACLs via `icacls` or `win32security` API to restrict access, or document that file-level permissions are not enforced on Windows.

---

**M7. `create_learning_mission` has an ID collision risk**
`learning_missions.py:86`

```python
mission_id = f"m-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
```

Two missions created in the same second get the same ID, causing the second to overwrite the first's report file and state.

**Fix:** Append a random suffix: `f"m-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"`.

---

### LOW

**L1. `_save_missions` is not atomic**
`learning_missions.py:70-73`

Uses `path.write_text()` directly instead of the atomic write-temp-then-replace pattern used elsewhere. A crash mid-write corrupts the missions file.

**Fix:** Use the same tmp-write + `os.replace` pattern as `_write_gaming_state`.

---

**L2. `_hotword_loop` exception handling silently swallows all errors**
`desktop_widget.py:502-503`

```python
except Exception:
    pass
```

If the PowerShell command path changes, or System.Speech is unavailable, the loop silently fails forever with no feedback.

**Fix:** Log the exception at least once: catch, log, then set a flag to avoid repeated logging.

---

**L3. `log_message` override suppresses all HTTP server logging**
`mobile_api.py:533-535`

All request logging is silenced, including errors. A 500 Internal Server Error in a handler is completely invisible.

**Fix:** Log errors (status >= 400) or log to a file rather than suppressing all output.

---

**L4. `_search_duckduckgo` User-Agent is incomplete**
`learning_missions.py:120-122`

The User-Agent string is truncated (`AppleWebKit/537.36` with no closing browser identifier). DuckDuckGo may rate-limit or block incomplete UA strings.

**Fix:** Use a complete, realistic User-Agent string.

---

---

## 2. Concrete Fixes

| # | Finding | Fix |
|---|---------|-----|
| C1 | PowerShell injection | Validate keyword: `if not re.fullmatch(r'[a-zA-Z0-9 ]{1,40}', keyword): raise ValueError(...)` |
| C2 | Broken sanitizer regex | Change `\\s` to `\s` and `\\S` to `\S` in both regex patterns |
| C3 | Master password over HTTP | Reject `X-Jarvis-Master-Password` header when bound to non-loopback addresses |
| H1 | Unauthenticated quick panel | Add `_validate_auth(b"")` check to `/` and `/quick` routes |
| H2 | Path not in HMAC signature | Change signing material to `ts + "\n" + nonce + "\n" + self.path + "\n" + body` on both client and server |
| H3 | Nonce DoS | Add per-IP rate limiting (e.g., 60 req/min) before nonce insertion |
| H4 | DNS rebinding | Resolve hostname once, construct URL with resolved IP, set `Host` header manually |
| H5 | Thread-unsafe monkey-patch | Add `threading.Lock` around the monkey-patch, or pass `repo_root` as parameter |
| M1 | Intent routing ambiguity | Add explicit test matrix; refactor to specificity-ordered intent table |
| M2 | Dedupe file race | Add file-locking around read-modify-write |
| M3 | Unbounded log widget | Trim to 500 lines after each insert |
| M4 | Animation when minimized | Skip canvas update when `self.state() == "iconic"` |
| M5 | Phase float growth | Wrap with modulo `2*pi` |
| M6 | `os.chmod` no-op on Windows | Use `icacls` or document limitation |
| M7 | Mission ID collision | Append `secrets.token_hex(3)` to mission ID |
| L1 | Non-atomic mission save | Use tmp-file + `os.replace` |
| L2 | Silent hotword errors | Log first occurrence, then suppress |
| L3 | Suppressed server logs | Log errors to stderr or a log file |
| L4 | Incomplete User-Agent | Add full browser identifier string |

---

## 3. Test Cases to Add

### Security Tests
1. **`test_sanitize_memory_content_redacts_password`** — Assert `_sanitize_memory_content("master password: s3cret123")` returns `"master password: [redacted]"` (currently fails due to C2).
2. **`test_sanitize_memory_content_redacts_token`** — Same for `"token=abc123def"`.
3. **`test_detect_hotword_keyword_injection`** — Assert `_detect_hotword_once("jarvis'); Remove-Item *; #")` raises `ValueError`.
4. **`test_is_safe_public_url_rejects_localhost`** — Assert `_is_safe_public_url("http://localhost/secret")` returns `False`.
5. **`test_is_safe_public_url_rejects_private_ip`** — Assert `_is_safe_public_url("http://192.168.1.1/admin")` returns `False`.
6. **`test_is_safe_public_url_rejects_non_http`** — Assert `_is_safe_public_url("file:///etc/passwd")` returns `False`.
7. **`test_owner_guard_blocks_empty_device_id`** — Mobile API returns 401 when `X-Jarvis-Device-Id` is empty with owner guard enabled.
8. **`test_master_password_rejected_over_non_loopback`** — After fixing C3, assert master password header is rejected on non-loopback.
9. **`test_nonce_cache_saturation_returns_error`** — Fill nonce store to `MAX_NONCES`, assert next request returns 401 with "Replay cache saturated".

### Functional Tests
10. **`test_command_endpoint_rejects_empty_text`** — POST `/command` with `""` text returns error.
11. **`test_command_endpoint_rejects_oversized_text`** — POST `/command` with 3000-char text returns error.
12. **`test_ingest_rejects_oversized_content`** — POST `/ingest` with 21,000-char content returns 400.
13. **`test_ingest_rejects_oversized_task_id`** — POST `/ingest` with 200-char task_id returns 400.
14. **`test_404_on_unknown_path`** — GET `/nonexistent` returns 404.
15. **`test_voice_run_intent_routing_specificity`** — Assert "safe mode status" routes to `runtime_status`, not `runtime_safe_on`.
16. **`test_voice_run_weather`** — Assert "what's the weather in NYC" routes to weather intent.
17. **`test_create_learning_mission_empty_topic`** — Assert `ValueError` raised.
18. **`test_run_learning_mission_not_found`** — Assert `ValueError` for nonexistent mission_id.
19. **`test_owner_guard_set_short_master_password`** — Assert `ValueError` for passwords under 10 chars.
20. **`test_owner_guard_clear_master_password`** — Assert `clear_master_password` clears hash and salt.
21. **`test_owner_guard_verify_unset_password`** — Assert `verify_master_password` returns `False` when no password set.
22. **`test_settings_rejects_non_bool_gaming_enabled`** — POST `/settings` with `gaming_enabled: "yes"` returns 400.
23. **`test_concurrent_voice_commands_thread_safety`** — Two simultaneous `/command` POSTs complete without repo_root corruption.

### Regression Tests
24. **`test_daemon_cycle_count_increments`** — Assert cycle counter increments correctly across paused/gaming/active states.
25. **`test_auto_ingest_dedupe_prevents_duplicate`** — Ingest same content twice, assert second call returns empty string.
26. **`test_persona_reply_humor_level_0_no_quips`** — Assert `humor_level=0` produces plain response without suffixes.

---

## 4. Optimization Opportunities

### Latency
- **Intent routing**: Replace the O(n) substring-match chain in `cmd_voice_run` with a precompiled trie or regex alternation for O(1) matching. Currently ~35 `in lowered` checks per command.
- **Health check polling**: `_health_loop` in the desktop widget polls every 8 seconds. Use exponential backoff (e.g., 2s when recently toggled, 30s when stable) to reduce latency on state changes while reducing steady-state load.
- **DNS resolution in `_is_safe_public_url`**: Cache DNS results with a short TTL (30s) to avoid re-resolving the same hosts during a single mission run, which fetches 12+ pages.

### Reliability
- **Daemon crash recovery**: `cmd_daemon_run` catches only `KeyboardInterrupt`. Any unhandled exception in `cmd_ops_autopilot` (e.g., corrupted JSON) crashes the entire daemon. Wrap the cycle body in a `try/except` that logs and continues.
- **Atomic writes on all state files**: `_save_missions` (learning_missions.py:73) uses direct `write_text`. Every state file should use the tmp+replace pattern.
- **File-lock on shared state**: The daemon, mobile API, and desktop widget all read/write `control.json`, `gaming_mode.json`, and dedupe files concurrently. Without file locking, concurrent writes can corrupt these files.

### Memory (24/7 runtime)
- **Nonce store pruning**: The nonce dict can hold up to 100k entries × ~200 bytes each ≈ 20MB. Reduce `REPLAY_WINDOW_SECONDS` to 60s (from 300s) and `MAX_NONCES` to 10k, since legitimate traffic is low-volume.
- **Desktop widget `output` buffer**: Trim to 500 lines as noted in M3. Current unbounded growth will consume ~50MB+ of tkinter text widget memory over weeks.
- **`MemoryStore` instantiation**: Both `_auto_ingest_memory` and `cmd_daemon_run` create new `MemoryStore(repo_root())` instances on each call/cycle. If `MemoryStore` holds file handles or caches, this leaks resources. Consider singleton or pass-through patterns.
- **Gaming process detection**: `_detect_active_game_process` spawns `tasklist.exe` and parses CSV output every daemon cycle (every 30-120s). On Windows, use `WMI` or `psutil` with process caching to avoid subprocess overhead.

### CPU
- **Orb animation**: 8.3 canvas redraws/second even when minimized. Gate behind visibility check (M4) to save ~3% CPU on low-end hardware.
- **Hotword detection polling**: `_hotword_loop` spawns a new PowerShell process every ~5 seconds to check for the wake word. This is ~17,000 process spawns per day. Consider using a persistent audio stream or a lighter-weight hotword library (e.g., `pvporcupine`).
