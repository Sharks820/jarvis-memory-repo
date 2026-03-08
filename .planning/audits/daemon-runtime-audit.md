# Daemon / Runtime / Widget Deep Optimization Audit

**Date:** 2026-03-07
**Scope:** `daemon_loop.py`, `runtime_control.py`, `process_manager.py`, `desktop_widget.py`, `widget_*.py`, `resilience.py`, `automation.py`, `ops_autopilot.py`, `gaming_mode.py`

---

## 1. Daemon Cycle Breakdown

### Main Loop Structure (`cmd_daemon_run_impl`)

The daemon runs an infinite `while True` loop in a single thread. Each cycle:

1. **Gather State** — resource snapshot, gaming mode, control state, idle detection
2. **Emit Status** — log cycle start, print status, log resource pressure
3. **Skip Check** — if paused or gaming mode enabled, sleep `max(idle_interval, 600)` seconds
4. **Periodic Subsystems** — frequency-gated tasks (see table below)
5. **Core Autopilot** — ops-autopilot pipeline (bootstrap, sync, brief, actions, automation)
6. **Circuit Breaker** — track consecutive failures, 5-min cooldown after 10 consecutive
7. **Sleep** — `sleep_seconds` (180s default, adjusted by resource pressure and idle state)

### Periodic Task Frequency Table

| Task | Frequency | Heavy? | Skippable? | Duration Estimate |
|---|---|---|---|---|
| **Missions (run pending)** | Every cycle (if enabled) | Yes | No | 5–30s (network + LLM) |
| **Mission auto-generate** | Every 50 cycles (~2.5hr) | Yes | Yes (pressure) | 2–10s |
| **Mobile-desktop sync** | Every 5 cycles (~15min) + cycle 1 | Medium | No | 1–5s |
| **Watchdog (service health)** | Every 5 cycles (~15min) | Light | No | <1s |
| **Self-heal** | Every 20 cycles (~1hr) + cycle 1 | Yes | Yes (pressure) | 3–15s |
| **Self-test (memory quiz)** | Every 20 cycles (~1hr) | Yes | Yes (pressure) | 5–20s |
| **KG regression check** | Every 10 cycles (~30min) | Medium | No | 1–5s |
| **Usage prediction** | Every 10 cycles (~30min) | Light | No | <1s |
| **DB optimize (ANALYZE)** | Every 100 cycles (~5hr) | Medium | Yes (pressure) | 2–10s |
| **DB optimize (VACUUM)** | Every 500 cycles (~25hr) | Heavy | Yes (pressure) | 10–60s |
| **Memory consolidation** | Every 50 cycles (~2.5hr) | Yes | Yes (pressure) | 5–30s |
| **Entity resolution** | Every 100 cycles (~5hr) | Yes | Yes (pressure) | 5–20s |
| **Auto-harvest** | Every 200 cycles (~10hr) | Yes | Yes (pressure) | 10–60s (network) |
| **Core autopilot** | Every cycle | Medium | No (but safe_mode guards) | 5–30s |

*Cycle time at 180s interval ≈ 3 min. All frequencies assume active mode.*

---

## 2. Stability Findings

### 2.1 ✅ Strengths (Solid Foundations)

- **Circuit breaker**: 10 consecutive autopilot failures → 5-min cooldown (not exit). Good.
- **Resource pressure management**: Three-level system (none/mild/severe) with throttling and heavy-task skipping. Well-designed.
- **Gaming mode**: Pauses daemon during gameplay. Auto-detect with 30s cache TTL on `tasklist` calls. Good.
- **PID file management**: Robust with file locking, PID reuse detection via creation timestamps, graceful+hard kill escalation. Excellent.
- **Conversation state persistence**: Saved on shutdown in `finally` block. Good.
- **Error handling**: Every periodic subsystem wrapped in broad exception handlers — individual failures don't crash the loop. Correct pattern.
- **Daemon bus caching**: Single CommandBus instance reused across cycles via `_get_daemon_bus()` with thread-safe lock. Good.
- **Watchdog**: Monitors `mobile_api` and restarts via subprocess if crashed. PID identity verified before action.

