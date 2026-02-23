# Phase 6: Voice and Personality - Research

**Researched:** 2026-02-23
**Domain:** Text-to-speech persona composition, speech-to-text transcription, domain-aware tone adaptation
**Confidence:** HIGH

## Summary

Phase 6 enhances Jarvis with two complementary capabilities: (1) a persona system that composes LLM-guided responses with a British butler personality that adapts tone based on the query's domain/branch, and (2) a local speech-to-text pipeline using faster-whisper that allows voice commands to be transcribed and executed as if typed.

The existing codebase already has strong foundations. Edge-TTS with en-GB-ThomasNeural is integrated and working with streaming chunked playback. A basic `PersonaConfig` and `compose_persona_reply()` exist but use hardcoded string templates without domain awareness or LLM integration. The voice-run pipeline has a well-established intent routing system through `_cmd_voice_run_impl()`. The Command Bus pattern is mature with clear handler/command dataclass conventions. The work is primarily about enriching `persona.py` with domain-aware system prompt composition and adding a new STT module alongside it.

**Primary recommendation:** Use faster-whisper v1.2.1 with the `small.en` model for STT (best accuracy/speed tradeoff for single-user English on CPU), sounddevice v0.5.5 for microphone capture, and enhance persona.py with a branch-to-tone mapping that composes system prompts fed to the ModelGateway for personality-aware LLM responses.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| VOICE-01 | Edge-TTS British male neural voice (en-GB-ThomasNeural) output with streaming chunked playback (preserve existing) | Already implemented in `voice.py`. Research confirms edge-tts v7.2.7 is current. Rate/pitch/volume prosody controls available. No changes needed beyond preserving behavior. |
| VOICE-02 | Persona layer composes personality-aware responses with British butler character and contextual mild humor | Enhance `compose_persona_reply()` to produce system prompts. Use ModelGateway.complete() with persona system prompt + user query to generate personality-aware text. Tone mapping and prompt templates documented below. |
| VOICE-03 | Persona adapts tone by context: professional for health/finance, light humor for gaming/casual, warm for family | Branch-to-tone mapping using existing `BRANCH_DESCRIPTIONS` from classify.py. Nine branches map to four tone profiles. Research provides complete mapping table. |
| VOICE-04 | Whisper-grade STT processes voice commands with accuracy comparable to Whisper desktop app | faster-whisper v1.2.1 with `small.en` model (~244M params, ~3.4% WER English). sounddevice for mic capture. VAD filtering via built-in Silero VAD. Pipeline: mic -> numpy array -> faster-whisper -> text -> voice-run. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| faster-whisper | 1.2.1 | Speech-to-text transcription | 4x faster than OpenAI Whisper, same accuracy, less memory, supports numpy array input directly |
| sounddevice | 0.5.5 | Microphone audio capture | Pure PortAudio bindings, records to numpy arrays natively, no MSVC build tools needed (unlike PyAudio) |
| edge-tts | 7.2.7 | Text-to-speech (already installed) | Already integrated, en-GB-ThomasNeural works, streaming chunked playback in place |
| numpy | >=1.26.0 | Audio array handling (already installed) | Already a dependency, used for audio data between sounddevice and faster-whisper |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ctranslate2 | (auto via faster-whisper) | Inference engine for Whisper | Pulled automatically as faster-whisper dependency |
| av (PyAV) | (auto via faster-whisper) | Audio decoding | Pulled automatically, includes FFmpeg libs so no separate FFmpeg install needed |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| faster-whisper | openai-whisper | 4x slower, more memory, same accuracy. No benefit. |
| faster-whisper | OpenAI Whisper API (cloud) | Requires internet, costs money, sends audio to cloud. Violates local-first value. |
| sounddevice | PyAudio | Requires MSVC build tools on Windows, callback-based API is more complex, records to bytes not numpy |
| small.en model | large-v3 model | 10GB VRAM, 6x slower, only ~1% WER improvement (3.4% vs 2.4%). Overkill for command transcription. |
| small.en model | tiny.en model | 2x WER increase (7.6% vs 3.4%). Short voice commands need high accuracy. |

