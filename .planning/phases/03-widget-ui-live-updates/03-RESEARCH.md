# Phase 3: Widget & UI Live Updates - Research

**Researched:** 2026-03-02
**Domain:** tkinter desktop widget, mobile API polling, activity feed, command output parsing
**Confidence:** HIGH

## Summary

The Jarvis desktop widget (`desktop_widget.py`, 3071 lines) is a tkinter-based GUI that communicates with the Jarvis engine exclusively through HTTP calls to the mobile API server. The widget has a health poll loop (~8 seconds), service status refresh (~10 seconds), and command processing via the `/command` endpoint. Brain growth metrics (facts, KG, missions, self-test) are already displayed via the `/widget-status` endpoint, but they only update on the health poll cycle -- there is no event-driven push mechanism.

The core problem is that the widget has no way to know when backend state changes between poll cycles. When a user cancels a learning mission (which currently lacks a dedicated command entirely), the Brain Growth section shows stale data until the next health poll (~8 seconds). Similarly, learning events (new facts, preferences, memories) only reflect on the next poll. There is also no "activity feed" UI component -- the existing "Activity" button fetches the last 20 events from `/activity` and dumps them into the conversation display, which is mixed in with user conversations and hard to distinguish.

**Primary recommendation:** Add a dedicated widget-facing event stream via polling `/widget-status` with an event diff mechanism (new events since last poll), wire mission cancel/complete/retry actions to trigger immediate dashboard refreshes, and add a dedicated scrollable activity feed panel (separate from the conversation display) with categorized, timestamped entries.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| UI-01 | Widget brain status updates live when missions cancelled/completed/retried | Currently NO cancel mission command exists anywhere. `_update_growth_labels()` reads mission data from `/widget-status` on ~8s poll. Need: (1) cancel/retry mission CLI commands, (2) immediate refresh after mission state change, (3) growth labels reflect new state within 1s |
| UI-02 | Widget learning indicator updates live for preferences, facts, memories | `_show_learned_indicator()` exists but only triggers on `memory_ingest`, `memory_forget`, `llm_conversation` intents. Does NOT trigger for preference learning, fact extraction, or KG changes. Need: broader intent detection + activity feed event-driven indicator |
| UI-03 | Activity feed in primary conversation display with real-time bot activity | `/activity` endpoint exists with SQLite-backed `ActivityFeed`. Widget already has `_view_activity_async()` but it dumps into conversation text. Need: dedicated UI panel with auto-refresh, not mixed into chat |
| UI-04 | Activity feed entries timestamped and categorized | `ActivityEvent` dataclass has `timestamp`, `category`, `summary`, `details`. 12 categories defined in `ActivityCategory`. Widget `_view_activity_async()` already formats as `[HH:MM:SS] [CATEGORY] summary`. Need: persistent panel with color-coded categories |
| UI-05 | Widget response display handles all command types cleanly | `_send_command_async()` worker parses `response=`, `reason=`, `error=`, `model=`, `provider=`, `intent=`, `source_*=` lines. Falls back to `[intent] ok=value` + raw stdout tail. Brain status (`cmd_brain_status`) prints structured `branch=` lines without `response=` prefix. Need: parser aware of brain_status, mission_status, system_status output formats |
</phase_requirements>

## Current Architecture Deep Dive

### Widget File Structure (desktop_widget.py, 3071 lines)

```
Lines 1-97:     Imports, DPAPI encryption helpers
Lines 98-298:   WidgetConfig, config load/save, DPAPI migration
Lines 300-460:  HMAC auth, HTTP helpers (_http_json, _http_json_bootstrap)
Lines 462-543:  Voice dictation (Whisper + System.Speech fallback)
Lines 545-607:  Toast notifications (Windows BalloonTip via PowerShell)
Lines 609-720:  Tray icon, tooltips, edge snapping
Lines 724-820:  JarvisDesktopWidget.__init__, state variables, startup
Lines 820-1090: Launcher orb (drag, release, tray icon)
Lines 1090-1178: System tray (pystray) menu and handlers
Lines 1179-1499: _build_ui() -- header, session, command input, flags,
                 buttons, quick actions, services, brain growth, conversation
Lines 1500-1578: Chat tag configuration (user, jarvis, system, error, etc.)
Lines 1580-1604: Clear history, end conversation
Lines 1605-1748: Pop-out conversation window
Lines 1749-1998: Toggle advanced, entry/check/btn helpers, command enter
Lines 1999-2052: Welcome message, learned indicator, thinking animation
Lines 2054-2498: Command processing (_send_command_async worker),
                 voice dictate, hotword loop, quick phrases
Lines 2501-2593: _refresh_services, _refresh_dashboard_async, _view_activity_async
Lines 2596-2678: Voice dictate, hotword loop
Lines 2680-2864: Health loop, _set_online, growth labels, intelligence label
Lines 2866-2930: State machine (idle/listening/processing/error)
Lines 2932-3066: Orb animation, launcher animation (30fps)
Lines 3069-3071: run_desktop_widget() entry point
```

