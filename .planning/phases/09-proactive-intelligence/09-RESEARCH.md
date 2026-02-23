# Phase 9: Proactive Intelligence and Polish - Research

**Researched:** 2026-02-23
**Domain:** Proactive triggers, wake word detection, cost reduction tracking, adversarial self-testing
**Confidence:** HIGH

## Summary

Phase 9 is the capstone phase that gives Jarvis the ability to act before being asked and to demonstrably self-improve while reducing costs. The four pillars are: (1) a proactive trigger system that monitors existing ops_sync data (bills, medications, calendar events) and fires time-based alerts, (2) wake word detection using openwakeword's pre-trained "hey_jarvis" model for hands-free activation, (3) cost reduction tracking that measures local vs cloud query percentage over time using the existing CostTracker SQLite database, and (4) adversarial self-testing that extends the existing golden task framework to quiz Jarvis on retained knowledge.

The codebase is well-prepared for this phase. The daemon loop (`_cmd_daemon_run_impl` in main.py) already runs periodic cycles with idle detection and can be extended with proactive checks. The `ops_sync.py` module already loads bills, medications, calendar events, and tasks. The `life_ops.py` module already has `suggest_actions()` which identifies due bills and medications. The `gateway/costs.py` CostTracker already logs every query with provider information. The `growth_tracker.py` already has a complete golden task evaluation framework with hash-chain integrity.

**Primary recommendation:** Compose existing modules -- do not build parallel systems. The proactive engine should wrap `suggest_actions()` output with time-awareness and notification delivery. Cost tracking extends CostTracker with a `local_vs_cloud_summary()` method. Adversarial testing reuses `run_eval()` with memory-recall golden tasks.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CONN-05 | Proactive assistance surfaces relevant info without being asked (bill due alerts, medication reminders, meeting prep) | ProactiveEngine class wraps ops_sync snapshot + life_ops suggest_actions with time-aware trigger rules and notification delivery via winotify + TTS |
| VOICE-05 | Wake word detection ("Jarvis") enables hands-free voice activation from across the room | openwakeword 0.6.0 has pre-trained "hey_jarvis" model (0.42 MB, runs on CPU via onnxruntime). Integrates with existing stt.py and desktop_widget.py hotword loop |
| INTL-05 | Progressive cost reduction: as local knowledge grows, more queries can be answered locally without cloud API calls | CostTracker.local_vs_cloud_summary() queries existing query_costs table grouping by provider. IntentClassifier already routes privacy/simple queries locally |
| GROW-03 | Adversarial self-testing periodically quizzes Jarvis on retained knowledge and alerts if recall accuracy drops | Extend growth_tracker.py with memory-recall golden tasks; schedule via daemon loop; alert via winotify if score drops below threshold |
| GROW-04 | Progressive cost reduction tracked: percentage of queries answered locally vs cloud API increases over time | Same CostTracker extension as INTL-05, with time-series snapshots stored in cost_reduction_history.jsonl for trend visualization |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openwakeword | 0.6.0 | Wake word detection | MIT license, pre-trained "hey_jarvis" model, runs on CPU, 0.42 MB model size, <80ms per frame |
| winotify | 1.1.0 | Windows toast notifications | Zero dependencies, pure Python, persistent notifications in Action Center, up to 5 action buttons |
| onnxruntime | (dep of openwakeword) | Neural network inference for wake word | Required by openwakeword on Windows (tflite not supported on Windows) |
| pyaudio | 0.2.14 | Microphone audio capture for wake word | Used by openwakeword examples, provides streaming audio frames at 16kHz |

