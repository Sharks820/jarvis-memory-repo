# Deep Voice Pipeline Audit — Complete Analysis

**Date**: 2026-03-07  
**Scope**: All voice/STT files in `engine/src/jarvis_engine/`  
**Finding**: The pipeline architecture is sound but has **11 root causes** for poor voice understanding.

---

## 1. Complete Voice Pipeline Flow Diagram

```
[Microphone] (sounddevice, 16kHz, mono, float32)
      │
      ▼
[Optional: Wake Word Detection] (openwakeword "hey_jarvis", Silero VAD pre-filter)
      │  pause wake word stream ──→ resume after STT
      ▼
[Optional: Drain stale audio] (configurable drain_seconds, default=0.0)
      │
      ▼
[VAD Loop] (Silero VAD @ 32ms chunks, threshold=0.5, OR RMS energy fallback)
      │  silence_duration=2.0s after speech → stop recording
      │  min_recording=0.5s, max=30s
      ▼
[Audio Preprocessing] (stt_postprocess.preprocess_audio)
      │  1. Peak normalize to -3dBFS
      │  2. HPSS for speech >5s (skip for short commands!)
      │  3. noisereduce spectral denoising (prop_decrease=0.6, stationary)
      │  4. librosa silence trim (top_db=30)
      │  5. Re-normalize
      ▼
[4-Tier STT Fallback Chain] (JARVIS_STT_BACKEND="auto")
      │  1. Parakeet TDT 0.6B (onnx-asr, local, 6.05% WER)
      │  2. Deepgram Nova-3 (cloud, keyword boosting, REST batch)
      │  3. Groq Whisper Turbo (cloud, free tier)
      │  4. faster-whisper large-v3 (local emergency)
      │
      │  Confidence threshold: 0.6 — if above, accept immediately
      │  If below, try next backend, keep best_so_far
      ▼
[Post-Processing Pipeline] (stt_postprocess.postprocess_transcription)
      │  1. Hallucination detection (exact/substring/repetition/compression)
      │  2. Foreign prefix stripping
      │  3. Filler word removal (um, uh, er, ah, hmm + "you know", "I mean")
      │  4. LLM post-correction (skipped for short high-conf commands)
      │  5. NER entity correction via jellyfish/metaphone
      ▼
[Intent Classification] (voice_pipeline._classify_and_route)
      │  IntentClassifier (embeddings) → route + model selection
      │  Privacy keywords → force local Ollama
      ▼
[Dispatch Table] (voice_intents._DISPATCH_RULES, ~40 rules)
      │  First-match on lowered text
      │  Fallback: _web_augmented_llm_conversation
      ▼
[Command Execution / LLM Response]
      │  Context: memories + KG facts + cross-branch + preferences
      ▼
[TTS Output] (edge-tts neural voices, streamed for >180 chars)
      │  Markdown stripped, URL shortened
      │  Fallback: Windows SAPI
```

---

## 2. Per-Component Quality Assessment

| Component | Quality | Notes |
|---|---|---|
| **VAD (Silero)** | ⚠️ FAIR | Good model, but threshold=0.5 is too high for soft speech; 2s silence timeout is too long for commands |
| **Audio Preprocessing** | ✅ GOOD | Normalize, denoise, trim — properly skips HPSS for short commands |
| **Parakeet TDT 0.6B** | ⚠️ FAIR | Good WER but no vocabulary injection, no confidence from logprobs in most cases |
| **Deepgram Nova-3** | ⚠️ FAIR | Missing critical API parameters; keyword list too small |
| **Groq Whisper** | ✅ OK | Prompt-limited (224 tokens), good fallback |
| **faster-whisper** | ✅ GOOD | Proper VAD params, beam_size=5, good as emergency |
| **Post-processing** | ✅ GOOD | Hallucination detection, fillers, LLM correction, NER |
| **Intent Routing** | ⚠️ FAIR | String-matching only; no fuzzy matching for misheard commands |
| **Personal Vocab** | ❌ POOR | Only 19 entries; missing common personal names, addresses, apps |
| **Wake Word** | ✅ OK | openwakeword with VAD pre-filter, cooldown, drain |
| **Telemetry** | ✅ GOOD | Full pipeline instrumentation, SLO tracking |
| **Error Handling** | ✅ GOOD | Graceful fallbacks, retry on 5xx/429, transport errors |