### 2.2 ⚠️ Concerns

#### CONCERN-D1: No Stuck-Iteration Watchdog
**Severity: Medium**
The daemon has no timeout on individual cycle iterations. If `_run_core_autopilot()` or any periodic task hangs (e.g., an LLM API call stuck in `urllib.urlopen`), the entire daemon stalls forever. The circuit breaker only triggers on nonzero return codes, not on time-outs.

**Recommendation:** Add a watchdog thread that monitors cycle completion time. If a cycle exceeds a threshold (e.g., 600s), log a warning and optionally kill the stuck operation. Alternatively, wrap the autopilot call with `threading.Timer` that sets a timeout flag.

#### CONCERN-D2: `_collect_kg_metrics` Opens Raw SQLite Connection
**Severity: Low**
In `_collect_kg_metrics()`, a raw `sqlite3.connect()` is used (lines in the function), and a `_KGShim` class wraps it. The connection is properly closed in a `finally` block. However, this creates a second connection to the same WAL-mode DB alongside whatever the daemon bus's MemoryEngine holds. Under heavy write load, this could cause SQLITE_BUSY.

**Recommendation:** Use the daemon bus's existing engine/KG connection instead of opening a new one. The bus context (`bus.ctx.kg`) should already have a live connection.

#### CONCERN-D3: `_dir_size_mb` in Resource Snapshot Is O(n) Directory Walk
**Severity: Low-Medium**
`capture_runtime_resource_snapshot()` calls `_dir_size_mb(root / ".planning" / "cache")` which does `rglob("*")` on the embedding cache directory. If the cache grows to thousands of files, this becomes expensive (I/O-bound directory scan) and runs **every cycle** (every 180s).

**Recommendation:** Cache the directory size result for a few cycles (e.g., recompute only every 5th cycle), or use a simpler heuristic (track file count + last-known size).