**Installation:**
```bash
pip install faster-whisper sounddevice
```

Note: faster-whisper pulls ctranslate2 and PyAV automatically. sounddevice pulls PortAudio binary wheels for Windows. No CUDA setup needed for CPU mode (recommended default).

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
  persona.py          # Enhanced: branch-to-tone mapping, system prompt composition
  voice.py            # Unchanged: Edge-TTS output (VOICE-01 preserve)
  stt.py              # NEW: faster-whisper STT pipeline with mic capture
  commands/
    voice_commands.py  # Enhanced: add VoiceListenCommand/VoiceListenResult
  handlers/
    voice_handlers.py  # Enhanced: add VoiceListenHandler, PersonaComposeHandler
```

### Pattern 1: Domain-Aware Persona System Prompt Composition
**What:** A function that takes a branch/domain identifier and composes a system prompt instructing the LLM to respond in the appropriate tone variant of the British butler persona.
**When to use:** Every time Jarvis generates a personality-aware response (VOICE-02 + VOICE-03).
**Example:**
```python
# persona.py

TONE_PROFILES: dict[str, dict[str, str]] = {
    "professional": {
        "branches": "health,finance,security",
        "tone_instruction": (
            "Respond with composed professionalism. Be precise, measured, and reassuring. "
            "Avoid humor when discussing health conditions, financial matters, or security concerns. "
            "Use language like 'I should note', 'It bears mentioning', 'May I suggest'."
        ),
    },
    "warm": {
        "branches": "family,communications",
        "tone_instruction": (
            "Respond with genuine warmth and care. Be supportive and encouraging. "
            "Use language like 'How lovely', 'I do hope', 'Shall I help with'. "
            "Light warmth is appropriate but keep focus on being helpful."
        ),
    },
    "light_humor": {
        "branches": "gaming,learning",
        "tone_instruction": (
            "Respond with wit and light humor. Mild British quips are welcome. "
            "Use language like 'Splendid', 'Rather impressive', 'One might say'. "
            "Historical or pop culture references (Bond, Downton Abbey) are appropriate."
        ),
    },
    "balanced": {
        "branches": "ops,coding,general",
        "tone_instruction": (
            "Respond with efficient professionalism and occasional dry wit. "
            "Be concise and action-oriented. Brief quips acceptable when tasks complete successfully. "
            "Use language like 'Very good, sir', 'Consider it done', 'Right away'."
        ),
    },
}

PERSONA_BASE_PROMPT = (
    "You are Jarvis, a British butler-style AI personal assistant. "
    "You address your employer as 'sir'. You are competent, discreet, "
    "and unfailingly helpful. Your speech patterns draw from the tradition "
    "of the consummate English butler -- measured, articulate, and occasionally "
    "dry-witted. You never use slang, emojis, or overly casual language. "
    "Keep responses concise (1-3 sentences for simple acknowledgments, "
    "longer only when the content demands it)."
)


def _resolve_tone(branch: str) -> str:
    """Map a memory branch to the appropriate tone profile."""
    for profile_name, profile in TONE_PROFILES.items():
        if branch in profile["branches"].split(","):
            return profile_name
    return "balanced"


def compose_persona_system_prompt(
    cfg: PersonaConfig,
    *,
    branch: str = "general",
) -> str:
    """Build a complete system prompt for LLM-powered persona responses."""
    if not cfg.enabled:
        return ""
    tone_name = _resolve_tone(branch)
    tone = TONE_PROFILES[tone_name]
    humor_note = ""
    if cfg.humor_level == 0:
        humor_note = "Do not use humor. Be purely functional."
    elif cfg.humor_level == 1:
        humor_note = "Minimal humor. Only the driest of remarks."
    elif cfg.humor_level == 3:
        humor_note = "Feel free to be witty. Historical quips and Bond references welcome."
    # humor_level == 2 is default, no extra note needed

    parts = [PERSONA_BASE_PROMPT, tone["tone_instruction"]]
    if humor_note:
        parts.append(humor_note)
    return "\n\n".join(parts)