---

## 3. ROOT CAUSES for Poor Voice Understanding (Ranked by Impact)

### 🔴 CRITICAL (High Impact)

#### RC-1: VAD Silence Timeout is 2.0 Seconds — Way Too Long
**File**: `stt_backends.py`, line 345  
**Problem**: `silence_duration=2.0` means the system waits 2 full seconds of silence after speech before stopping. Users expect ~0.7-1.0s response time for command assistants. This makes the bot feel sluggish and unresponsive.  
**But worse**: During those 2 seconds, background noise can be interpreted as "still speaking," causing the system to record environmental noise that corrupts the transcription.  
**Fix**: Change `silence_duration` default to `1.0` (or 0.8 for a snappy experience). Add environment variable `JARVIS_VAD_SILENCE_DURATION` for tuning.

#### RC-2: VAD Threshold 0.5 Misses Soft Speech and False-Triggers on Noise
**File**: `stt_vad.py`, line 99: `threshold: float = 0.5`  
**Also**: `stt_backends.py` calls `get_vad_detector(sampling_rate=sample_rate)` using default threshold=0.5  
**Problem**: Threshold 0.5 is Silero's generic default. For a personal voice assistant in a home/office environment:
- Soft-spoken commands ("hey jarvis, what time is it") may not trigger onset detection
- TV/music in the background WILL trigger false speech detection, extending the recording with noise
- The same threshold is used for onset AND offset — these should be different (onset should be more sensitive, offset should tolerate brief pauses)

**Fix**:
- Set onset threshold to 0.35 (more sensitive to catch soft speech)
- Set offset threshold to 0.45 (less likely to cut off mid-sentence on brief pauses)
- Or at minimum, lower the global threshold to 0.4

#### RC-3: No "Hangover" / Speech Padding in VAD
**File**: `stt_backends.py`, `_capture_audio_loop()`  
**Problem**: When VAD detects silence after speech, it stops immediately counting silence frames. There's no "hangover time" — a brief 200-300ms buffer after the last speech frame to catch trailing consonants and word endings. This causes clipping of the last word.  
**Evidence**: The `min_recording_chunks = 0.5s` ensures at least 500ms is captured, but that doesn't help if the user says "Jarvis, set a timer for five minutes" and the VAD cuts off "minutes" at "minut-".  
**Fix**: Add `speech_pad_chunks` (e.g., 10 chunks = 320ms at 32ms/chunk) that continue recording after the first silence frame before starting the silence counter.

#### RC-4: Deepgram Missing Critical API Parameters
**File**: `stt_backends.py`, `_build_deepgram_params()`, line ~155  
**Current params**: `model=nova-3, language=en, punctuate=true, smart_format=true, keywords=[...]`  
**Missing params that dramatically improve accuracy**:
- **`utterances=true`** — Deepgram's built-in utterance segmentation
- **`endpointing=300`** — Tell Deepgram to wait 300ms for speech pause (instead of default 10ms)
- **`filler_words=false`** — Let Deepgram strip fillers natively (more accurate than regex)
- **`numerals=true`** — Convert "five" to "5" for timer/alarm commands
- **`detect_language=false`** (explicitly) — Prevent unnecessary language detection overhead
- **`model=nova-3` with `version=latest`** — Ensure latest model revision

**Critical**: The `keywords` parameter should use `keywords=term:intensifier` format for boosting:
```python
# Current: params.append(("keywords", kt))
# Should be: params.append(("keywords", f"{kt}:2"))  # boost by factor 2
```
Deepgram's keyword boosting is FAR more effective with intensity values (1-5 scale).

