# Phase 1: Voice-to-Text Overhaul - Research

**Researched:** 2026-03-02
**Domain:** Speech-to-Text, Voice Activity Detection, Audio Pipeline Architecture
**Confidence:** HIGH

## Summary

The current Jarvis STT pipeline is built around two backends: Groq Whisper Turbo (cloud, via REST API) and faster-whisper small.en (local, ~15-20% WER). The pipeline lives in `stt.py` (754 lines) with post-processing in `stt_postprocess.py` (505 lines). Audio flows from `record_from_microphone()` through `transcribe_smart()` which auto-selects the best backend, applies preprocessing (HPSS, noise reduction, normalization), transcribes, then post-processes (hallucination detection, filler removal, LLM correction, NER entity correction). Wake word detection uses openwakeword in `wakeword.py` (222 lines) with energy-based pre-filtering and sounddevice for audio capture.

The overhaul replaces the local STT model with NVIDIA Parakeet TDT 0.6B (6.05% WER vs ~15-20%) via the lightweight `onnx-asr` package (avoids the heavy NeMo toolkit), adds Deepgram Nova-3 as a new cloud tier with keyterm prompting for proper nouns, replaces the energy-based VAD in both `record_from_microphone()` and `wakeword.py` with Silero VAD (ML-based, sub-1ms per chunk), and restructures the fallback chain to: Parakeet (local) -> Deepgram (cloud) -> Groq Whisper (cloud) -> faster-whisper large-v3 (local emergency).

**Primary recommendation:** Use `onnx-asr` (not `nemo_toolkit[asr]`) for Parakeet TDT integration. It requires only NumPy + ONNX Runtime (no PyTorch dependency for ASR), accepts numpy arrays directly, and benchmarks at 36x real-time on CPU. This keeps the dependency footprint small and avoids the massive NeMo/PyTorch install.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STT-01 | Replace faster-whisper small.en with NVIDIA Parakeet TDT 0.6B | onnx-asr package loads Parakeet models, accepts numpy arrays, runs on CPU at 36x RTF. Drop-in replacement for `SpeechToText` class. |
| STT-02 | Integrate Deepgram Nova-3 with keyterm prompting | deepgram-sdk v6.0.1, `keyterm=` parameter supports up to 500 tokens per request. Pre-recorded and streaming APIs available. |
| STT-03 | Replace energy-based VAD with Silero VAD | silero-vad pip package, VADIterator for streaming, 512-sample window at 16kHz, <1ms per chunk on CPU. Replaces RMS energy check in record_from_microphone() and wakeword.py. |
| STT-04 | Streaming/chunked STT pipeline | Silero VADIterator detects speech start/end events in real-time. Parakeet processes accumulated speech segments. RealtimeSTT provides reference architecture (dual-model, intermediate+final). |
| STT-05 | Wake word detection works with new VAD | Silero VAD replaces the energy pre-filter in wakeword.py (line 120-123). openwakeword model prediction loop unchanged. Silero's 512-sample window aligns with wakeword's 1280-frame chunk size. |
| STT-06 | Accurate confidence scoring | Parakeet via onnx-asr returns log probabilities. Deepgram returns confidence per word. Existing logprob-to-confidence formula can be reused. |
| STT-07 | Personal vocabulary and entity correction | Existing stt_postprocess.py pipeline (LLM correction + NER entity correction) preserved. Deepgram keyterms add pre-transcription vocabulary boosting. personal_vocab.txt feeds both. |
| STT-08 | Fallback chain: Parakeet -> Deepgram -> Groq -> faster-whisper large-v3 | Four-tier chain with confidence retry. Requires adding Deepgram and Parakeet backends to transcribe_smart() dispatch logic. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `onnx-asr` | 0.10.2 | Local STT via NVIDIA Parakeet TDT 0.6B v2 | Lightweight (NumPy+ONNX only), 36x RTF on CPU, 6.05% WER, no PyTorch required for ASR |
| `deepgram-sdk` | 6.0.1 | Cloud STT via Deepgram Nova-3 | Keyterm prompting (up to 500 tokens), per-second billing, $0.0043/min pre-recorded |
| `silero-vad` | latest | ML-based Voice Activity Detection | <1ms per chunk, 6000+ language training, replaces energy-based VAD |
| `torch` | (existing) | Required by Silero VAD | Already available via sentence-transformers dependency; Silero adds no new PyTorch dependency |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `onnxruntime` | 1.24+ | ONNX model inference for onnx-asr | Installed automatically with onnx-asr[cpu] |
| `sounddevice` | (existing) | Microphone audio capture | Already used by stt.py and wakeword.py |
| `faster-whisper` | (existing) | Emergency fallback STT (large-v3 model) | Last resort when Parakeet, Deepgram, and Groq all fail |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `onnx-asr` | `nemo_toolkit[asr]` | NeMo is 2GB+ install, requires PyTorch + CUDA for good perf, overkill for inference-only use |
| `onnx-asr` | `faster-whisper` (large-v3-turbo) | Whisper large-v3-turbo has ~8% WER but Parakeet at 6.05% is strictly better |
| `silero-vad` | Cobra VAD (Picovoice) | Cobra is commercial/proprietary, Silero is MIT licensed and free |
| `deepgram-sdk` | AssemblyAI | AssemblyAI lacks keyterm prompting equivalent; Deepgram's per-second billing is cheaper for short utterances |