### Supporting (already in codebase)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| sounddevice | (existing) | Alternative mic capture | Already used by stt.py record_from_microphone; can share audio stream |
| edge-tts | >=7.2.7 | TTS announcements for proactive alerts | Already in dependencies, used for voice output |
| sqlite3 | stdlib | Cost tracking database | Already used by CostTracker for query_costs table |
| numpy | >=1.26.0 | Audio array processing | Already in dependencies |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| openwakeword | Porcupine (Picovoice) | Higher accuracy but requires API key and is not MIT-licensed; limited free tier |
| openwakeword | Windows Speech Recognition (current _detect_hotword_once) | Already exists in desktop_widget.py but spawns a new PowerShell process every 2 seconds; very high latency and CPU overhead |
| winotify | win10toast | win10toast requires pywin32 dependency; winotify is zero-dependency |
| pyaudio | sounddevice | sounddevice is already installed but pyaudio is the openwakeword documented approach; either works for 16kHz mono capture |
| schedule library | APScheduler | APScheduler is more powerful but adds unnecessary complexity; daemon loop already handles periodic execution |
| schedule library | daemon loop (existing) | Daemon loop is already running periodic cycles -- proactive checks integrate directly into it without any new scheduler dependency |

**Installation:**
```bash
pip install openwakeword winotify pyaudio
```

Note: pyaudio may require a pre-built wheel on Windows. If pip install fails, use `pip install pipwin && pipwin install pyaudio` or download the wheel from https://www.lfd.uci.edu/~gohlke/pythonlibs/. Alternatively, reuse sounddevice (already installed) with openwakeword by converting sounddevice output to the format openwakeword expects (16-bit int16 PCM at 16kHz).

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
    proactive/
        __init__.py          # ProactiveEngine class
        triggers.py          # Time-based and event-based trigger rules
        notifications.py     # Windows toast + TTS notification delivery
    wakeword.py              # openwakeword integration, mic streaming loop
    gateway/
        costs.py             # Extended with local_vs_cloud_summary()
    growth_tracker.py        # Extended with memory-recall golden tasks
```

### Pattern 1: Proactive Trigger Engine
**What:** A rule-based engine that evaluates ops_sync snapshot data against time-aware trigger conditions and fires notifications.
**When to use:** Every daemon cycle (or a proactive-specific sub-cycle within the daemon).
**Example:**
```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

@dataclass
class TriggerRule:
    rule_id: str
    description: str
    check_fn: Callable[[dict], list[str]]  # snapshot -> list of alert messages
    cooldown_minutes: int = 60  # don't re-fire within this window

class ProactiveEngine:
    def __init__(self, rules: list[TriggerRule], notifier: Notifier):
        self._rules = rules
        self._notifier = notifier
        self._last_fired: dict[str, datetime] = {}

    def evaluate(self, snapshot: dict) -> list[str]:
        alerts = []
        now = datetime.now()
        for rule in self._rules:
            last = self._last_fired.get(rule.rule_id)
            if last and (now - last) < timedelta(minutes=rule.cooldown_minutes):
                continue
            messages = rule.check_fn(snapshot)
            if messages:
                self._last_fired[rule.rule_id] = now
                for msg in messages:
                    self._notifier.send(msg)
                    alerts.append(msg)
        return alerts
```

### Pattern 2: Wake Word Detection Loop (openwakeword)
**What:** A background thread continuously captures microphone audio and runs openwakeword inference. On detection, triggers voice dictation flow.
**When to use:** When wake word feature is enabled (toggle in desktop widget).
**Example:**
```python
import openwakeword
from openwakeword.model import Model
import pyaudio
import numpy as np

CHUNK = 1280  # 80ms at 16kHz
RATE = 16000
THRESHOLD = 0.5