#### RC-5: Personal Vocabulary is Pathetically Small (19 entries)
**File**: `engine/src/jarvis_engine/data/personal_vocab.txt`  
**Current content**: Only 19 generic terms (Jarvis, Ollama, Groq, SQLite, etc.)  
**Missing**: 
- User's actual name pronunciation variants ("Conner" is there, good)
- Contact names (friends, family, coworkers)
- Street/neighborhood names the user mentions
- App names commonly used ("Spotify", "Discord", "Steam", etc.)
- Technical terms specific to user's work
- Custom command phrases
- Local business names, favorite restaurants

**Impact**: Every proper noun not in the vocabulary will be misheard. "Hey Jarvis, text Mom" → "Hey Jarvis, text ma'am". This is the #1 usability complaint with all voice assistants.

### 🟡 MODERATE (Medium Impact)

#### RC-6: No Pre-Speech Audio Buffering (Clipped Beginnings)
**File**: `stt_backends.py`, `_capture_audio_loop()`  
**Problem**: Recording only begins when VAD first detects speech. But speech onset detection has latency — by the time Silero VAD processes the 32ms chunk and returns `True`, the beginning of the word may already be partially missed.  
**Fix**: Implement a **ring buffer** (300-500ms) that always keeps the last N chunks. When speech is first detected, prepend the ring buffer contents to the recording. This captures the full onset of the first word.

```python
# Add ring buffer before main loop
ring_buffer = collections.deque(maxlen=int(0.4 / chunk_duration))  # 400ms

for i in range(max_chunks):
    chunk, _ = stream.read(samples_per_chunk)
    
    if not speech_detected:
        ring_buffer.append(chunk.copy())
    
    is_speech = _detect_speech(...)
    
    if is_speech and not speech_detected:
        # First speech frame — prepend ring buffer
        frames.extend(ring_buffer)
        ring_buffer.clear()
        speech_detected = True
    
    if speech_detected:
        frames.append(chunk.copy())
    # ... rest of silence detection
```

#### RC-7: No Adaptive Noise Floor
**File**: `stt_backends.py`  
**Problem**: The RMS fallback uses a fixed `silence_threshold=0.01`. Even with Silero VAD, there's no adaptation to the ambient noise level. In a noisy room, everything sounds like speech. In a quiet room, even breathing might trigger.  
**Fix**: Before starting to listen for speech, measure 500ms-1s of ambient noise to calibrate the noise floor. Silero VAD handles this somewhat internally, but when it falls back to RMS energy, the fixed threshold is inadequate.

#### RC-8: `drain_seconds=0.0` Default — Wake Word Remnants Corrupt Transcription
**File**: `stt_backends.py`, line 346: `drain_seconds: float = 0.0`  
**Problem**: When the wake word detector triggers and hands off to the STT recorder, the OS audio buffer may still contain the "hey jarvis" utterance. With `drain_seconds=0.0`, this remnant is included in the recording, causing the STT to transcribe "Hey Jarvis, set a timer" instead of just "set a timer". The intent matcher then has to strip the wake word — but if it's slightly garbled, it won't match.  
**Evidence**: The proactive handler uses `drain_seconds=0.3` (line 165 of `proactive_handlers.py`), but the default is 0.0.  
**Fix**: Change default to `drain_seconds=0.3`. The wake word detector should also pass this value when handing off to STT.

#### RC-9: Intent Matching is Pure String Containment — No Fuzzy Matching
**File**: `voice_intents.py`, `_DISPATCH_RULES`  
**Problem**: All intent matching uses exact substring matching: `"pause jarvis" in lowered`. If the STT transcribes "paws jarvis", "pause javis", "pos jarvis", or "pause service" — none will match. The user's intent is clear but the literal string doesn't match.  
**Fix**: Add Levenshtein/fuzzy matching for command phrases. For critical commands (pause, resume, safe mode), accept matches within edit distance 2. Or use the existing embedding-based IntentClassifier for ALL routing, not just the LLM fallback.