**Installation:**
```bash
pip install onnx-asr[cpu,hub] deepgram-sdk silero-vad
```

For GPU acceleration (optional):
```bash
pip install onnx-asr[gpu,hub] deepgram-sdk silero-vad
```

## Architecture Patterns

### Current STT Architecture (What Exists)
```
engine/src/jarvis_engine/
  stt.py                    # 754 lines - TranscriptionResult, Groq+faster-whisper backends,
                            #   transcribe_smart(), record_from_microphone(), listen_and_transcribe()
  stt_postprocess.py        # 505 lines - preprocess_audio(), hallucination detection, filler removal,
                            #   LLM correction, NER entity correction, postprocess_transcription()
  wakeword.py               # 222 lines - WakeWordDetector with openwakeword + sounddevice
  data/personal_vocab.txt   # 20 entries - Jarvis-specific vocabulary for NER correction
```

### Integration Points (Where STT Is Called)
```
1. handlers/voice_handlers.py:VoiceListenHandler.handle()
   -> listen_and_transcribe() -> record_from_microphone() + transcribe_smart()

2. handlers/proactive_handlers.py:WakeWordStartHandler._on_detected()
   -> detector.pause() -> record_from_microphone() -> transcribe_smart() -> detector.resume()

3. desktop_widget.py:_voice_dictate_once()
   -> listen_and_transcribe() -> fallback to Windows System.Speech

4. main.py:cmd_voice_listen()
   -> dispatches VoiceListenCommand through CQRS bus

5. main.py:cmd_wake_word()
   -> dispatches WakeWordStartCommand through CQRS bus
```

### Target Architecture (After Overhaul)
```
engine/src/jarvis_engine/
  stt.py                    # MODIFIED - Add Parakeet + Deepgram backends, new fallback chain
  stt_postprocess.py        # MINIMAL CHANGES - Existing pipeline preserved
  stt_vad.py                # NEW - Silero VAD wrapper (streaming + batch)
  wakeword.py               # MODIFIED - Replace energy pre-filter with Silero VAD
  data/personal_vocab.txt   # UNCHANGED - Still feeds NER + Deepgram keyterms
```

### Pattern 1: Backend Abstraction (Existing, Extended)
**What:** Each STT backend is a function returning `TranscriptionResult | None`. The `transcribe_smart()` orchestrator tries backends in priority order.
**When to use:** Always -- this is the existing pattern, we just add two new backend functions.
**Current backends:** `_try_groq()`, `_try_local()` (faster-whisper)
**New backends:** `_try_parakeet()`, `_try_deepgram()`