### Health Poll Loop (_health_loop)

**Location:** Lines 2683-2781
**Cycle:** ~8 seconds (16 iterations x 0.5s sleep)
**What it does:**
1. Reads config from tkinter vars on main thread (thread-safe via `after(0, _read_cfg)`)
2. Polls `/health` (unauthenticated, fast-path in API)
3. If online + has auth tokens, fetches `/widget-status` (authenticated)
4. Calls `_set_online(ok, intel_data, growth_data)` on main thread

**What `/widget-status` returns:**
- `growth.metrics.facts_total`, `facts_last_7d`
- `growth.metrics.kg_nodes`, `kg_edges`
- `growth.metrics.memory_records`
- `growth.metrics.mission_count`, `active_missions` (list of {topic, status, findings})
- `growth.metrics.last_self_test_score`
- `growth.metrics.growth_trend`
- `alerts` (list of proactive alerts)

**Key gap:** The 8-second poll is the ONLY mechanism that updates Brain Growth labels. There is no way to force an immediate refresh after a command completes.

### Brain Growth Display (_update_growth_labels)

**Location:** Lines 2790-2838
**Labels:** Facts, KG Size, Memory, Missions, Self-Test, Trend
**Mission display:** Shows count + first 3 topic names, colored with ACCENT for active

### Learned Indicator (_show_learned_indicator)

**Location:** Lines 2010-2026
**Trigger:** After successful `/command` response, IF intent is `memory_ingest`, `memory_forget`, or `llm_conversation` (line 2433)
**Display:** Green italic "Learned" text in chat, auto-removed after 2 seconds

**Key gap:** Does NOT trigger for:
- Fact extraction (happens in daemon, not via /command)
- Preference learning (happens via LearnInteractionCommand in background)
- KG updates from harvesting or missions

### Command Processing Flow

```
_send_command_async()
  -> Set state "processing", show thinking, disable input
  -> Worker thread:
     -> POST /command with {text, execute, approve_privileged, speak, master_password, model_override}
     -> API calls cmd_voice_run() in-process, captures stdout
     -> Returns {ok, intent, reason, stdout_tail, error, ...}
  -> Parse stdout_tail lines for response=, reason=, error=, model=, etc.
  -> Display response in chat (or fall back to [intent] ok=value)
  -> Show learned indicator if intent matches
  -> Reset state to idle, re-enable input
```

### Existing Activity Feed

**Backend:** `activity_feed.py` -- SQLite-backed, singleton, thread-safe
- 12 categories: llm_routing, fact_extracted, correction_applied, consolidation, regression_check, daemon_cycle, proactive_trigger, harvest, web_research, voice, error, security
- `log_activity()` convenience function used in: main.py daemon loop, gateway/models.py, learning/engine.py, proactive/__init__.py
- `get_activity_feed().query(limit, category, since)` returns `ActivityEvent` list
- `get_activity_feed().stats()` returns 24h category counts

**Widget UI:** `_view_activity_async()` (lines 2555-2593)
- Button labeled "Activity" in the fetch row
- Fetches `GET /activity?limit=20`
- Dumps events into conversation chat as `[HH:MM:SS] [CATEGORY] summary`
- Mixed with conversation messages, no auto-refresh, no visual separation

### Missing: Cancel Mission Command

**Critical finding:** There is NO cancel mission command anywhere in the codebase.
- `learning_missions.py` has: `create_learning_mission`, `run_learning_mission`, `retry_failed_missions`, `auto_generate_missions`, `load_missions`, `_save_missions`
- `main.py` has: `cmd_mission_create`, `cmd_mission_run`, `cmd_mission_status`
- Commands module has: `MissionCreateCommand`, `MissionRunCommand`, `MissionStatusCommand`
- **No** `cancel_mission`, `MissionCancelCommand`, or status-update function

The user reports "cancelling a mission doesn't update the brain UI" -- this confirms the feature is either completely missing or uses an ad-hoc approach (manually editing missions.json).

### Mission Status Values

From `learning_missions.py`:
- `pending` -- waiting to run
- `running` -- currently executing (set during run_learning_mission)
- `completed` -- had verified findings
- `failed` -- no verified findings, retries < 2
- `exhausted` -- failed with retries >= 2