### 🟢 MINOR (Lower Impact but Important)

#### RC-10: No Echo Cancellation / Barge-In Detection
**Problem**: When TTS is speaking (edge-tts playing audio), if the user says "stop" or "Jarvis", the microphone picks up the TTS output mixed with the user's voice. There's no acoustic echo cancellation. The STT will transcribe the bot's own speech.  
**Fix**: At minimum, mute the microphone during TTS output. Better: implement echo reference subtraction using the known TTS audio waveform.

#### RC-11: Parakeet Has No Vocabulary/Prompt Injection
**File**: `stt.py`, `_try_parakeet()`  
**Problem**: Unlike Whisper (which accepts `initial_prompt`) and Deepgram (which accepts `keywords`), the Parakeet TDT model via `onnx_asr.load_model().recognize()` accepts no vocabulary hints. Every proper noun is a blind guess.  
**Impact**: As Parakeet is the FIRST backend tried, misheard proper nouns propagate. If Parakeet returns confidence ≥ 0.6 (which it often does for English), the result is accepted without trying Deepgram (which HAS keyword boosting).

---

## 4. Every Missed Optimization Opportunity

| # | Opportunity | Impact | Effort |
|---|---|---|---|
| 1 | **Streaming Deepgram** instead of batch — get partial results while user still speaks | High | Medium |
| 2 | **WebSocket streaming** for Deepgram with `interim_results=true` for real-time feedback | High | Medium |
| 3 | **Pre-emphasis filter** (y[n] = x[n] - 0.97·x[n-1]) before STT to boost high-frequency consonants (s, t, f, th) | Medium | Low |
| 4 | **Automatic Gain Control (AGC)** — normalize volume in real-time during capture, not just post-hoc | Medium | Medium |
| 5 | **Dual-threshold VAD** — lower threshold for onset (0.35), higher for offset (0.45) | High | Low |
| 6 | **Microphone device selection** — currently uses `sounddevice` default device; no way to pick a specific mic | Medium | Low |
| 7 | **Audio format optimization** — send Opus/FLAC to Deepgram instead of WAV for lower latency | Low | Low |
| 8 | **Warm-start STT models** — pre-load Parakeet model at daemon startup, not on first use | High | Low |
| 9 | **Parallel backend queries** — run Parakeet AND Deepgram simultaneously, take the faster high-confidence result | High | Medium |
| 10 | **Speaker diarization** — distinguish user from TV/other people in the room | Medium | High |
| 11 | **Confidence-weighted keyword matching** — weight high-confidence transcription words more for intent matching | Medium | Medium |
| 12 | **User speech profile adaptation** — track commonly misheard words per user and add them to vocabulary | High | Medium |
| 13 | **Per-user noise profile** — measure ambient noise at startup and set VAD threshold dynamically | Medium | Low |

---

## 5. Specific Parameter Recommendations

### VAD Parameters
```python
# stt_backends.py - record_from_microphone()
silence_duration=1.0,        # Was 2.0 — cut in half for snappier response
drain_seconds=0.3,           # Was 0.0 — flush wake word remnants

# stt_vad.py - SileroVADDetector
threshold=0.4,               # Was 0.5 — catch softer speech

# stt_backends.py - _capture_audio_loop()
# Add speech_pad_ms equivalent: ~10 chunks hangover at 32ms = 320ms
```

### Deepgram Parameters
```python
# stt_backends.py - _build_deepgram_params()
params: list[tuple[str, str]] = [
    ("model", "nova-3"),
    ("language", language),
    ("punctuate", "true"),
    ("smart_format", "true"),
    ("utterances", "true"),         # NEW
    ("endpointing", "400"),         # NEW — 400ms endpoint detection
    ("filler_words", "false"),      # NEW — remove fillers server-side
    ("numerals", "true"),           # NEW — "five" → "5"
]
# Keyword boosting with intensity:
for kt in keyterms[:500]:
    params.append(("keywords", f"{kt}:2"))  # Was just kt — add boost factor
```