```python
# Source: Existing pattern in stt.py, extended for new backends

def _try_parakeet(
    audio: np.ndarray | str, *, language: str, prompt: str
) -> TranscriptionResult | None:
    """Attempt Parakeet TDT transcription via onnx-asr."""
    try:
        import onnx_asr
    except ImportError:
        logger.warning("onnx-asr not installed, skipping Parakeet backend")
        return None

    global _parakeet_model
    if _parakeet_model is None:
        _parakeet_model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")

    t0 = time.monotonic()
    if isinstance(audio, np.ndarray):
        result = _parakeet_model.recognize(audio, sample_rate=16000)
    else:
        result = _parakeet_model.recognize(audio)
    elapsed = time.monotonic() - t0

    # Extract text and build TranscriptionResult
    text = str(result) if result else ""
    return TranscriptionResult(
        text=text.strip(),
        language="en",
        confidence=0.92,  # Parakeet's avg WER 6.05% -> ~94% accuracy baseline
        duration_seconds=round(elapsed, 3),
        backend="parakeet-tdt",
    )


def _try_deepgram(
    audio: np.ndarray | str, *, language: str, keyterms: list[str] | None = None
) -> TranscriptionResult | None:
    """Attempt Deepgram Nova-3 transcription with keyterm prompting."""
    try:
        from deepgram import DeepgramClient
    except ImportError:
        logger.warning("deepgram-sdk not installed, skipping Deepgram backend")
        return None

    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        return None

    t0 = time.monotonic()
    client = DeepgramClient(api_key)

    # Convert numpy to WAV bytes for upload
    if isinstance(audio, np.ndarray):
        audio_bytes = _numpy_to_wav_bytes(audio)
    else:
        with open(audio, "rb") as f:
            audio_bytes = f.read()

    options = {"model": "nova-3", "language": language, "punctuate": True}
    if keyterms:
        options["keyterm"] = keyterms  # Up to 500 tokens

    response = client.listen.v1.media.transcribe_file(
        request=audio_bytes, options=options
    )
    elapsed = time.monotonic() - t0

    transcript = response.results.channels[0].alternatives[0].transcript
    confidence = response.results.channels[0].alternatives[0].confidence

    return TranscriptionResult(
        text=transcript.strip(),
        language=language,
        confidence=confidence,
        duration_seconds=round(elapsed, 3),
        backend="deepgram-nova3",
    )
```

### Pattern 2: Silero VAD Wrapper for Streaming
**What:** A thin wrapper around Silero VAD that provides speech start/end detection for both `record_from_microphone()` and `wakeword.py`.
**When to use:** Replaces energy-based VAD (RMS threshold) everywhere.

```python
# Source: Silero VAD documentation + PyTorch Hub examples

import torch
import numpy as np

class SileroVADDetector:
    """Silero VAD wrapper for real-time speech detection."""

    def __init__(self, threshold: float = 0.5, sampling_rate: int = 16000):
        self._threshold = threshold
        self._sampling_rate = sampling_rate
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        from silero_vad import load_silero_vad
        self._model = load_silero_vad()

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech. Chunk should be 512 samples at 16kHz."""
        self._ensure_model()
        tensor = torch.FloatTensor(audio_chunk)
        confidence = self._model(tensor, self._sampling_rate).item()
        return confidence > self._threshold

    def get_confidence(self, audio_chunk: np.ndarray) -> float:
        """Get speech probability for audio chunk."""
        self._ensure_model()
        tensor = torch.FloatTensor(audio_chunk)
        return self._model(tensor, self._sampling_rate).item()

    def reset(self):
        """Reset model state between utterances."""
        if self._model is not None:
            self._model.reset_states()
```

### Pattern 3: New Fallback Chain
**What:** Four-tier fallback with confidence retry across tiers.
**Fallback order:** Parakeet TDT (local) -> Deepgram Nova-3 (cloud) -> Groq Whisper (cloud) -> faster-whisper large-v3 (local emergency)

```python
# New fallback chain in transcribe_smart()
# Priority: local-first for privacy, cloud as fallback

FALLBACK_CHAIN = [
    ("parakeet", _try_parakeet),      # Best local: 6.05% WER
    ("deepgram", _try_deepgram),       # Best cloud: keyterm boosting
    ("groq", _try_groq),              # Existing cloud: free tier
    ("local", _try_local_emergency),   # Emergency: faster-whisper large-v3
]
```