```

### Pattern 2: STT Pipeline with Voice Activity Detection
**What:** A module that captures microphone audio, detects speech boundaries via Silero VAD, and transcribes with faster-whisper.
**When to use:** When user triggers voice input (VOICE-04).
**Example:**
```python
# stt.py

from __future__ import annotations

import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class TranscriptionResult:
    text: str
    language: str
    confidence: float
    duration_seconds: float


class SpeechToText:
    """Local speech-to-text using faster-whisper."""

    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None  # Lazy load

    def _ensure_model(self):
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

    def transcribe_audio(
        self,
        audio: np.ndarray | str,
        *,
        language: str = "en",
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        """Transcribe audio from numpy array or file path."""
        self._ensure_model()
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=vad_filter,
        )
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())
        return TranscriptionResult(
            text=" ".join(text_parts),
            language=info.language,
            confidence=info.language_probability,
            duration_seconds=info.duration,
        )


def record_from_microphone(
    *,
    sample_rate: int = 16000,
    max_duration_seconds: float = 30.0,
    silence_threshold_seconds: float = 2.0,
) -> np.ndarray:
    """Record audio from the default microphone until silence detected."""
    import sounddevice as sd

    recording = sd.rec(
        int(max_duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    return recording.flatten()
```

### Pattern 3: Command Bus Integration for STT
**What:** New command/result dataclasses and handler following the established project pattern.
**When to use:** Wiring STT into the bus (same pattern as VoiceSayCommand).
**Example:**
```python
# commands/voice_commands.py additions

@dataclass(frozen=True)
class VoiceListenCommand:
    max_duration_seconds: float = 30.0
    language: str = "en"
    model_size: str = "small.en"


@dataclass
class VoiceListenResult:
    text: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    message: str = ""
```

### Anti-Patterns to Avoid
- **Hardcoding persona text in voice handlers:** Keep all persona composition in `persona.py`. Handlers should call persona functions, not embed personality strings.
- **Loading WhisperModel at import time:** The model is ~500MB in memory. Must be lazy-loaded on first use (same pattern as EmbeddingService).
- **Recording audio with fixed duration and no silence detection:** Always use VAD or silence-based cutoff. A 30-second fixed recording for a 3-word command wastes time.
- **Sending audio to cloud for transcription:** Violates local-first value proposition. All STT must run locally via faster-whisper.
- **Composing system prompts per-word or per-sentence:** Compose the persona system prompt once per request, not per chunk. The LLM call is the expensive part.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Speech-to-text | Custom audio processing + ML model | faster-whisper WhisperModel | Whisper is SOTA for English STT. CTranslate2 backend gives 4x speedup. |
| Voice activity detection | Custom amplitude/energy threshold | Silero VAD (built into faster-whisper) | Silero is neural-network-based, handles background noise, pauses, and breathing. Amplitude thresholds fail in noisy environments. |
| Microphone capture | Raw PortAudio C bindings or wave module | sounddevice.rec() | Records directly to numpy arrays. PortAudio handles device enumeration, sample rates, and buffer management. |
| Audio format conversion | Manual FFmpeg subprocess calls | PyAV (bundled with faster-whisper) | faster-whisper includes PyAV which has FFmpeg built in. decode_audio() handles resampling to 16kHz float32. |
| Persona text generation | Expanding hardcoded template strings | LLM with system prompt via ModelGateway | Template strings cannot adapt to arbitrary content. The LLM produces natural British butler prose when given the right system prompt. |

**Key insight:** The STT pipeline is sounddevice -> numpy array -> faster-whisper. All three steps are well-tested library calls. The persona pipeline is branch detection -> tone mapping -> system prompt -> ModelGateway.complete(). Both are composition of existing tools, not novel engineering.

## Common Pitfalls

### Pitfall 1: Whisper Model Download on First Use
**What goes wrong:** First call to `WhisperModel("small.en")` downloads ~500MB from Hugging Face. If this happens during a voice command, the user waits 60+ seconds with no feedback.
**Why it happens:** faster-whisper auto-downloads models on first instantiation.
**How to avoid:** Pre-download the model during installation or first startup. Add a CLI command like `jarvis-engine stt-setup` that triggers the download. Show progress feedback.
**Warning signs:** First voice command takes minutes instead of seconds.

### Pitfall 2: Microphone Permission on Windows 11
**What goes wrong:** Windows 11 blocks microphone access by default for desktop apps. sounddevice.rec() raises PortAudioError.
**Why it happens:** Windows Settings > Privacy > Microphone must have "Let desktop apps access your microphone" enabled.
**How to avoid:** Catch PortAudioError with a clear error message directing user to Windows privacy settings. Do not just crash.
**Warning signs:** "PortAudioError: No default input device" or similar errors.

### Pitfall 3: Persona System Prompt Too Long
**What goes wrong:** Overly detailed persona instructions consume input tokens, leaving less room for the actual query and increasing cost.
**Why it happens:** Temptation to over-specify the persona with paragraphs of instructions.
**How to avoid:** Keep the base persona prompt under 150 tokens. Tone-specific additions under 50 tokens each. Total system prompt under 250 tokens.
**Warning signs:** Cost tracker shows persona-related queries cost significantly more than bare queries.

### Pitfall 4: STT Accuracy on Short Commands
**What goes wrong:** Whisper hallucinate words or produces empty text for very short (1-3 word) voice commands.
**Why it happens:** Whisper was trained on longer utterances. Very short audio clips can fall below the model's comfort zone.
**How to avoid:** Use `vad_filter=True` to strip silence. Set `no_speech_threshold=0.6`. If the transcription is empty or suspiciously short, prompt user to repeat. Consider requiring a minimum audio length of ~1 second.
**Warning signs:** Empty transcriptions, repeated words, or hallucinated filler text.

### Pitfall 5: Edge-TTS Rate Limiting
**What goes wrong:** Microsoft throttles Edge-TTS requests when too many are sent in rapid succession (during streamed chunked playback).
**Why it happens:** Edge-TTS uses Microsoft's free online service. Rate limits are undocumented but real.
**How to avoid:** The existing chunked playback with queue-based producer/consumer already mitigates this. Don't increase chunk frequency. Keep sentences_per_chunk at 3.
**Warning signs:** edge-tts synthesis failures mid-stream, 429-like errors in stderr.

### Pitfall 6: Confusing compose_persona_reply with compose_persona_system_prompt
**What goes wrong:** Two different functions serve different purposes. The existing `compose_persona_reply()` generates template-based acknowledgment strings (no LLM). The new `compose_persona_system_prompt()` generates a system prompt for LLM calls. Mixing them up creates inconsistent behavior.
**Why it happens:** Similar names, both in persona.py.
**How to avoid:** Keep both functions but clearly document their purposes. `compose_persona_reply()` is for quick template acks (voice-run success/fail). `compose_persona_system_prompt()` is for rich LLM responses that need personality.
**Warning signs:** LLM being called for simple "command completed" acknowledgments (wasteful), or template strings being used where personality-aware prose is needed.

## Code Examples

### Verified: faster-whisper transcribe from numpy array
```python
# Source: https://github.com/SYSTRAN/faster-whisper README + transcribe.py
from faster_whisper import WhisperModel
import numpy as np

model = WhisperModel("small.en", device="cpu", compute_type="int8")

# audio must be float32 numpy array, 16kHz mono
audio = np.zeros(16000 * 5, dtype=np.float32)  # 5 seconds of silence
segments, info = model.transcribe(audio, language="en", vad_filter=True)
text = " ".join(seg.text.strip() for seg in segments)
```

### Verified: sounddevice microphone recording to numpy
```python
# Source: https://python-sounddevice.readthedocs.io/
import sounddevice as sd
import numpy as np

# Record 5 seconds at 16kHz mono
duration = 5.0
sample_rate = 16000
recording = sd.rec(
    int(duration * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="float32",
)
sd.wait()  # Block until recording finishes
audio = recording.flatten()  # Shape: (num_samples,)
```

### Verified: Edge-TTS rate/pitch control (existing project pattern)
```python
# Source: engine/src/jarvis_engine/voice.py lines 206-213
# The project already uses edge-tts CLI with --rate flag
cmd = [
    exe, "--text", text, "--voice", voice,
    f"--rate={rate_pct:+d}%",
    "--write-media", out_path,
]
```

### Pattern: Persona-aware LLM response via ModelGateway
```python
# Compose system prompt based on branch, then call gateway
from jarvis_engine.persona import compose_persona_system_prompt, load_persona_config
from jarvis_engine.gateway.models import ModelGateway

cfg = load_persona_config(root)
system_prompt = compose_persona_system_prompt(cfg, branch="health")
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": "When is my next medication due?"},
]
response = gateway.complete(messages, model="qwen3:14b", route_reason="persona_reply")
# response.text will be in British butler tone, professional for health domain
```

## Branch-to-Tone Mapping

The existing branch classification system (from `memory/classify.py`) provides 9 branches. These map to 4 tone profiles:

| Branch | Tone Profile | Behavior |
|--------|-------------|----------|
| health | professional | No humor, precise, reassuring |
| finance | professional | No humor, measured, factual |
| security | professional | No humor, careful, authoritative |
| family | warm | Supportive, caring, encouraging |
| communications | warm | Polite, attentive, personable |
| gaming | light_humor | Witty, playful, pop culture refs |
| learning | light_humor | Encouraging with clever quips |
| ops | balanced | Efficient, occasional dry wit |
| coding | balanced | Concise, action-oriented, dry humor |
| general | balanced | Default balanced butler tone |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| OpenAI Whisper (Python) | faster-whisper (CTranslate2) | 2023-2024 | 4x faster, less memory, same accuracy |
| large model for all STT | turbo model (large-v3-turbo) | Late 2024 | 809M params, ~2.5% WER, 8x faster than large |
| PyAudio for mic capture | sounddevice | 2024-2025 | No MSVC build tools needed, numpy-native, cleaner API |
| Template-based persona | LLM system prompt persona | 2024-2025 | Natural language adaptation vs rigid templates |
| Whisper .en variants | Base Whisper multilingual | 2024 | .en models still better for English-only, small.en recommended |

**Deprecated/outdated:**
- PyAudio: Still works but requires MSVC build tools on Windows and records to bytes (not numpy). sounddevice is strictly better for this use case.
- openai-whisper: Works but 4x slower than faster-whisper. No benefit.
- Custom SSML in edge-tts: Microsoft removed support for custom SSML tags. Only rate/pitch/volume prosody controls remain.

## Model Size Decision Matrix

For this project (single-user, English-only, voice commands of 3-30 words):

| Model | Params | VRAM/RAM | WER (English) | Speed | Recommendation |
|-------|--------|----------|---------------|-------|----------------|
| tiny.en | 39M | ~1 GB | ~7.6% | 10x | Too inaccurate for commands |
| base.en | 74M | ~1 GB | ~5.0% | 7x | Acceptable for simple commands only |
| **small.en** | **244M** | **~2 GB** | **~3.4%** | **4x** | **Best tradeoff. USE THIS.** |
| medium.en | 769M | ~5 GB | ~2.9% | 2x | Marginal gain, 2.5x slower |
| large-v3 | 1,550M | ~10 GB | ~2.4% | 1x | Overkill, massive resource use |
| turbo | 809M | ~6 GB | ~2.5% | 8x | Great if GPU available |

**Decision: Use `small.en` as default with env var override `JARVIS_STT_MODEL`** to allow the user to switch to `base.en` (lower resources) or `medium.en` (higher accuracy) if desired.

For GPU users: `turbo` is an excellent choice (near-large accuracy at 8x speed) but requires ~6GB VRAM. The env var override allows this without code changes.

## Open Questions

1. **Silence detection strategy for voice commands**
   - What we know: faster-whisper has built-in Silero VAD (`vad_filter=True`). sounddevice can record fixed duration with `sd.rec()`.
   - What's unclear: Should we use VAD to auto-stop recording, or record for a fixed duration (e.g., 10s) and let VAD trim silence? Auto-stop is better UX but more complex.
   - Recommendation: Start with fixed-duration recording (configurable, default 10s) with `vad_filter=True` on the transcription side. Add auto-stop in a future iteration if needed.

2. **Persona LLM calls: cloud or local?**
   - What we know: The persona system prompt + user text needs an LLM to generate personality-aware prose. The ModelGateway already has cloud/local routing.
   - What's unclear: Should persona responses always use local Ollama (free, private) or allow cloud routing for higher quality?
   - Recommendation: Use the existing IntentClassifier routing. Simple acknowledgments use `compose_persona_reply()` (no LLM). Rich responses that need personality go through `ModelGateway.complete()` with `route_reason="persona_reply"`.

3. **When to use LLM persona vs template persona**
   - What we know: `compose_persona_reply()` exists for quick template acks. LLM calls cost time and tokens.
   - What's unclear: Exactly which voice-run intents warrant LLM persona vs template.
   - Recommendation: Template for all command confirmations (success/fail). LLM only for conversational queries, briefing narration, and unknown intents where Jarvis needs to compose a natural response.

## Sources

### Primary (HIGH confidence)
- [SYSTRAN/faster-whisper GitHub](https://github.com/SYSTRAN/faster-whisper) - API signatures, model sizes, usage patterns
- [faster-whisper PyPI v1.2.1](https://pypi.org/project/faster-whisper/) - Version, dependencies, Python requirements
- [sounddevice PyPI v0.5.5](https://pypi.org/project/sounddevice/) - Version, PortAudio bindings
- [edge-tts PyPI v7.2.7](https://pypi.org/project/edge-tts/) - Current version, rate/pitch options
- Existing codebase: `persona.py`, `voice.py`, `command_bus.py`, `handlers/voice_handlers.py`, `memory/classify.py`

### Secondary (MEDIUM confidence)
- [OpenWhispr model comparison](https://openwhispr.com/blog/whisper-model-sizes-explained) - WER figures, model size comparison table
- [reriiasu/speech-to-text GitHub](https://github.com/reriiasu/speech-to-text) - Architecture pattern for sounddevice + Silero VAD + faster-whisper pipeline
- [Brim Labs LLM Personas blog](https://brimlabs.ai/blog/llm-personas-how-system-prompts-influence-style-tone-and-intent/) - System prompt persona design patterns

### Tertiary (LOW confidence)
- Whisper model WER figures vary by benchmark dataset. The ~3.4% for small.en is from OpenAI's published benchmarks on LibriSpeech. Real-world command accuracy may differ.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - faster-whisper is the de facto standard for local Whisper inference, sounddevice is well-established, edge-tts already integrated
- Architecture: HIGH - follows exact patterns from existing codebase (command bus, lazy loading, handler shims)
- Pitfalls: HIGH - well-documented issues from faster-whisper GitHub discussions and existing voice.py patterns
- Branch-to-tone mapping: MEDIUM - mapping is logical but tone prompt text will need tuning through iteration
- WER accuracy figures: MEDIUM - published benchmarks, but real-world voice commands may perform differently

**Research date:** 2026-02-23
**Valid until:** 2026-04-23 (stable domain, 60-day window)