### STT Confidence Threshold
```python
# stt.py
CONFIDENCE_RETRY_THRESHOLD = 0.65  # Was 0.6 — slightly higher to prefer multi-backend
```

### faster-whisper VAD Parameters (emergency backend)
```python
# stt.py - SpeechToText.transcribe_audio()
vad_parameters=dict(
    threshold=0.4,                  # Was 0.5
    min_silence_duration_ms=400,    # Was 500 — faster cutoff
    speech_pad_ms=300,              # Was 200 — more padding for word boundaries
    min_speech_duration_ms=200,     # Was 250 — catch shorter commands
),
```

---

## 6. Missing Features vs. Competing Voice Assistants

| Feature | Alexa | Google | Siri | Jarvis | Priority |
|---|---|---|---|---|---|
| Continuous listening mode | ✅ | ✅ | ✅ | ❌ (one-shot) | HIGH |
| Barge-in (interrupt while speaking) | ✅ | ✅ | ✅ | ❌ | HIGH |
| Echo cancellation | ✅ | ✅ | ✅ | ❌ | HIGH |
| Adaptive noise floor | ✅ | ✅ | ✅ | ❌ | MEDIUM |
| Pre-speech audio ring buffer | ✅ | ✅ | ✅ | ❌ | HIGH |
| Streaming STT (partial results) | ✅ | ✅ | ✅ | ❌ (batch only) | MEDIUM |
| Contact name recognition | ✅ | ✅ | ✅ | ⚠️ (19 vocab entries) | HIGH |
| Fuzzy intent matching | ✅ | ✅ | ✅ | ❌ (exact substring) | HIGH |
| Multi-turn voice dialog | ✅ | ✅ | ✅ | ⚠️ (history exists but no dialog confirmation) | MEDIUM |
| "Did you mean X?" confirmation | ✅ | ✅ | ❌ | ❌ | MEDIUM |
| Microphone device selection | ✅ | ✅ | ✅ | ❌ (default only) | LOW |
| Wake word sensitivity tuning | ✅ | ✅ | ❌ | ⚠️ (hardcoded 0.5) | LOW |
| Audio beep on listen start/stop | ✅ | ✅ | ✅ | ❌ | LOW |

---

## 7. Code-Level Fixes Needed

### Fix 1: VAD Silence Duration (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, line 345
```python
# BEFORE
silence_duration: float = 2.0,
# AFTER
silence_duration: float = 1.0,
```

### Fix 2: VAD Threshold (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_vad.py`, line 99
```python
# BEFORE
threshold: float = 0.5,
# AFTER
threshold: float = 0.4,
```
Also update the singleton factory default on line 195:
```python
# BEFORE
def get_vad_detector(threshold: float = 0.5, ...) -> SileroVADDetector:
# AFTER
def get_vad_detector(threshold: float = 0.4, ...) -> SileroVADDetector:
```

### Fix 3: Drain Seconds Default (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, line 346
```python
# BEFORE
drain_seconds: float = 0.0,
# AFTER
drain_seconds: float = 0.3,
```

### Fix 4: Deepgram Keyword Boosting Intensity (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, in `_build_deepgram_params()`
```python
# BEFORE (line ~165)
for kt in keyterms[:500]:
    params.append(("keywords", kt))
# AFTER
for kt in keyterms[:500]:
    params.append(("keywords", f"{kt}:2"))
```

### Fix 5: Add Missing Deepgram Parameters (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, in `_build_deepgram_params()`
```python
params: list[tuple[str, str]] = [
    ("model", "nova-3"),
    ("language", language),
    ("punctuate", "true"),
    ("smart_format", "true"),
    ("utterances", "true"),
    ("endpointing", "400"),
    ("filler_words", "false"),
    ("numerals", "true"),
]
```