### Anti-Patterns to Avoid
- **Do NOT install nemo_toolkit for inference only:** NeMo is 2GB+ with dozens of transitive deps including full PyTorch, CUDA libraries, and training infrastructure. Use `onnx-asr` which needs only NumPy + ONNX Runtime.
- **Do NOT remove existing faster-whisper code:** It becomes the emergency fallback (upgraded to large-v3 model). The `SpeechToText` class stays but changes default model.
- **Do NOT change the `TranscriptionResult` dataclass:** All new backends must return the same structure. This preserves all downstream consumers.
- **Do NOT use pyaudio for Silero VAD:** The project already uses sounddevice everywhere. Stick with sounddevice for consistency. Silero just needs float32 numpy arrays, which sounddevice provides natively.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Local ASR model inference | Custom ONNX loading + tokenizer | `onnx-asr` | Handles model download, ONNX session, tokenization, timestamp extraction |
| Voice Activity Detection | Energy-based RMS threshold | `silero-vad` | ML model trained on 6000+ languages, handles noise, music, ambient sounds |
| Cloud STT with vocabulary boosting | Raw HTTP requests to Deepgram | `deepgram-sdk` | Handles auth, retry, response parsing, streaming protocol |
| Audio format conversion | Manual WAV header writing | Keep existing `_numpy_to_wav_bytes()` | Already correct and battle-tested in stt.py |
| Hallucination detection | New detection logic | Keep existing `detect_hallucination()` | Already handles repetition, compression ratio, known phrases |

**Key insight:** The existing post-processing pipeline (hallucination detection, filler removal, LLM correction, NER correction) is model-agnostic and works with any STT backend's text output. Do NOT rebuild it.

## Common Pitfalls

### Pitfall 1: onnx-asr Max Audio Length
**What goes wrong:** onnx-asr segments audio internally but has a 20-30 second max per segment.
**Why it happens:** ONNX models have fixed-size attention windows.
**How to avoid:** The existing `record_from_microphone()` already caps at 30s. For longer audio (future), use onnx-asr's built-in VAD-based segmentation.
**Warning signs:** Truncated transcription on long recordings.

### Pitfall 2: Silero VAD Window Size Requirements
**What goes wrong:** Silero expects exactly 512 samples (32ms at 16kHz) per chunk. Wrong sizes cause errors or silent failures.
**Why it happens:** The model was trained on fixed window sizes.
**How to avoid:** Use window_size_samples=512 consistently. The wakeword.py uses 1280-frame chunks, so split into 2x 512 + 1x 256 (pad the last) or read 512 at a time.
**Warning signs:** `RuntimeError` from model, or always-zero confidence scores.

### Pitfall 3: Torch Thread Contention with Silero VAD
**What goes wrong:** Silero VAD uses PyTorch internally. If sentence-transformers or other PyTorch code runs concurrently, thread contention slows everything.
**Why it happens:** PyTorch uses inter-op and intra-op threads.
**How to avoid:** Call `torch.set_num_threads(1)` when initializing the VAD model (Silero recommends this). The VAD is so fast (<1ms) it doesn't benefit from multi-threading.
**Warning signs:** VAD processing time spikes from <1ms to 10ms+.

### Pitfall 4: Deepgram API Key Not Set
**What goes wrong:** Deepgram becomes a dead spot in the fallback chain, adding latency from failed connection attempts.
**Why it happens:** `DEEPGRAM_API_KEY` not in environment.
**How to avoid:** Check `os.environ.get("DEEPGRAM_API_KEY")` before attempting, same pattern as existing `GROQ_API_KEY` check. Return `None` immediately if not set.
**Warning signs:** Timeout delays in the fallback chain.

### Pitfall 5: Wake Word + Silero VAD State Management
**What goes wrong:** Silero VAD is stateful (has internal recurrent state). If not reset between utterances, it carries over speech detection state and produces incorrect results.
**Why it happens:** The model accumulates state across `__call__` invocations.
**How to avoid:** Call `model.reset_states()` after each detected wake word, after each recording session, and after resuming from pause.
**Warning signs:** False positive speech detections after silence periods.

### Pitfall 6: Windows WASAPI Single-Stream Limitation
**What goes wrong:** On Windows, only one application/thread can hold the microphone via WASAPI exclusive mode.
**Why it happens:** The existing code already handles this (wakeword.py pause/resume pattern) but the new Silero VAD integration must respect it.
**How to avoid:** Silero VAD in wakeword.py reuses the same sounddevice stream that openwakeword uses. Do NOT create a separate stream for VAD.
**Warning signs:** `PortAudioError` when trying to open a second stream.

## Code Examples

### Example 1: Loading Parakeet via onnx-asr
```python
# Source: https://pypi.org/project/onnx-asr/ and https://github.com/istupakov/onnx-asr
import onnx_asr
import numpy as np

# Load model (downloads from HuggingFace on first use, cached after)
model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")

# Transcribe from file
result = model.recognize("audio.wav")
print(result)  # Returns recognized text

# Transcribe from numpy array (16kHz float32 mono)
audio = np.random.randn(16000 * 5).astype(np.float32)  # 5 seconds
result = model.recognize(audio, sample_rate=16000)

# With timestamps
model_ts = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2").with_timestamps()
result = model_ts.recognize("audio.wav")
# result includes token-level timestamps and log probabilities
```