No `cancelled` status exists.

## Standard Stack

### Core (Already In Use)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| tkinter | stdlib | Desktop widget GUI | Already in use, no change needed |
| threading | stdlib | Background workers | `_thread()` pattern for all async work |
| urllib | stdlib | HTTP calls to mobile API | `_http_json()` wrapper |
| sqlite3 | stdlib | Activity feed storage | `ActivityFeed` class |

### No New Dependencies Needed

This phase is entirely within the existing widget + mobile API architecture. All changes are:
1. New CLI command for mission cancel (Python, no deps)
2. Widget UI modifications (tkinter, no deps)
3. Activity feed polling changes (existing infrastructure)
4. Command output parser improvements (string parsing, no deps)

## Architecture Patterns

### Pattern 1: Immediate Dashboard Refresh After Command

**What:** After `/command` completes, fetch `/widget-status` and update growth labels
**When to use:** Every command completion that could change brain state
**Example:**
```python
# In _send_command_async worker, after parsing response:
if ok and intent in ("memory_ingest", "memory_forget", "llm_conversation",
                      "brain_status", "mission_status", "mission_cancel"):
    try:
        ws = _http_json(cfg, "/widget-status", method="GET")
        growth_data = ws.get("growth") if isinstance(ws, dict) else None
        self.after(0, self._update_growth_labels, growth_data)
    except Exception:
        pass  # Best-effort refresh
```

### Pattern 2: Dedicated Activity Feed Panel

**What:** Replace the "dump into chat" activity display with a persistent, auto-refreshing panel
**When to use:** Activity feed UI (UI-03, UI-04)
**Design:**
```
+-------------------------------------------------+
| Brain Growth section (existing)                  |
+-------------------------------------------------+
| Activity Feed (new)  [Filter: All v]  [Refresh]  |
|   [12:34:56] [LLM] Routed to kimi-k2 via groq  |
|   [12:34:50] [DAEMON] Cycle 42 started          |
|   [12:34:45] [FACT] Extracted: "Conner uses..."  |
|   [12:34:30] [LEARN] New preference detected     |
|   ...scrollable...                               |
+-------------------------------------------------+
| Conversation (existing, below)                   |
+-------------------------------------------------+
```

**Implementation approach:**
- New `tk.LabelFrame` with `text="Activity Feed"` between Brain Growth and Conversation
- `tk.Text` widget (read-only) with tags for each category (color-coded)
- Compact display: `[HH:MM:SS] [CAT] summary` per line
- Auto-refresh: poll `/activity?limit=15&since=LAST_TS` on the existing health loop
- Max ~8 visible lines, scrollable

### Pattern 3: Event Diff Polling

**What:** Track `last_event_timestamp` and only fetch new events since last poll
**When to use:** Activity feed auto-refresh to avoid re-fetching all events
**Example:**
```python
# In _health_loop, after fetching /widget-status:
if ok and cfg.token and cfg.signing_key:
    try:
        since = getattr(self, '_last_activity_ts', None)
        url = f"/activity?limit=15"
        if since:
            url += f"&since={since}"
        act_data = _http_json(cfg, url, method="GET")
        events = act_data.get("events", [])
        if events:
            self._last_activity_ts = events[0].get("timestamp")
            self.after(0, self._append_activity_events, events)
    except Exception:
        pass
```

### Pattern 4: Mission Cancel Command

**What:** New CQRS command + CLI command + main.py keyword handler for cancelling missions
**Design:**
```python
# In commands/ops_commands.py:
@dataclass
class MissionCancelCommand:
    mission_id: str

# In handlers/ops_handlers.py:
def _handle_mission_cancel(cmd):
    missions = load_missions(root)
    for m in missions:
        if m.get("mission_id") == cmd.mission_id:
            m["status"] = "cancelled"
            m["updated_utc"] = datetime.now(UTC).isoformat()
            _save_missions(root, missions)
            return MissionCancelResult(ok=True, mission_id=cmd.mission_id)
    return MissionCancelResult(ok=False, error="Mission not found")

# In main.py cmd_voice_run keyword detection:
elif "cancel mission" in lowered or "stop mission" in lowered:
    # Extract mission topic and find matching mission
    intent = "mission_cancel"
```

### Pattern 5: Command Output Format Awareness

**What:** Extend the response parser in `_send_command_async` to handle structured commands
**When to use:** brain_status, mission_status, system_status output
**Problem:** These commands print structured lines (e.g., `branch=`, `mission_id=`) but no `response=` prefix, so the widget shows raw `[intent] ok=True` with last 6 stdout lines