#### CONCERN-D4: `time.sleep()` in Main Loop Is Not Interruptible
**Severity: Low**
The daemon sleeps with `time.sleep(state["sleep_seconds"])` which can be up to 1800s under severe pressure. During this sleep, the daemon cannot respond to a `KeyboardInterrupt` promptly on Windows (Python's signal handling during sleep is platform-dependent).

**Recommendation:** Replace `time.sleep(N)` with a loop of shorter sleeps (e.g., `for _ in range(N): time.sleep(1); if stop_event.is_set(): break`). This pattern is already used in the widget's `_health_sleep()`.

#### CONCERN-D5: No Graceful Shutdown Signal Handler (Windows)
**Severity: Low-Medium**
The daemon relies on `KeyboardInterrupt` for shutdown. On Windows, `CTRL_C_EVENT` is the only signal mechanism, and the process manager's `_graceful_shutdown()` explicitly skips it on Windows (`return False`), falling through to hard kill. If the daemon is stopped via `kill_service()`, it gets hard-killed without running the `finally` cleanup (conversation state save, PID removal).

**Recommendation:** Register a `signal.signal(signal.SIGBREAK, handler)` on Windows (SIGBREAK is delivered by `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)`), or use a file-based shutdown sentinel that the daemon checks between cycles.

#### CONCERN-D6: Circuit Breaker Cooldown Blocks Everything
**Severity: Low**
When the circuit breaker opens (10 consecutive failures), it does `time.sleep(300)` — a 5-minute hard block. No periodic subsystems, watchdog, or health checks run during this time.

**Recommendation:** Make the cooldown interruptible (same sleep-loop pattern as CONCERN-D4), and consider running at least the watchdog during cooldown.

### 2.3 ✅ No Resource Leaks Found

- All SQLite connections in `_discover_harvest_topics` and `_collect_kg_metrics` are closed in `finally` blocks.
- The `BudgetManager` in `_run_auto_harvest_cycle` is closed in a `finally` block.
- All threads spawned by the widget are daemonic (`daemon=True`).
- The widget cancels `after()` callbacks during shutdown.
- Output text widget is capped at 500 lines (prevents unbounded growth).
- `_seen_event_ids` dict is capped at 500 entries with pruning to 400.

---

## 3. Efficiency Findings

### 3.1 Cycle Interval (180s)

**Verdict: Appropriate for the workload.**

- 180s gives ~480 cycles/day in active mode, ~96 in idle (900s).
- The cycle does real work (autopilot, sync, health checks) — not just polling.
- Idle mode (900s / 15min) is reasonable for inactive periods.
- Gaming mode correctly suspends cycles entirely.

### 3.2 ⚠️ Missions Run Every Cycle (When Enabled)

**Severity: Medium**
When `--run-missions` is set, `_run_missions_cycle()` attempts to run a pending mission on **every cycle**. Each mission run involves network I/O (web search, LLM calls). If missions keep failing, this creates unnecessary load every 3 minutes.

**Recommendation:** Add a backoff for failed missions (e.g., skip for 5 cycles after a failure). The retry mechanism (`retry_failed_missions`) already exists but runs only every 50 cycles.

### 3.3 ⚠️ Self-Heal Runs on Cycle 1

**Severity: Low**
Both self-heal and mobile-desktop sync run on cycle 1 (startup). Self-heal includes `brain_regression_report()` and potentially `run_memory_maintenance()`, which can be heavy. This delays the first useful autopilot cycle.

**Recommendation:** Defer self-heal to cycle 2 or 3 to improve startup responsiveness.

### 3.4 Resource Snapshot I/O

The resource snapshot writes a JSON file to disk every cycle. This is acceptable at 180s intervals but adds up:
- `capture_runtime_resource_snapshot()` — reads budgets file, stats process, walks cache dir
- `write_resource_pressure_state()` — writes JSON
- `read_control_state()` — reads JSON
- `read_gaming_mode_state()` — reads JSON

Total: ~4 file reads + 1 file write + 1 directory walk per cycle. Negligible at 180s.

### 3.5 `_process_usage()` with `psutil`

`psutil.Process.cpu_percent(interval=0.0)` returns instantaneous CPU — this is fine (non-blocking). Memory via `rss` is also instant. Good.

---

## 4. Widget Findings

### 4.1 ✅ Thread Safety

- All Tkinter mutations go through `self.after(0, ...)` from background threads. **Correct pattern.**
- `_health_loop` reads tkinter vars via `self.after(0, _read_cfg)` with `threading.Event` synchronization. **Correct.**
- `_hotword_loop_inner` reads `BooleanVar` via main-thread callback. **Correct.**
- Background HTTP workers are daemonic threads.

### 4.2 ✅ Animation Frame Rate

- Both `_animate_orb` and `_animate_launcher` use `self.after(33, ...)` → ~30fps.
- This is appropriate for a small UI. CPU impact is minimal (canvas coord updates, no heavy rendering).
- Animation stops if `stop_event` is set. Good.
- `_animate_launcher` skips if `launcher_canvas is None`. Good.

### 4.3 ⚠️ Health Polling Frequency

**Severity: Low**
`_health_loop` polls every ~8 seconds (16 × 0.5s sleep). This means:
- `/health` endpoint hit every 8s
- `/widget-status` endpoint hit every 8s (if authenticated)

For a local service, this is acceptable but slightly aggressive.

**Recommendation:** Increase to 15-20s. The orb animation already provides visual feedback; 8s polling adds little value over 15s.

### 4.4 ⚠️ `_refresh_services` Calls process_manager Every 10s

**Severity: Low**
`_refresh_services()` re-schedules itself every 10s via `self.after(10000, ...)`. It calls `list_services()` which reads up to 3 PID files from disk. Lightweight, but runs even when the panel is hidden (withdrawn).

**Recommendation:** Skip service refresh when panel is withdrawn to save I/O.

### 4.5 ✅ Widget Shutdown

- `_shutdown()` sets `stop_event`, stops tray icon, kills child services, cancels animation callbacks, joins daemon threads (1s timeout), destroys windows.
- Proper TclError catching throughout.
- `_confirm_exit()` shows a dialog before shutdown.

### 4.6 ⚠️ `_kill_child_services` During Shutdown

**Severity: Low**
On shutdown, the widget kills `mobile_api` and `daemon` processes. The fallback path spawns a `powershell` process with `Get-CimInstance`. This is slow (2-5s) but only runs on shutdown. Acceptable.

### 4.7 ✅ Memory Management

- Output text capped at 500 lines. Good.
- `_seen_event_ids` capped at 500 → pruned to 400. Good.
- Popout window properly nullifies references on close. Good.
- Position save debounced to 300ms. Good.

### 4.8 ⚠️ Tooltip Leak Risk

**Severity: Very Low**
`_Tooltip` creates/destroys `Toplevel` windows on hover. If the mouse enters rapidly (hundreds of times), there's a theoretical flood of `after()` callbacks. However, each `_schedule` cancels the previous one, so this is effectively de-bounced. No real risk.

---

## 5. Startup Optimization

### Current Boot Sequence

1. `_register_daemon_pid()` — write PID file (fast)
2. Initialize conversation state singleton (loads from disk)
3. Enter main loop
4. Cycle 1: gather state → emit status → skip check → run periodic subsystems (sync + self-heal on first cycle) → core autopilot

### Startup Analysis

- **CommandBus creation** (`get_bus()`) is deferred to first access via `_get_daemon_bus()`. ✅ Lazy.
- **Conversation state** is loaded eagerly on startup. This is fast (JSON file read).
- **Self-heal on cycle 1** is the main startup cost — can take 3-15s.
- **Mobile-desktop sync on cycle 1** adds 1-5s.

**Recommendation:**
- Move self-heal to cycle 2 (or later) so the first autopilot cycle runs faster.
- The first cycle should be: state gather + autopilot only, with subsystems deferred by 1 cycle.
- Consider printing a "daemon ready" marker after cycle 1 completes for health checks.

### Health Check

There is no explicit "all subsystems ready" health check endpoint for the daemon. The watchdog in `process_manager.py` only checks if the PID is alive, not if the daemon has completed initialization.

**Recommendation:** Write a `ready` sentinel file (e.g., `.planning/runtime/daemon_ready.json`) after the first successful cycle completes. External health checks can poll this file.

---

## 6. Resilience Module Assessment

### What `run_self_heal` Does:
1. Ensures mobile security config exists (generates token/signing_key if missing)
2. Runs `run_mobile_desktop_sync` (checks security, owner guard, memory stats)
3. Runs `brain_regression_report` (checks memory health)
4. Conditionally runs `run_memory_maintenance` (if regression detected or forced)
5. Scans recent logs for error patterns (HTTP 400, tracebacks, timeouts, auth failures)
6. Produces a report with status (ok/attention/error)

### Assessment:
- **Good coverage** of security config, memory health, and log scanning.
- **Missing:** No check for disk space, database file size, or WAL file bloat.
- **Missing:** No check for orphaned lock files or temp files.
- **Missing:** No check for stale runtime state files (e.g., `resource_pressure.json` with old timestamps).

---

## 7. Process Manager Assessment

### Strengths:
- File locking (`msvcrt.locking`) prevents TOCTOU on PID writes.
- PID reuse detection via `GetProcessTimes` (Windows) or `/proc/stat` (Linux).
- Graceful → hard kill escalation with 5s timeout.
- Clean watchdog pattern with callback-based restart.

### Concerns:
- **CONCERN-P1:** On Windows, `_graceful_shutdown` always returns `False` (skips CTRL_C_EVENT to avoid killing the console group). This means **every** service stop is a hard kill (`TerminateProcess`). Hard kills skip Python `finally` blocks and `atexit` handlers.

---

## 8. Specific Code Fixes

### FIX-1: Interruptible Sleep in Main Daemon Loop
```python
# In cmd_daemon_run_impl, replace:
time.sleep(state["sleep_seconds"])

# With:
_interruptible_sleep(state["sleep_seconds"], stop_event=None)  # or use KeyboardInterrupt check

# Implementation:
def _interruptible_sleep(seconds: int, *, check_interval: float = 1.0) -> None:
    """Sleep in small increments to allow faster KeyboardInterrupt response."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(check_interval, max(0, remaining)))
```

### FIX-2: Cache `_dir_size_mb` for Resource Snapshot
```python
# In runtime_control.py, add caching:
_dir_size_cache: dict[str, tuple[float, float]] = {}  # path -> (timestamp, size_mb)
_DIR_SIZE_CACHE_TTL = 600.0  # 10 minutes

def _dir_size_mb_cached(path: Path) -> float:
    key = str(path)
    now = time.monotonic()
    cached = _dir_size_cache.get(key)
    if cached and (now - cached[0]) < _DIR_SIZE_CACHE_TTL:
        return cached[1]
    size = _dir_size_mb(path)
    _dir_size_cache[key] = (now, size)
    return size
```

### FIX-3: Mission Failure Backoff
```python
# In _run_missions_cycle, add backoff tracking:
_mission_fail_backoff_until: float = 0.0

def _run_missions_cycle(root, cycles, skip_heavy_tasks):
    global _mission_fail_backoff_until
    if time.monotonic() < _mission_fail_backoff_until:
        print("mission_cycle_skipped=backoff")
        return
    try:
        mission_rc = _run_next_pending_mission()
    except (...) as exc:
        mission_rc = 2
        _mission_fail_backoff_until = time.monotonic() + 900  # 15min backoff
        ...
```

### FIX-4: Defer Self-Heal from Cycle 1
```python
# In _run_periodic_subsystems, change self-heal condition:
if self_heal_every_cycles > 0 and (cycles == 2 or cycles % self_heal_every_cycles == 0):
    ...  # Start on cycle 2 instead of 1
```

---

## 9. 8-Hour Soak Run Readiness Assessment

### ✅ Ready (No Blockers)
- Error isolation: all periodic tasks wrapped in exception handlers
- Resource pressure management: throttling + heavy-task skipping
- Circuit breaker: prevents cascading failure loops
- Memory management: no accumulating data structures found
- Database: WAL mode + periodic ANALYZE/VACUUM
- PID management: robust with reuse detection

### ⚠️ Risks for 8-Hour Run
| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Stuck LLM API call blocks daemon | High — no cycles run | Low | Add cycle timeout watchdog (CONCERN-D1) |
| `_dir_size_mb` grows expensive | Low — slight slowdown | Medium | Cache directory sizes (FIX-2) |
| Mission failures every cycle | Low — wasted CPU/network | Medium | Add backoff (FIX-3) |
| Hard kill on service stop | Medium — lost state | Medium | Windows limitation; accept or add sentinel file |
| Memory growth from daemon bus | Low | Low | Bus is singleton; MemoryEngine manages its own pools |

### Soak Run Monitoring Recommendations
1. Watch `resource_process_memory_mb` across cycles for monotonic growth
2. Monitor cycle duration (time between `cycle=N` and `cycle_rc=X` output lines)
3. Check for `consecutive_failures` lines in output
4. Verify `self_heal_cycle_rc=0` at regular intervals
5. After 8 hours, verify `kg_regression_status=ok`

---

## 10. Summary

The daemon loop is **well-architected** for long-running operation. Key strengths:
- Clean separation of concerns (each subsystem in its own function)
- Comprehensive error handling that prevents cascading failures
- Resource pressure management with automatic throttling
- Proper PID management with race condition protection

The widget is **thread-safe** and follows correct Tkinter patterns. Animations are efficient at 30fps. Health polling is slightly aggressive but not problematic.

**Top 3 Recommendations for REL-01 Compliance:**
1. **Add cycle timeout watchdog** — the single biggest risk for an 8-hour soak run is a stuck iteration with no timeout
2. **Make sleep interruptible** — allows faster shutdown and better responsiveness to state changes
3. **Cache directory size computation** — prevents I/O degradation as cache grows over time