def wake_word_loop(on_detected: Callable, stop_event: threading.Event):
    oww_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    audio = pyaudio.PyAudio()
    stream = audio.open(format=pyaudio.paInt16, channels=1,
                        rate=RATE, input=True, frames_per_buffer=CHUNK)
    try:
        while not stop_event.is_set():
            pcm = stream.read(CHUNK, exception_on_overflow=False)
            audio_array = np.frombuffer(pcm, dtype=np.int16)
            prediction = oww_model.predict(audio_array)
            for model_name, scores in oww_model.prediction_buffer.items():
                if scores[-1] > THRESHOLD:
                    oww_model.reset()
                    on_detected()
                    break
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()
```

### Pattern 3: Cost Reduction Tracking
**What:** Extend CostTracker with a method that computes local vs cloud query ratios over configurable time windows.
**When to use:** Dashboard display, daily briefing cost section, trend tracking.
**Example:**
```python
def local_vs_cloud_summary(self, days: int = 30) -> dict:
    cur = self._db.execute("""
        SELECT
            CASE WHEN provider = 'ollama' THEN 'local' ELSE 'cloud' END as category,
            COUNT(*) as count,
            SUM(cost_usd) as total_cost
        FROM query_costs
        WHERE ts >= datetime('now', ?)
        GROUP BY category
    """, (f"-{days} days",))
    local_count = 0
    cloud_count = 0
    cloud_cost = 0.0
    for row in cur.fetchall():
        if row["category"] == "local":
            local_count = row["count"]
        else:
            cloud_count = row["count"]
            cloud_cost = row["total_cost"] or 0.0
    total = local_count + cloud_count
    return {
        "period_days": days,
        "local_count": local_count,
        "cloud_count": cloud_count,
        "total_count": total,
        "local_pct": round(local_count / total * 100, 1) if total > 0 else 0.0,
        "cloud_cost_usd": cloud_cost,
    }