**Solution:** Add response= prints to cmd_brain_status, cmd_mission_status, cmd_status in main.py, similar to how cmd_mission_status already has a `response=` line (line 1651). Brain status needs one. System status needs one.

### Anti-Patterns to Avoid

- **WebSocket/SSE for push updates:** Tkinter's event loop does not natively support WebSocket. Adding asyncio or websockets would add massive complexity for minimal gain over 2-3 second polling. NEVER add WebSocket to this widget.
- **Direct database access from widget:** The widget runs in a separate process from the daemon. It MUST go through the mobile API HTTP interface. Never import MemoryEngine or KnowledgeGraph directly.
- **Blocking HTTP in tkinter main thread:** All HTTP calls MUST use `_thread(worker)` pattern. Never block the main thread or the UI freezes.
- **Removing the health poll loop:** The existing poll loop is well-designed (8s cycle, fallback URLs, thread-safe var reads). Enhance it, don't replace it.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Event categories | Custom category system | `ActivityCategory` constants | Already defined, 12 categories |
| Activity storage | File-based log | `ActivityFeed` (SQLite) | Thread-safe, queryable, auto-prune |
| Colored text display | Custom drawing | tkinter Text widget tags | `tag_configure()` already used for chat |
| Thread-safe UI updates | Direct widget manipulation | `self.after(0, callback)` | Already the established pattern |
| HTTP with auth | Raw urllib calls | `_http_json(cfg, path)` | Handles HMAC, SSL, fallback URLs |

## Common Pitfalls

### Pitfall 1: Tkinter Thread Safety Violations
**What goes wrong:** Updating tkinter widgets from background threads causes crashes or silent corruption
**Why it happens:** tkinter is NOT thread-safe. Only the main thread can touch widgets.
**How to avoid:** Always use `self.after(0, callback)` or `self._log_async()` for UI updates from worker threads. The codebase already does this correctly -- follow the pattern.
**Warning signs:** `TclError: out of stack space` or `RuntimeError: main thread is not in main loop`

### Pitfall 2: Activity Feed Panel Eats Conversation Space
**What goes wrong:** Adding a fixed-height activity panel shrinks the conversation display
**Why it happens:** The widget is 470x840 with fixed-size sections already consuming most vertical space
**How to avoid:** Make the activity feed collapsible (toggle button) and keep it compact (6-8 lines max). Use a small font (9pt). Consider putting it in a `tk.PanedWindow` so the user can resize.
**Warning signs:** Conversation display becomes too small to be useful on smaller screens

### Pitfall 3: Stale Event Timestamps in Activity Polling
**What goes wrong:** Using `since=LAST_TS` misses events if two events have the same timestamp
**Why it happens:** ISO timestamps can have identical seconds for rapid events
**How to avoid:** Use `since=` for initial filtering, then deduplicate by `event_id` client-side
**Warning signs:** Missing activity events or duplicated entries

### Pitfall 4: Health Loop Becoming Too Expensive
**What goes wrong:** Adding activity polling to the health loop doubles API calls per cycle
**Why it happens:** `/widget-status` is already a combined endpoint but doesn't include recent activity events
**How to avoid:** Extend `/widget-status` to include recent activity events (e.g., `events_since` parameter) rather than making a separate `/activity` call every 8 seconds
**Warning signs:** Increased latency in health loop, rate limit hits

### Pitfall 5: Cancel Mission Without Confirmation
**What goes wrong:** User accidentally cancels a mission that was making progress
**Why it happens:** No undo mechanism for mission cancellation
**How to avoid:** The command handler should show the mission's current state (findings count) and ask for confirmation, OR make "cancelled" status re-activatable
**Warning signs:** Users complaining about lost mission progress

## Code Examples

### Current _update_growth_labels (reference)
```python
# Source: desktop_widget.py lines 2790-2838
# This is what needs to update faster after mission changes
def _update_growth_labels(self, growth_data):
    # ... parses growth_data["metrics"] ...
    missions = m.get("active_missions", [])
    mission_count = int(m.get("mission_count", ...))
    if mission_count > 0 and isinstance(missions, list) and missions:
        topics = [str(mi.get("topic", "?"))[:20] for mi in missions[:3]]
        self._growth_labels["missions"].config(
            text=f"{mission_count} active: {', '.join(topics)}", fg=self.ACCENT)
```

### Current Activity Fetch (reference)
```python
# Source: desktop_widget.py lines 2555-2593
def _view_activity_async(self):
    cfg = self._current_cfg()
    def worker():
        data = _http_json(cfg, "/activity?limit=20", method="GET")
        events = data.get("events", [])
        for evt in reversed(events):
            ts_short = ts_raw[11:19]
            cat = str(evt.get("category", ""))
            summary = str(evt.get("summary", ""))
            self._log_async(f"[{ts_short}] [{cat.upper()}] {summary}", role=role)
    self._thread(worker)
```