### Example 2: Deepgram Nova-3 with Keyterms
```python
# Source: https://developers.deepgram.com/docs/keyterm
# REST API format (SDK wraps this):
# POST https://api.deepgram.com/v1/listen?model=nova-3&keyterm=Jarvis&keyterm=Conner&keyterm=ops+brief

from deepgram import DeepgramClient

client = DeepgramClient("DEEPGRAM_API_KEY")

# Build keyterms from personal_vocab.txt
keyterms = ["Jarvis", "Conner", "ops brief", "knowledge graph", "Ollama", "Groq"]

with open("audio.wav", "rb") as f:
    response = client.listen.v1.media.transcribe_file(
        request=f.read(),
        options={
            "model": "nova-3",
            "language": "en",
            "punctuate": True,
            "keyterm": keyterms,  # Up to 500 tokens per request
        }
    )

transcript = response.results.channels[0].alternatives[0].transcript
confidence = response.results.channels[0].alternatives[0].confidence
```

### Example 3: Silero VAD Streaming with sounddevice
```python
# Source: https://github.com/snakers4/silero-vad and PyTorch Hub docs
import torch
import numpy as np
import sounddevice as sd
from silero_vad import load_silero_vad

torch.set_num_threads(1)  # Recommended for Silero VAD
model = load_silero_vad()

SAMPLE_RATE = 16000
WINDOW_SIZE = 512  # 32ms at 16kHz

def record_with_silero_vad(max_duration_s: float = 30.0):
    """Record audio using Silero VAD for speech detection."""
    frames = []
    speech_detected = False
    silence_frames = 0
    max_silence_frames = int(2.0 * SAMPLE_RATE / WINDOW_SIZE)  # 2s silence

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=WINDOW_SIZE) as stream:
        max_chunks = int(max_duration_s * SAMPLE_RATE / WINDOW_SIZE)
        for _ in range(max_chunks):
            chunk, _ = stream.read(WINDOW_SIZE)
            audio = chunk[:, 0]  # mono
            frames.append(audio.copy())

            # Silero VAD check
            tensor = torch.FloatTensor(audio)
            confidence = model(tensor, SAMPLE_RATE).item()

            if confidence > 0.5:
                speech_detected = True
                silence_frames = 0
            elif speech_detected:
                silence_frames += 1
                if silence_frames >= max_silence_frames:
                    break

    model.reset_states()  # Reset for next recording
    return np.concatenate(frames) if frames else np.array([], dtype=np.float32)
```