```

### Pattern 4: Adversarial Self-Testing
**What:** Extend golden task framework with memory-recall tasks that quiz Jarvis on knowledge it should have retained. Run periodically in daemon; alert if score drops.
**When to use:** Scheduled in daemon loop (e.g., every 50 cycles or once daily).
**Example:**
```python
# Memory-recall golden tasks test retained knowledge
# Format matches existing GoldenTask structure
MEMORY_RECALL_TASKS = [
    {
        "id": "recall_owner_name",
        "prompt": "What is the name of your owner?",
        "must_include": ["conner"]
    },
    {
        "id": "recall_device_primary",
        "prompt": "What is your primary device?",
        "must_include": ["windows", "desktop"]
    },
    # ... dynamically generated from ingested knowledge
]
```

### Anti-Patterns to Avoid
- **Polling too frequently for proactive checks:** Don't check every second. Daemon cycles (30s-300s) are sufficient. Medication reminders only need 5-minute granularity.
- **Running wake word detection in the main thread:** Always run in a daemon background thread. The 80ms frame processing must not block the UI or command processing.
- **Building a separate scheduler:** The daemon loop already runs periodic cycles. Add proactive checks as a hook in the existing loop, not a parallel scheduling system.
- **Storing cost history in a separate database:** The query_costs table in CostTracker already has all the data needed. Add summary methods, don't create a second data store.
- **Training a custom "Jarvis" wake word model:** The pre-trained "hey_jarvis" model works out of the box. Training a custom single-word "Jarvis" model would require significant ML effort and data collection for marginal benefit.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Wake word detection | Custom audio ML pipeline | openwakeword 0.6.0 with "hey_jarvis" model | Two-stage neural network architecture with verifier; 100K+ parameters trained on 200K synthetic clips. ~0.42 MB model. |
| Windows desktop notifications | Custom Win32 API calls or PowerShell scripts | winotify 1.1.0 | Zero-dependency, pure Python, supports action buttons, persistent in Action Center |
| Microphone audio streaming | Custom audio buffer management | pyaudio or sounddevice | Handles sample rate conversion, buffer management, device enumeration |
| Cost aggregation SQL | Manual file-based tracking | CostTracker.local_vs_cloud_summary() | SQLite already has all query costs with timestamps and provider info |
| Eval hash-chain integrity | Custom hash verification | growth_tracker.validate_history_chain() | Already implements SHA-256 chain with prev_run_sha256 linking |

**Key insight:** Almost every capability needed in Phase 9 exists in embryonic form. The proactive system is `suggest_actions()` plus time-awareness plus notification delivery. Wake word replaces the PowerShell-based `_detect_hotword_once()` with a proper ML model. Cost tracking adds a summary method to an existing SQLite table. Adversarial testing extends an existing framework.

## Common Pitfalls

### Pitfall 1: Wake Word Continuously Using CPU
**What goes wrong:** openwakeword runs inference on every 80ms audio frame, consuming constant CPU even when idle.
**Why it happens:** The model processes audio continuously to detect the wake word.
**How to avoid:** Enable openwakeword's built-in Silero VAD (Voice Activity Detection) with `vad_threshold=0.5`. This skips inference on silent frames. Also, the model is tiny (0.42 MB) and designed for embedded devices -- a single core of a Raspberry Pi 3 runs 15-20 models simultaneously. On a desktop PC, CPU impact is negligible (~1-2%).
**Warning signs:** Task Manager shows sustained CPU usage from the Jarvis process even when nobody is speaking.

### Pitfall 2: Notification Spam from Proactive Alerts
**What goes wrong:** Same alert fires every daemon cycle (30-300 seconds), flooding the user with repeated notifications.
**Why it happens:** No cooldown or deduplication on trigger rules.
**How to avoid:** Every trigger rule must have a cooldown period (minimum 60 minutes for most alerts). Store last-fired timestamps per rule. Use content hashing to deduplicate identical alerts within the cooldown window.
**Warning signs:** Windows Action Center fills with dozens of identical "Bill due" notifications.

### Pitfall 3: PyAudio Installation Fails on Windows
**What goes wrong:** `pip install pyaudio` fails because it requires PortAudio C library compilation.
**Why it happens:** Windows doesn't have PortAudio development headers by default.
**How to avoid:** Use `pip install pipwin && pipwin install pyaudio` or download a pre-built wheel. Alternatively, use sounddevice (already installed) instead of pyaudio -- sounddevice wraps PortAudio and has pre-built wheels for Windows. Convert sounddevice float32 output to int16 for openwakeword: `(audio * 32767).astype(np.int16)`.
**Warning signs:** `error: Microsoft Visual C++ 14.0 or greater is required` during pip install.

### Pitfall 4: Adversarial Self-Tests Show False Regressions
**What goes wrong:** Memory recall scores drop not because Jarvis forgot, but because the local LLM gives slightly different phrasing.
**Why it happens:** `score_text()` uses exact word boundary matching (`\b{token}\b`), which is brittle against synonyms or rephrasing.
**How to avoid:** Use broader must_include tokens (e.g., "conner" not "Conner is my owner"). Accept partial coverage (e.g., 70% threshold vs 100%). Consider adding a semantic similarity check for LOW-confidence matches.
**Warning signs:** Score fluctuates significantly between identical runs.

### Pitfall 5: Cost Reduction Metric Appears Flat
**What goes wrong:** local_pct doesn't increase over time even though Jarvis is learning.
**Why it happens:** IntentClassifier routes based on query complexity, not knowledge availability. A complex query always goes to cloud regardless of local knowledge.
**How to avoid:** Track the metric for simple_private route queries specifically (these are the ones that can shift from cloud to local). Also track "queries that could have gone to cloud but were answered locally" as a separate metric.
**Warning signs:** local_pct stays at the same value for weeks.

### Pitfall 6: openwakeword and faster-whisper Fighting Over Microphone
**What goes wrong:** Both the wake word loop and the STT transcription try to open the microphone simultaneously, causing PortAudio errors.
**Why it happens:** Only one audio stream can be active on most Windows audio devices at a time (exclusive mode).
**How to avoid:** When wake word is detected, STOP the wake word audio stream, THEN start the STT recording, THEN restart the wake word stream after transcription completes. Use a shared microphone lock.
**Warning signs:** `PortAudioError: [Errno -9999] Unanticipated host error` or silent recordings.

## Code Examples

### Windows Toast Notification (winotify)
```python
# Source: winotify PyPI documentation
from winotify import Notification