### Current Learned Indicator Trigger (reference)
```python
# Source: desktop_widget.py line 2433-2434
if ok and intent in ("memory_ingest", "memory_forget", "llm_conversation"):
    self.after(0, self._show_learned_indicator)
```

### Activity Feed Event Categories (reference)
```python
# Source: activity_feed.py lines 33-47
class ActivityCategory:
    LLM_ROUTING = "llm_routing"
    FACT_EXTRACTED = "fact_extracted"
    CORRECTION_APPLIED = "correction_applied"
    CONSOLIDATION = "consolidation"
    REGRESSION_CHECK = "regression_check"
    DAEMON_CYCLE = "daemon_cycle"
    PROACTIVE_TRIGGER = "proactive_trigger"
    HARVEST = "harvest"
    WEB_RESEARCH = "web_research"
    VOICE = "voice"
    ERROR = "error"
    SECURITY = "security"
```

### Places That Log Activity Events
```python
# Source: grep results across codebase
# main.py daemon loop:     DAEMON_CYCLE (cycle start, fact_extracted, harvest, web_research, error)
# gateway/models.py:       LLM_ROUTING (every model routing decision)
# learning/engine.py:      FACT_EXTRACTED (new facts learned)
# proactive/__init__.py:   PROACTIVE_TRIGGER (alert fired)
```

### Missing Categories for UI-02
The following events are NOT currently logged to ActivityFeed but should be:
- **Preference learned** -- PreferenceTracker detects a new preference
- **Memory consolidated** -- MemoryConsolidator runs
- **Mission completed/cancelled** -- Mission state changes
- **KG fact added** -- Direct KG additions (some are logged as FACT_EXTRACTED, but not all paths)

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Activity button dumps into chat | Activity events mixed with conversation | Hard to distinguish, no auto-refresh |
| 8s health poll is only growth update | No immediate refresh after commands | Stale data for up to 8 seconds |
| No cancel mission command | User cannot cancel missions at all | Feature completely missing |
| `brain_status` prints structured output | No `response=` line for widget | Widget shows raw `[brain_status] ok=True` |

## Open Questions

1. **Should the activity feed replace the "Activity" button or supplement it?**
   - What we know: Current button dumps 20 events into chat. A persistent panel would be separate.
   - Recommendation: Keep the button for "full activity log" dump, add a compact auto-refreshing panel for live monitoring. The button could switch to opening the feed in a pop-out window.

2. **How many activity categories should be shown by default?**
   - What we know: 12 categories exist, but `daemon_cycle` events fire every ~120s and would dominate
   - Recommendation: Filter out `daemon_cycle` and `regression_check` by default. Show: llm_routing, fact_extracted, correction_applied, proactive_trigger, harvest, error, security. Add a "Show All" toggle.

3. **Should `/widget-status` include recent activity events?**
   - What we know: Adding events to the existing combined endpoint avoids an extra HTTP call per poll cycle
   - Recommendation: YES. Add `recent_events` (last 10, excluding daemon_cycle) to `/widget-status` response. This is the most efficient approach.

## Sources

### Primary (HIGH confidence)
- `engine/src/jarvis_engine/desktop_widget.py` -- Full 3071-line widget source, read in its entirety
- `engine/src/jarvis_engine/mobile_api.py` -- API endpoints: /widget-status, /activity, /command, /health
- `engine/src/jarvis_engine/main.py` -- CLI commands: cmd_brain_status, cmd_mission_status, cmd_voice_run
- `engine/src/jarvis_engine/activity_feed.py` -- Full ActivityFeed implementation (275 lines)
- `engine/src/jarvis_engine/learning_missions.py` -- Mission lifecycle, NO cancel command exists

### Secondary (MEDIUM confidence)
- Grep analysis of `log_activity` callsites across entire codebase
- Grep analysis confirming no `cancel_mission` or `MissionCancelCommand` exists anywhere

## Metadata

**Confidence breakdown:**
- Current architecture understanding: HIGH -- read every relevant file in full
- Activity feed mechanism: HIGH -- read complete implementation
- Missing cancel command: HIGH -- confirmed via exhaustive grep
- UI layout recommendations: MEDIUM -- subjective design choices, need user validation
- Poll optimization: HIGH -- clear from health loop implementation

**Research date:** 2026-03-02
**Valid until:** 2026-04-02 (stable codebase, internal project)