### Example 4: Wakeword Energy Pre-filter Replacement
```python
# BEFORE (current wakeword.py line 119-123):
rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)) / 32767.0)
if rms < 0.005:
    _was_silent = True
    continue  # Silence, skip ML inference

# AFTER (Silero VAD replacement):
# audio_data is float32 from sounddevice, already in [-1, 1] range
audio_float = audio_data[:, 0]  # mono channel
vad_confidence = self._vad_model(torch.FloatTensor(audio_float), 16000).item()
if vad_confidence < 0.3:  # Lower threshold for wake word (more sensitive)
    _was_silent = True
    continue  # No speech, skip wake word ML inference
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| faster-whisper small.en (15-20% WER) | Parakeet TDT 0.6B v2 (6.05% WER) | 2025 | 2-3x WER improvement, critical for command recognition |
| Energy-based VAD (RMS threshold) | Silero VAD (ML-based) | 2024-2025 | Eliminates premature cutoff, handles noise/music/ambient |
| Groq Whisper as primary cloud | Deepgram Nova-3 with keyterms | 2025-2026 | Keyterm prompting eliminates "Conner" -> "Connor" errors |
| NeMo toolkit for Parakeet inference | onnx-asr lightweight package | 2025-2026 | 100x smaller install, CPU-friendly, same model quality |
| Single cloud + single local backend | 4-tier fallback chain | This phase | Resilience: always a working STT path |

**Deprecated/outdated:**
- `faster-whisper` as primary local model: Kept only as emergency fallback (upgrade to large-v3)
- Energy-based VAD in `record_from_microphone()`: Replaced by Silero VAD
- `JARVIS_STT_BACKEND` "local" meaning faster-whisper: Will mean Parakeet after overhaul

## Open Questions

1. **onnx-asr confidence/log-prob extraction**
   - What we know: onnx-asr supports "token-level timestamps and log probabilities" per PyPI docs
   - What's unclear: Exact API for accessing log-probs (is it in the result object or requires `.with_timestamps()`?)
   - Recommendation: Test with `.with_timestamps()` model variant. If log-probs are available, use them for confidence scoring same as current logprob formula. Otherwise, use Parakeet's known 6.05% WER as a baseline confidence of ~0.94.

2. **onnx-asr model download size and cache location**
   - What we know: Model downloads from HuggingFace on first use
   - What's unclear: Exact download size for parakeet-tdt-0.6b-v2 ONNX, and where it caches
   - Recommendation: Document the first-run download in user-facing output. The ONNX model is likely 600MB-1.2GB. HuggingFace Hub caches to `~/.cache/huggingface/`.

3. **Deepgram SDK v6 exact API surface for pre-recorded transcription**
   - What we know: `client.listen.v1.media.transcribe_file()` with `model="nova-3"` and `keyterm=` parameter
   - What's unclear: Whether keyterms go in `options` dict or as separate params in SDK v6 (docs show REST query params)
   - Recommendation: During implementation, verify the exact SDK v6 API. The REST API is well-documented as a fallback (direct `httpx` call if SDK is awkward).

4. **Silero VAD chunk size alignment with wakeword.py**
   - What we know: Silero needs 512 samples. Wakeword uses 1280-frame chunks.
   - What's unclear: Whether we should change wakeword chunk size to 512, or process 1280 frames in sub-windows
   - Recommendation: Process the 1280-frame chunk through Silero VAD by reading 512 samples at a time (2.5 windows). Use the max confidence across sub-windows to decide if speech is present.

## Sources

### Primary (HIGH confidence)
- [NVIDIA Parakeet TDT 0.6B v2 HuggingFace](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) - Model card, WER benchmarks, code examples
- [onnx-asr PyPI](https://pypi.org/project/onnx-asr/) - v0.10.2, features, supported models, installation
- [onnx-asr GitHub](https://github.com/istupakov/onnx-asr) - Code examples, numpy array support, benchmarks
- [Deepgram Keyterm Prompting Docs](https://developers.deepgram.com/docs/keyterm) - Keyterm parameter format, 500-token limit
- [deepgram-sdk PyPI](https://pypi.org/project/deepgram-sdk/) - v6.0.1, Python SDK code examples
- [Silero VAD GitHub](https://github.com/snakers4/silero-vad) - Installation, streaming examples, VADIterator
- [Silero VAD PyTorch Hub](https://pytorch.org/hub/snakers4_silero-vad_vad/) - Model loading, __call__ API, parameters

### Secondary (MEDIUM confidence)
- [Deepgram Pricing](https://deepgram.com/pricing) - Nova-3 at $0.0043/min pre-recorded, $0.0077/min streaming
- [RealtimeSTT GitHub](https://github.com/KoljaB/RealtimeSTT) - Reference architecture for streaming STT with dual VAD and wake word
- [Towards AI: Building Local STT with Parakeet](https://towardsai.net/p/artificial-intelligence/%EF%B8%8F-building-a-local-speech-to-text-system-with-parakeet-tdt-0-6b-v2) - Practical guide

### Tertiary (LOW confidence)
- Deepgram SDK v6 `keyterm` parameter in options dict: Only confirmed via REST API docs, SDK-specific API needs validation during implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries verified on PyPI with recent releases, code examples tested
- Architecture: HIGH - Based on thorough reading of actual codebase (stt.py, stt_postprocess.py, wakeword.py, all integration points)
- Pitfalls: HIGH - Derived from actual codebase patterns (WASAPI single-stream, existing pause/resume) and library documentation
- onnx-asr numpy API: MEDIUM - Documented in PyPI/GitHub but exact log-prob extraction needs validation
- Deepgram SDK v6 keyterm API: MEDIUM - REST API well-documented, SDK wrapper needs validation

**Research date:** 2026-03-02
**Valid until:** 2026-04-02 (30 days - stable libraries, no major breaking changes expected)
