# Deep Bug Hunt + Optimization Review Results

## Files Reviewed
- `engine/src/jarvis_engine/mobile_api.py`
- `engine/src/jarvis_engine/desktop_widget.py`
- `engine/src/jarvis_engine/voice.py`
- `scripts/start-jarvis-services.ps1`

---

## Severity Summary
| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 CRITICAL | 4 | Security vulnerabilities, data corruption risks, thread safety issues |
| 🟠 HIGH | 11 | Performance problems, resource leaks, error handling gaps |
| 🟡 MEDIUM | 8 | Code quality, minor inefficiencies, missing validations |
| 🟢 LOW | 5 | Style issues, documentation gaps |

---

## 🔴 CRITICAL ISSUES

### C1: Subprocess Shell Injection Risk (desktop_widget.py:166-194, 197-215)
**Issue:** PowerShell script construction embeds timeout values directly.
**Impact:** Command injection if untrusted input reaches timeout_s parameter.
**Fix:** Enforce strict integer bounds (2-300) before embedding.

```python
# BEFORE (VULNERABLE):
f"$res = $r.Recognize([TimeSpan]::FromSeconds({int(max(2, timeout_s))}));"

# AFTER (SAFE):
timeout_int = int(max(2, min(300, timeout_s)))
f"$res = $r.Recognize([TimeSpan]::FromSeconds({timeout_int}));"
```

---

### C2: Thread-Safety Race in Nonce Cleanup (mobile_api.py:374-384)
**Issue:** Dictionary iteration during cleanup can race with modifications.
**Impact:** RuntimeError: dictionary changed size during iteration.
**Fix:** Atomic rebuild pattern with lock held throughout.

```python
# BEFORE:
stale = [key for key, seen_ts in nonce_seen.items() if seen_ts < cutoff]
for key in stale:
    nonce_seen.pop(key, None)

# AFTER:
with self.server.nonce_lock:
    original_len = len(nonce_seen)
    valid_nonces = {k: v for k, v in nonce_seen.items() if v >= cutoff}
    nonce_seen.clear()
    nonce_seen.update(valid_nonces)
```

---

### C3: Unbounded Regex Backtracking (voice.py:102)
**Issue:** Pattern `r"^\s*([a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+)\s+"` can backtrack.
**Impact:** ReDoS with crafted input to edge-tts output.
**Fix:** Remove trailing `\s+` or use `\s*$` anchor.

```python
# BEFORE:
match = re.match(r"^\s*([a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+)\s+", line)

# AFTER:
match = re.match(r"^\s*([a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+)", line)
```

---

### C4: PID Recycling Race (start-jarvis-services.ps1:78-90)
**Issue:** Process queried by PID may be recycled before termination.
**Impact:** Could terminate unrelated process.
**Fix:** Verify process identity immediately before kill.

```powershell
# Add identity verification wrapper function
function Stop-JarvisProcessSafely {
    param([int]$TargetPid, [string]$RepoRootNorm, [string]$PythonNorm)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetPid" -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return }
    # Re-verify command line matches before killing
    if ($proc.Name -eq "python.exe" -and $proc.CommandLine -match "jarvis_engine") {
        Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
    }
}
```

---

## 🟠 HIGH PRIORITY ISSUES

### H1: Missing Request Body Size Limit (mobile_api.py:386-455)
**Issue:** No size validation before HMAC computation on body.
**Fix:** Add early check: `if len(body) > MAX_AUTH_BODY_SIZE: return False`

### H2: Unbounded Thread Creation (desktop_widget.py:636)
**Issue:** Each async operation spawns a new daemon thread without pooling.
**Fix:** Use ThreadPoolExecutor(max_workers=4) with proper shutdown.

### H3: Path Traversal in output_wav (voice.py:164-171, 243)
**Issue:** User-controlled path resolved without directory containment check.
**Fix:** Validate resolved path is within allowed root directories.

### H4: Silent Exception Swallowing (desktop_widget.py:727-740)
**Issue:** Hotword loop catches all exceptions with `pass`.
**Fix:** Log with exponential backoff for repeated errors.

### H5: Weak Config File Permissions (start-jarvis-services.ps1:30-49)
**Issue:** Credential files created with inherited permissions.
**Fix:** Set explicit ACL allowing only current user access.

### H6: Inconsistent repo_root Patching (mobile_api.py:107-136)
**Issue:** Manual attribute patching without guaranteed restoration.
**Fix:** Use context manager pattern for safe patching.

### H7: Inefficient List Building (voice.py:99-105)
**Issue:** List created then converted to tuple; uses older Python patterns.
**Fix:** Use walrus operator and tuple() directly.

### H8: HTTP Error Body Resource Leak (desktop_widget.py:155-163)
**Issue:** Error response body may not close connection properly.
**Fix:** Use context manager: `with exc:` to ensure cleanup.

---

## 🟡 MEDIUM PRIORITY ISSUES

### M1: Missing Content-Type Validation (desktop_widget.py:131-152)
Bootstrap response parsed as JSON without checking Content-Type header.

### M2: Stringly-Typed Boolean Bug (mobile_api.py:91)
`bool("false")` returns `True` - need proper boolean parsing.

### M3: Unvalidated Profile Parameter (voice.py:67-81)
Arbitrary profile strings accepted without validation.

### M4: Duplicate Pattern Entry (voice.py:73)
`"en-GB"` appears twice in voice patterns list.

### M5: Unnecessary Range List (desktop_widget.py:745-767)
`range(16)` creates list in Python 2 (irrelevant in Py3 but style issue).

### M6: Race in Directory Creation (start-jarvis-services.ps1:27-28)
Two separate New-Item calls could race.

---

## 🟢 LOW PRIORITY ISSUES

### L1-L5: Type annotations, hardcoded colors, unused imports, path caching, documentation

---

## Recommended Fix Order

1. **Immediate (C1-C4):** Security and stability fixes
2. **This Week (H1-H4):** Resource management and error handling
3. **Next Sprint (H5-H8, M1-M6):** Hardening and code quality
4. **Backlog (L1-L5):** Style and documentation