### Fix 6: Pre-Speech Ring Buffer (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, in `_capture_audio_loop()`
Add a `collections.deque` ring buffer of ~400ms before the main loop. When speech onset is first detected, prepend the ring buffer to `frames`.

### Fix 7: Speech Padding / Hangover (HIGH PRIORITY)
**File**: `engine/src/jarvis_engine/stt_backends.py`, in `_capture_audio_loop()`
After speech stops, continue recording for `speech_pad_chunks` (10 chunks = 320ms) before starting the silence counter.

### Fix 8: Expand Personal Vocabulary (MEDIUM PRIORITY)
**File**: `engine/src/jarvis_engine/data/personal_vocab.txt`
Add at least 50-100 entries covering:
- Contact names (family, friends, coworkers)
- Frequently mentioned apps, services, games
- Technical terms from user's work
- Local place names
- User's custom command phrases

### Fix 9: Pre-emphasis Filter (LOW PRIORITY)
**File**: `engine/src/jarvis_engine/stt_postprocess.py`, in `preprocess_audio()`
Add before normalization:
```python
# Pre-emphasis to boost high-frequency consonants
if len(audio) > 1:
    audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
```

### Fix 10: Warm-Start Parakeet at Daemon Boot (MEDIUM PRIORITY)
**File**: `engine/src/jarvis_engine/stt.py`
Call `_try_parakeet(np.zeros(1600, dtype=np.float32), language="en")` in a background thread during daemon startup to pre-load the model, avoiding first-use latency.

---

## 8. Test Coverage Analysis

### What IS Tested (18 test files, ~200+ tests)
- TranscriptionResult defaults and construction
- SpeechToText lazy loading and env var override
- Mock transcription with fake WhisperModel
- Groq API call/retry logic
- Deepgram API param building and response parsing
- Parakeet mock transcription
- transcribe_smart fallback chain logic
- Confidence retry thresholds
- Post-processing: hallucination detection, filler removal, LLM correction, NER
- Audio preprocessing (normalize, HPSS, noisereduce)
- VAD detector (Silero and RMS fallback)
- Voice authentication (enroll, verify)
- Intent dispatch routing
- Voice extractors (phone, URL, weather)
- Voice context building
- Voice telemetry lifecycle
- Wake word detector setup

### What is NOT Tested (Critical Gaps)
1. **Real microphone capture** — all tests mock `sounddevice`
2. **End-to-end voice pipeline** (mic → STT → intent → execute) — no integration test
3. **Ring buffer / pre-speech buffering** — doesn't exist yet, so no tests
4. **VAD threshold behavior with real audio** — only tested with synthetic data
5. **Deepgram keyword boosting effectiveness** — no test verifies keyword boost format
6. **Audio format correctness** — WAV header generation tested but not validated against real backends
7. **Concurrent wake word + STT mic access** — race condition in stream pause/resume
8. **Windows-specific audio issues** — WASAPI exclusive mode, mic permissions, sample rate negotiation

---

## 9. Summary: What to Fix First (Priority Order)

1. **Add pre-speech ring buffer** (RC-6) — catches clipped word beginnings
2. **Add speech hangover/padding** (RC-3) — catches clipped word endings  
3. **Lower VAD threshold to 0.4** (RC-2) — catches soft speech
4. **Reduce silence duration to 1.0s** (RC-1) — faster response, less noise
5. **Set drain_seconds=0.3 default** (RC-8) — prevent wake word contamination
6. **Add Deepgram keyword intensity boost** (RC-4) — `keyword:2` format
7. **Add missing Deepgram params** (RC-4) — endpointing, numerals, filler_words
8. **Expand personal_vocab.txt** (RC-5) — 100+ entries for proper noun recognition
9. **Add fuzzy intent matching** (RC-9) — tolerate STT errors in commands
10. **Add pre-emphasis filter** (RC-11 related) — boost consonant recognition

These 10 fixes would collectively eliminate the majority of "doesn't understand me" complaints.