def send_proactive_alert(title: str, message: str, icon_path: str = "") -> None:
    """Send a Windows toast notification for a proactive alert."""
    toast = Notification(
        app_id="Jarvis",
        title=title,
        msg=message,
        icon=icon_path if icon_path else "",
        duration="long",  # "short" (7s) or "long" (25s)
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()
```

### openwakeword with sounddevice (alternative to pyaudio)
```python
# Using sounddevice (already installed) instead of pyaudio
import sounddevice as sd
import numpy as np
from openwakeword.model import Model

def wake_word_loop_sd(on_detected, stop_event):
    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    CHUNK = 1280
    RATE = 16000

    def audio_callback(indata, frames, time_info, status):
        if stop_event.is_set():
            raise sd.CallbackAbort
        # Convert float32 to int16 for openwakeword
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
        prediction = model.predict(audio_int16)
        for name, scores in model.prediction_buffer.items():
            if scores[-1] > 0.5:
                model.reset()
                on_detected()
                break

    with sd.InputStream(samplerate=RATE, channels=1, blocksize=CHUNK,
                        dtype='float32', callback=audio_callback):
        stop_event.wait()
```

### Proactive Trigger Rules (composing existing ops_sync data)
```python
from datetime import datetime, date
from jarvis_engine.life_ops import load_snapshot, suggest_actions

def check_medication_reminders(snapshot_data: dict) -> list[str]:
    """Check if any medications are due within the next 30 minutes."""
    now = datetime.now()
    alerts = []
    for med in snapshot_data.get("medications", []):
        due_time = med.get("due_time", med.get("time", ""))
        if not due_time:
            continue
        # Parse HH:MM format
        try:
            hour, minute = map(int, due_time.split(":"))
            due_dt = now.replace(hour=hour, minute=minute, second=0)
            diff_minutes = (due_dt - now).total_seconds() / 60
            if 0 <= diff_minutes <= 30:
                name = med.get("name", "Unknown medication")
                dose = med.get("dose", med.get("dosage", ""))
                alerts.append(f"Medication reminder: {name} ({dose}) due at {due_time}")
        except (ValueError, TypeError):
            continue
    return alerts

def check_bill_due_soon(snapshot_data: dict) -> list[str]:
    """Check for bills due today or tomorrow."""
    today = date.today().isoformat()
    alerts = []
    for bill in snapshot_data.get("bills", []):
        status = str(bill.get("status", "")).lower()
        if status in {"due", "overdue"}:
            name = bill.get("name", "Unknown bill")
            amount = bill.get("amount", 0)
            alerts.append(f"Bill due: {name} (${float(amount):.2f})")
    return alerts

def check_calendar_prep(snapshot_data: dict) -> list[str]:
    """Check for upcoming calendar events needing preparation."""
    alerts = []
    for event in snapshot_data.get("calendar_events", []):
        if str(event.get("prep_needed", "")).lower() in {"yes", "true", "1"}:
            title = event.get("title", "Untitled event")
            time_str = event.get("time", "")
            alerts.append(f"Meeting prep needed: {title} at {time_str}")
    return alerts
```

### Cost Reduction Trend Tracking
```python
def cost_reduction_snapshot(cost_tracker, history_path: Path) -> dict:
    """Take a daily snapshot of local vs cloud query ratio for trend tracking."""
    summary_7d = cost_tracker.local_vs_cloud_summary(days=7)
    summary_30d = cost_tracker.local_vs_cloud_summary(days=30)

    snapshot = {
        "date": date.today().isoformat(),
        "7d_local_pct": summary_7d["local_pct"],
        "30d_local_pct": summary_30d["local_pct"],
        "7d_cloud_cost_usd": summary_7d["cloud_cost_usd"],
        "30d_cloud_cost_usd": summary_30d["cloud_cost_usd"],
        "7d_total_queries": summary_7d["total_count"],
        "30d_total_queries": summary_30d["total_count"],
    }
    # Append to JSONL file (same pattern as growth_tracker history)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=True) + "\n")
    return snapshot
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| PowerShell SpeechRecognitionEngine for hotword | openwakeword ML-based wake word detection | 2023-2024 | Orders of magnitude more accurate, runs continuously with minimal CPU, proper neural network vs grammar-based matching |
| Custom notification scripts | winotify for Windows toast | 2022 | Zero-dependency, persistent in Action Center, supports action buttons |
| Manual cost tracking | SQLite-backed CostTracker with automatic per-query logging | Already in codebase (Phase 3) | Just needs summary method for local-vs-cloud ratio |
| No proactive alerts | Time-aware trigger rules composing ops_sync data | Phase 9 (new) | Transforms Jarvis from reactive to proactive assistant |

**Deprecated/outdated:**
- `_detect_hotword_once()` in desktop_widget.py: Spawns a new PowerShell process with System.Speech.Recognition.SpeechRecognitionEngine every 2 seconds. Very high latency (2s minimum), high CPU, poor accuracy. Replace with openwakeword continuous streaming.
- win10toast: Requires pywin32, hasn't been updated since 2020. winotify is the modern replacement.

## Open Questions

1. **Should "Jarvis" (single word) or "Hey Jarvis" (phrase) be the wake word?**
   - What we know: openwakeword has a pre-trained "hey_jarvis" model. The requirement says "Jarvis" from across the room. A single word has higher false-positive rates than a two-word phrase.
   - What's unclear: Whether the user expects to say just "Jarvis" or "Hey Jarvis."
   - Recommendation: Use "Hey Jarvis" with the pre-trained model. It matches the Iron Man convention, has lower false positives, and avoids custom model training. The desktop widget UI can say "Say 'Hey Jarvis' to activate."

2. **Should proactive alerts interrupt the user with TTS or be notification-only?**
   - What we know: TTS is available via edge-tts. Windows toast notifications are non-intrusive. The user may not want spoken interruptions during gaming or focus time.
   - What's unclear: User preference for notification modality.
   - Recommendation: Default to Windows toast notification only. Add opt-in TTS announcement for high-priority alerts (overdue bills, missed medications). Respect gaming mode and daemon_paused state -- never alert during those.

3. **How to generate memory-recall golden tasks dynamically?**
   - What we know: The golden task framework uses a static JSON file of tasks. Memory recall should test knowledge that Jarvis has actually ingested.
   - What's unclear: How to programmatically generate must_include tokens from the memory database.
   - Recommendation: Maintain a separate `memory_recall_tasks.json` alongside the existing golden tasks. Start with static tasks about known facts (owner name, device, preferences). In a future iteration, auto-generate from high-confidence memory entries.

## Sources

### Primary (HIGH confidence)
- openwakeword PyPI page -- version 0.6.0, dependencies, Windows support confirmed
- openwakeword GitHub README -- pre-trained models list including "hey_jarvis", API details
- openwakeword docs/models/hey_jarvis.md -- model architecture (102,849 params, 0.42 MB), training data details
- openwakeword examples/detect_from_microphone.py -- streaming detection pattern, chunk size 1280, threshold 0.5
- winotify PyPI page -- version 1.1.0, zero dependencies, action buttons, Windows 10+ support
- Existing codebase: ops_sync.py, life_ops.py, gateway/costs.py, growth_tracker.py, desktop_widget.py, stt.py, gateway/classifier.py, gateway/models.py

### Secondary (MEDIUM confidence)
- Picovoice wake word guide 2026 -- confirms openwakeword as a viable open-source option alongside commercial alternatives
- Neural Engineer Medium article -- evaluation of openwakeword false reject performance, confirms reasonable accuracy
- openwakeword community reports -- false-accept target <0.5/hour, false-reject target <5%

### Tertiary (LOW confidence)
- openwakeword "just Jarvis" single-word detection -- the pre-trained model is "hey jarvis" (two words). Unverified community reports suggest it may respond to just "jarvis" with higher false-reject rates.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - openwakeword verified on PyPI, winotify verified on PyPI, all APIs documented
- Architecture: HIGH - composing existing codebase modules with well-understood patterns
- Pitfalls: HIGH - common issues well-documented in openwakeword issues and community
- Wake word detection: MEDIUM - pre-trained model exists but no published accuracy metrics for "hey_jarvis"
- Cost reduction tracking: HIGH - extends existing CostTracker with straightforward SQL aggregation

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (stable domain, libraries are mature)
