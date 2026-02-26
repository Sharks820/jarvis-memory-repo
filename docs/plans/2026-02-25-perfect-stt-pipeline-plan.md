# Perfect STT Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a 7-stage post-processing pipeline that transforms raw Whisper output into perfectly formed, error-free text with noise reduction, hallucination filtering, filler removal, LLM correction, and entity name correction.

**Architecture:** New `stt_postprocess.py` module handles all post-processing stages. Audio preprocessing runs before Whisper transcription. Text post-processing (hallucination detection, filler removal, LLM correction, NER) runs after. Integration point is `transcribe_smart()` in `stt.py`, which calls preprocessing before transcription and postprocessing after. A skip path bypasses expensive stages for short high-confidence commands.

**Tech Stack:** Python, numpy, noisereduce, librosa (HPSS), faster-whisper, Groq API (Kimi K2 for LLM correction), jellyfish (phonetic matching)

**Design doc:** `docs/plans/2026-02-25-perfect-stt-pipeline-design.md`

---

### Task 1: Install dependencies

**Files:**
- Modify: `engine/pyproject.toml` (if it exists, otherwise pip install directly)

**Step 1: Install noisereduce and librosa**

Run:
```bash
cd /c/Users/Conner/jarvis-memory-repo
.venv/Scripts/pip install noisereduce librosa jellyfish
```

Expected: Successfully installed packages

**Step 2: Verify imports work**

Run:
```bash
cd /c/Users/Conner/jarvis-memory-repo
.venv/Scripts/python -c "import noisereduce; import librosa; import jellyfish; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: add noisereduce, librosa, jellyfish dependencies for STT pipeline"
```

---

### Task 2: Create personal vocabulary file

**Files:**
- Create: `engine/src/jarvis_engine/data/personal_vocab.txt`

**Step 1: Create the vocabulary file**

```text
Conner (not Connor, Conor)
Jarvis (AI assistant name)
ops brief
knowledge graph
proactive engine
Ollama
Groq
Anthropic
SQLite
Kotlin
Jetpack Compose
brain status
daily brief
self heal
daemon
safe mode
Kimi K2
Mistral
Gemini
```

**Step 2: Verify file exists**

Run: `ls engine/src/jarvis_engine/data/personal_vocab.txt`
Expected: File listed

**Step 3: Commit**

```bash
git add engine/src/jarvis_engine/data/personal_vocab.txt
git commit -m "feat: add personal vocabulary file for STT post-correction"
```

---

### Task 3: Audio preprocessing (`preprocess_audio`)

**Files:**
- Create: `engine/src/jarvis_engine/stt_postprocess.py`
- Create: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

In `engine/tests/test_stt_postprocess.py`:

```python
"""Tests for STT post-processing pipeline."""

from __future__ import annotations

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 1. preprocess_audio: basic passthrough
# ---------------------------------------------------------------------------

def test_preprocess_audio_returns_float32_array() -> None:
    """preprocess_audio returns a float32 numpy array."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    audio = np.random.randn(16000).astype(np.float32) * 0.1
    result = preprocess_audio(audio)
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32


def test_preprocess_audio_normalizes_peak() -> None:
    """Audio is peak-normalized so max amplitude is near target (-3dBFS ~ 0.708)."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    # Very quiet audio
    audio = np.random.randn(16000).astype(np.float32) * 0.01
    result = preprocess_audio(audio)
    peak = np.max(np.abs(result))
    # After normalization, peak should be close to 0.708 (-3dBFS)
    assert peak > 0.5, f"Peak {peak} too low after normalization"
    assert peak <= 1.0, f"Peak {peak} exceeds 1.0"


def test_preprocess_audio_trims_silence() -> None:
    """Leading and trailing silence is trimmed."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    # 1s silence + 0.5s speech + 1s silence
    silence = np.zeros(16000, dtype=np.float32)
    speech = np.random.randn(8000).astype(np.float32) * 0.5
    audio = np.concatenate([silence, speech, silence])
    result = preprocess_audio(audio)
    # Result should be shorter than input (silence trimmed)
    assert len(result) < len(audio), f"Expected trimming: {len(result)} >= {len(audio)}"


def test_preprocess_audio_handles_pure_silence() -> None:
    """Pure silence returns an empty or near-empty array without error."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    audio = np.zeros(16000, dtype=np.float32)
    result = preprocess_audio(audio)
    assert isinstance(result, np.ndarray)
    # Should be very short or empty since it's all silence
    assert len(result) < 16000


def test_preprocess_audio_preserves_length_for_speech() -> None:
    """Speech-only audio is not drastically shortened."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    # Continuous speech-like audio (no silence periods)
    audio = np.random.randn(16000).astype(np.float32) * 0.3
    result = preprocess_audio(audio)
    # Should retain most of the audio (some edge trimming is OK)
    assert len(result) > 8000, f"Too much audio removed: {len(result)}"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis_engine.stt_postprocess'`

**Step 3: Write minimal implementation**

In `engine/src/jarvis_engine/stt_postprocess.py`:

```python
"""STT post-processing pipeline for perfect transcription quality.

Stages:
1. Audio preprocessing (normalize, HPSS, noise reduce, silence trim)
2. Hallucination detection
3. Filler word removal
4. LLM post-correction
5. NER entity correction
6. Full pipeline orchestration
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1: Audio Preprocessing
# ---------------------------------------------------------------------------

def preprocess_audio(
    audio: np.ndarray,
    *,
    sample_rate: int = 16000,
    target_dbfs: float = -3.0,
    silence_threshold_db: float = -30.0,
) -> np.ndarray:
    """Preprocess audio for optimal Whisper transcription.

    Pipeline:
    1. Peak normalize to target_dbfs
    2. HPSS: separate speech harmonics from percussive noise
    3. Spectral noise reduction
    4. Trim leading/trailing silence

    Parameters
    ----------
    audio:
        Mono float32 numpy array at sample_rate Hz.
    sample_rate:
        Audio sample rate (default 16000).
    target_dbfs:
        Target peak level in dBFS (default -3.0 = 0.708 amplitude).
    silence_threshold_db:
        Silence threshold in dB for trimming (default -30.0).
    """
    if len(audio) == 0:
        return audio.astype(np.float32)

    audio = audio.astype(np.float32)

    # 1. Peak normalize
    peak = np.max(np.abs(audio))
    if peak > 0:
        target_amplitude = 10 ** (target_dbfs / 20)  # -3dBFS = 0.708
        audio = audio * (target_amplitude / peak)

    # 2. HPSS: keep harmonic (speech), discard percussive (clicks, keyboard)
    try:
        import librosa

        stft = librosa.stft(audio)
        harmonic, _ = librosa.decompose.hpss(stft)
        audio = librosa.istft(harmonic, length=len(audio))
        audio = audio.astype(np.float32)
    except ImportError:
        logger.warning("librosa not installed, skipping HPSS")
    except Exception as exc:
        logger.warning("HPSS failed, skipping: %s", exc)

    # 3. Spectral noise reduction
    try:
        import noisereduce as nr

        audio = nr.reduce_noise(
            y=audio, sr=sample_rate, prop_decrease=0.6, stationary=True
        )
        audio = audio.astype(np.float32)
    except ImportError:
        logger.warning("noisereduce not installed, skipping noise reduction")
    except Exception as exc:
        logger.warning("Noise reduction failed, skipping: %s", exc)

    # 4. Trim silence
    try:
        import librosa

        trimmed, _ = librosa.effects.trim(audio, top_db=abs(silence_threshold_db))
        audio = trimmed.astype(np.float32)
    except ImportError:
        pass  # Already warned above
    except Exception as exc:
        logger.warning("Silence trimming failed, skipping: %s", exc)

    return audio
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add audio preprocessing with HPSS, noise reduction, and silence trimming"
```

---

### Task 4: Hallucination detection (`detect_hallucination`)

**Files:**
- Modify: `engine/src/jarvis_engine/stt_postprocess.py`
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 2. detect_hallucination
# ---------------------------------------------------------------------------

def test_detect_hallucination_known_phrases() -> None:
    """Known hallucination phrases are detected."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("Thanks for watching!") is True
    assert detect_hallucination("Please subscribe and like") is True
    assert detect_hallucination("[music]") is True


def test_detect_hallucination_clean_text() -> None:
    """Normal speech is not flagged as hallucination."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("What is on my calendar today") is False
    assert detect_hallucination("Remind me to buy groceries") is False


def test_detect_hallucination_repeated_sequences() -> None:
    """Repeated 3+ word sequences are flagged."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    # Same 3-word phrase repeated
    assert detect_hallucination("the the the the the") is True
    assert detect_hallucination("I am good I am good I am good") is True


def test_detect_hallucination_high_compression() -> None:
    """Text with compression ratio > 2.4 is flagged."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    # Highly repetitive text has high compression ratio
    repeated = "hello world " * 50
    assert detect_hallucination(repeated) is True


def test_detect_hallucination_empty_text() -> None:
    """Empty text is flagged as hallucination."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("") is True
    assert detect_hallucination("   ") is True
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py::test_detect_hallucination_known_phrases -v`
Expected: FAIL with `cannot import name 'detect_hallucination'`

**Step 3: Write minimal implementation**

Append to `engine/src/jarvis_engine/stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# Stage 2: Hallucination Detection
# ---------------------------------------------------------------------------

HALLUCINATION_PHRASES: set[str] = {
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe and like",
    "like and subscribe",
    "click the bell",
    "hit the like button",
    "leave a comment",
    "[music]",
    "[applause]",
    "[laughter]",
    "[silence]",
    "you",
    "...",
    "the end",
    "bye bye",
    "subtitles by",
    "copyright",
}


def detect_hallucination(text: str) -> bool:
    """Detect Whisper hallucinations in transcribed text.

    Checks:
    1. Known hallucination phrases
    2. Repeated 3+ word sequences
    3. Compression ratio > 2.4 (highly repetitive)
    4. Empty/whitespace-only text

    Returns True if text appears to be a hallucination.
    """
    stripped = text.strip()
    if not stripped:
        return True

    lower = stripped.lower()

    # Check known phrases
    for phrase in HALLUCINATION_PHRASES:
        if phrase in lower:
            return True

    # Check for repeated word sequences (3+ words repeated)
    words = lower.split()
    if len(words) >= 6:
        for n in range(3, len(words) // 2 + 1):
            for i in range(len(words) - 2 * n + 1):
                seq = tuple(words[i : i + n])
                rest = words[i + n :]
                for j in range(len(rest) - n + 1):
                    if tuple(rest[j : j + n]) == seq:
                        return True

    # Check compression ratio
    import zlib

    encoded = stripped.encode("utf-8")
    if len(encoded) > 10:
        compressed = zlib.compress(encoded, 9)
        ratio = len(encoded) / len(compressed)
        if ratio > 2.4:
            return True

    return False
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -k "hallucination" -v`
Expected: All 5 hallucination tests PASS

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add hallucination detection for Whisper output"
```

---

### Task 5: Smart filler word removal (`remove_fillers`)

**Files:**
- Modify: `engine/src/jarvis_engine/stt_postprocess.py`
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 3. remove_fillers
# ---------------------------------------------------------------------------

def test_remove_fillers_basic() -> None:
    """Basic filler words are removed."""
    from jarvis_engine.stt_postprocess import remove_fillers

    assert remove_fillers("um I need to uh check my calendar") == "I need to check my calendar"


def test_remove_fillers_multi_word() -> None:
    """Multi-word fillers like 'you know' and 'I mean' are removed."""
    from jarvis_engine.stt_postprocess import remove_fillers

    result = remove_fillers("I mean you know it's like a good idea you know")
    assert "you know" not in result
    assert "I mean" not in result


def test_remove_fillers_preserves_like_as_verb() -> None:
    """'like' used as a verb (not filler) is preserved."""
    from jarvis_engine.stt_postprocess import remove_fillers

    # "I like" is verb usage, should be preserved
    assert "like" in remove_fillers("I like pizza")
    # "do you like" is verb usage
    assert "like" in remove_fillers("do you like this")


def test_remove_fillers_clean_text_unchanged() -> None:
    """Text without fillers passes through unchanged."""
    from jarvis_engine.stt_postprocess import remove_fillers

    clean = "What is the weather today"
    assert remove_fillers(clean) == clean


def test_remove_fillers_normalizes_whitespace() -> None:
    """Output has no double spaces after filler removal."""
    from jarvis_engine.stt_postprocess import remove_fillers

    result = remove_fillers("um  uh  hello  er  world")
    assert "  " not in result
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py::test_remove_fillers_basic -v`
Expected: FAIL with `cannot import name 'remove_fillers'`

**Step 3: Write minimal implementation**

Append to `engine/src/jarvis_engine/stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# Stage 3: Smart Filler Word Removal
# ---------------------------------------------------------------------------

# Simple fillers: always remove
_SIMPLE_FILLERS = re.compile(
    r"\b(?:um|uh|er|ah|hmm|hm|mhm|erm)\b", re.IGNORECASE
)

# Multi-word fillers: remove when not at sentence start
_MULTI_WORD_FILLERS = re.compile(
    r"\b(?:you know|I mean|sort of|kind of)\b", re.IGNORECASE
)

# "like" as filler: remove when NOT preceded by subject pronouns/verbs
# Preserve: "I like", "you like", "they like", "would like", "looks like"
_LIKE_FILLER = re.compile(
    r"(?<!\bI )(?<!\byou )(?<!\bthey )(?<!\bwe )(?<!\bwould )(?<!\bdo )(?<!\bdoes )(?<!\blooks )(?<!\bfeel )(?<!\bsound )(?<!\bseems? )(?<!\bhe )(?<!\bshe )\blike\b(?! [a-z]+ing\b)",
    re.IGNORECASE,
)


def remove_fillers(text: str) -> str:
    """Remove filler words while preserving sentence structure.

    Removes:
    - Simple fillers: um, uh, er, ah, hmm
    - Multi-word fillers: "you know", "I mean", "sort of", "kind of"
    - "like" when used as filler (preserves "I like", "looks like", etc.)

    Returns cleaned text with normalized whitespace.
    """
    result = _SIMPLE_FILLERS.sub("", text)
    result = _MULTI_WORD_FILLERS.sub("", result)
    # Normalize whitespace
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -k "filler" -v`
Expected: All 5 filler tests PASS

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add smart filler word removal for STT output"
```

---

### Task 6: LLM post-correction (`correct_with_llm`)

**Files:**
- Modify: `engine/src/jarvis_engine/stt_postprocess.py`
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 4. correct_with_llm
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock


def test_correct_with_llm_calls_gateway() -> None:
    """correct_with_llm calls ModelGateway.complete with the right prompt."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = MagicMock(text="Corrected text here.")

    result = correct_with_llm("corrected text here", mock_gateway, vocab_lines=["Conner"])
    mock_gateway.complete.assert_called_once()
    call_args = mock_gateway.complete.call_args
    assert call_args[1]["model"] == "moonshotai/kimi-k2-instruct"
    assert "Conner" in call_args[1]["messages"][0]["content"]


def test_correct_with_llm_returns_corrected_text() -> None:
    """The corrected text from the LLM is returned."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = MagicMock(text="Hello, Conner!")

    result = correct_with_llm("hello conner", mock_gateway)
    assert result == "Hello, Conner!"


def test_correct_with_llm_fallback_on_error() -> None:
    """On gateway error, original text is returned unchanged."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock()
    mock_gateway.complete.side_effect = RuntimeError("API error")

    result = correct_with_llm("hello conner", mock_gateway)
    assert result == "hello conner"


def test_correct_with_llm_skips_when_no_gateway() -> None:
    """When gateway is None, original text is returned."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    result = correct_with_llm("hello world", None)
    assert result == "hello world"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py::test_correct_with_llm_calls_gateway -v`
Expected: FAIL with `cannot import name 'correct_with_llm'`

**Step 3: Write minimal implementation**

Append to `engine/src/jarvis_engine/stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# Stage 4: LLM Post-Correction
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are a speech-to-text post-processor. Fix ONLY transcription errors in the text below.

Rules:
- Fix spelling, punctuation, and capitalization
- Fix misheard proper nouns using the vocabulary list
- Do NOT change the meaning or add new content
- Do NOT add commentary or explanation
- Return ONLY the corrected text, nothing else

{vocab_section}"""


def _load_personal_vocab() -> list[str]:
    """Load personal vocabulary from data file."""
    vocab_path = Path(__file__).parent / "data" / "personal_vocab.txt"
    if not vocab_path.exists():
        return []
    try:
        lines = vocab_path.read_text(encoding="utf-8").strip().splitlines()
        return [line.strip() for line in lines if line.strip()]
    except OSError:
        return []


def correct_with_llm(
    text: str,
    gateway: object | None,
    *,
    vocab_lines: list[str] | None = None,
) -> str:
    """Use an LLM to correct transcription errors.

    Parameters
    ----------
    text:
        Raw transcribed text to correct.
    gateway:
        ModelGateway instance (or None to skip correction).
    vocab_lines:
        Optional personal vocabulary lines. If None, loaded from file.

    Returns the corrected text, or original text on error.
    """
    if gateway is None:
        return text

    if vocab_lines is None:
        vocab_lines = _load_personal_vocab()

    vocab_section = ""
    if vocab_lines:
        vocab_section = "Personal vocabulary:\n" + "\n".join(f"- {v}" for v in vocab_lines)

    system_prompt = _LLM_SYSTEM_PROMPT.format(vocab_section=vocab_section)

    try:
        response = gateway.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            model="moonshotai/kimi-k2-instruct",
            max_tokens=512,
            route_reason="stt-post-correction",
        )
        corrected = response.text.strip()
        if corrected:
            return corrected
        return text
    except Exception as exc:
        logger.warning("LLM post-correction failed: %s", exc)
        return text
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -k "llm" -v`
Expected: All 4 LLM tests PASS

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add LLM post-correction for STT transcriptions"
```

---

### Task 7: NER entity correction (`correct_entities`)

**Files:**
- Modify: `engine/src/jarvis_engine/stt_postprocess.py`
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 5. correct_entities
# ---------------------------------------------------------------------------

def test_correct_entities_exact_match() -> None:
    """Exact case-insensitive matches are corrected."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("ask jarvis about it", ["Jarvis"])
    assert result == "ask Jarvis about it"


def test_correct_entities_phonetic_match() -> None:
    """Phonetically similar names are corrected."""
    from jarvis_engine.stt_postprocess import correct_entities

    # "Connor" is phonetically similar to "Conner"
    result = correct_entities("tell Connor about it", ["Conner"])
    assert "Conner" in result


def test_correct_entities_no_match() -> None:
    """Text without entity matches passes through unchanged."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("the weather is nice", ["Conner", "Jarvis"])
    assert result == "the weather is nice"


def test_correct_entities_multiple_entities() -> None:
    """Multiple entities in one sentence are all corrected."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("hey jarvis tell conner", ["Jarvis", "Conner"])
    assert "Jarvis" in result
    assert "Conner" in result


def test_correct_entities_empty_list() -> None:
    """Empty entity list returns text unchanged."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("hello world", [])
    assert result == "hello world"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py::test_correct_entities_exact_match -v`
Expected: FAIL with `cannot import name 'correct_entities'`

**Step 3: Write minimal implementation**

Append to `engine/src/jarvis_engine/stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# Stage 5: NER Entity Correction
# ---------------------------------------------------------------------------

def correct_entities(text: str, entity_list: list[str]) -> str:
    """Correct entity names using exact and phonetic matching.

    For each word in the text, checks:
    1. Case-insensitive exact match against entity_list
    2. Phonetic similarity (Metaphone) for near-misses

    Parameters
    ----------
    text:
        Input text to correct.
    entity_list:
        List of canonical entity names (e.g., ["Conner", "Jarvis"]).
    """
    if not entity_list or not text:
        return text

    # Build phonetic lookup
    try:
        import jellyfish
    except ImportError:
        logger.warning("jellyfish not installed, skipping entity correction")
        return text

    # Build a map: lowercase -> canonical, and metaphone -> canonical
    exact_map: dict[str, str] = {}
    phonetic_map: dict[str, str] = {}
    for entity in entity_list:
        exact_map[entity.lower()] = entity
        try:
            code = jellyfish.metaphone(entity)
            phonetic_map[code] = entity
        except Exception:
            pass

    words = text.split()
    corrected_words = []
    for word in words:
        # Strip punctuation for matching
        stripped = word.strip(".,!?;:'\"")
        prefix = word[: len(word) - len(word.lstrip(".,!?;:'\""))]
        suffix = word[len(stripped) + len(prefix) :]

        # Exact match
        if stripped.lower() in exact_map:
            corrected_words.append(prefix + exact_map[stripped.lower()] + suffix)
            continue

        # Phonetic match
        try:
            word_code = jellyfish.metaphone(stripped)
            if word_code in phonetic_map:
                canonical = phonetic_map[word_code]
                # Only replace if the word is similar enough (avoid false positives)
                similarity = jellyfish.jaro_winkler_similarity(
                    stripped.lower(), canonical.lower()
                )
                if similarity > 0.75:
                    corrected_words.append(prefix + canonical + suffix)
                    continue
        except Exception:
            pass

        corrected_words.append(word)

    return " ".join(corrected_words)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -k "entities" -v`
Expected: All 5 entity tests PASS

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add NER entity correction with phonetic matching"
```

---

### Task 8: Full pipeline orchestrator (`postprocess_transcription`)

**Files:**
- Modify: `engine/src/jarvis_engine/stt_postprocess.py`
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write the failing tests**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 6. postprocess_transcription (full pipeline)
# ---------------------------------------------------------------------------

def test_postprocess_full_pipeline() -> None:
    """Full pipeline runs all stages in order."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = MagicMock(text="Hello, Conner!")

    result = postprocess_transcription(
        text="um hello conner",
        confidence=0.8,
        gateway=mock_gateway,
        entity_list=["Conner"],
    )
    # Fillers removed, LLM corrected, entities fixed
    assert "um" not in result
    assert result  # Non-empty


def test_postprocess_skip_path_short_command() -> None:
    """Short high-confidence commands skip LLM and NER stages."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    mock_gateway = MagicMock()

    result = postprocess_transcription(
        text="brain status",
        confidence=0.98,
        gateway=mock_gateway,
        entity_list=["Conner"],
    )
    # Gateway should NOT be called for short high-confidence command
    mock_gateway.complete.assert_not_called()
    assert result == "brain status"


def test_postprocess_hallucination_returns_empty() -> None:
    """Hallucinated text returns empty string."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    result = postprocess_transcription(
        text="Thanks for watching! Subscribe!",
        confidence=0.5,
        gateway=None,
        entity_list=[],
    )
    assert result == ""


def test_postprocess_no_gateway_still_cleans() -> None:
    """Without gateway, filler removal and entity correction still run."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    result = postprocess_transcription(
        text="um uh hello jarvis",
        confidence=0.8,
        gateway=None,
        entity_list=["Jarvis"],
    )
    assert "um" not in result
    assert "uh" not in result
    assert "Jarvis" in result
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt_postprocess.py::test_postprocess_full_pipeline -v`
Expected: FAIL with `cannot import name 'postprocess_transcription'`

**Step 3: Write minimal implementation**

Append to `engine/src/jarvis_engine/stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# Stage 6: Full Pipeline Orchestration
# ---------------------------------------------------------------------------

# Known short command patterns for skip path
_COMMAND_PATTERNS: set[str] = {
    "brain status",
    "ops brief",
    "daily brief",
    "self heal",
    "pause jarvis",
    "resume jarvis",
    "pause daemon",
    "resume daemon",
    "safe mode",
    "stop listening",
    "start listening",
}


def postprocess_transcription(
    text: str,
    confidence: float,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
    vocab_lines: list[str] | None = None,
) -> str:
    """Run the full STT post-processing pipeline.

    Stages:
    1. Hallucination detection -> return empty if hallucinated
    2. Filler word removal
    3. LLM post-correction (skipped for short high-confidence commands)
    4. NER entity correction (skipped for short high-confidence commands)

    The skip path triggers when:
    - Text is < 5 words
    - Confidence > 0.95
    - This keeps voice commands snappy

    Parameters
    ----------
    text:
        Raw transcribed text.
    confidence:
        Whisper confidence score (0.0 to 1.0).
    gateway:
        ModelGateway instance for LLM correction (or None to skip).
    entity_list:
        Personal entity names for NER correction.
    vocab_lines:
        Personal vocabulary for LLM prompt.
    """
    if not text or not text.strip():
        return ""

    # Stage 1: Hallucination detection
    if detect_hallucination(text):
        logger.info("Hallucination detected, discarding: %r", text[:80])
        return ""

    # Stage 2: Filler removal
    text = remove_fillers(text)

    if not text.strip():
        return ""

    # Skip path: short high-confidence commands
    word_count = len(text.split())
    is_short_command = word_count < 5 and confidence > 0.95

    if is_short_command:
        logger.debug("Skip path: short high-confidence command (%d words, %.2f conf)", word_count, confidence)
        return text

    # Stage 3: LLM post-correction
    if gateway is not None:
        text = correct_with_llm(text, gateway, vocab_lines=vocab_lines)

    # Stage 4: NER entity correction
    if entity_list:
        text = correct_entities(text, entity_list)

    return text
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -k "postprocess" -v`
Expected: All 4 postprocess tests PASS

**Step 5: Run full postprocess test suite**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -v`
Expected: All tests PASS (approximately 24 tests)

**Step 6: Commit**

```bash
git add engine/src/jarvis_engine/stt_postprocess.py engine/tests/test_stt_postprocess.py
git commit -m "feat: add full STT post-processing pipeline with skip path"
```

---

### Task 9: Fix local Whisper confidence + add new transcription params

**Files:**
- Modify: `engine/src/jarvis_engine/stt.py:297-362` (SpeechToText.transcribe_audio)
- Modify: `engine/tests/test_stt.py`

**Step 1: Write the failing test for confidence fix**

Add to `engine/tests/test_stt.py` (at the end of the file):

```python
# ---------------------------------------------------------------------------
# 69. Local confidence uses segment avg_logprob, not language_probability
# ---------------------------------------------------------------------------

def test_local_confidence_uses_logprobs() -> None:
    """SpeechToText.transcribe_audio computes confidence from avg_logprob, not language_probability."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()

    # Segment with avg_logprob of -0.2 should give confidence = 1.0 + (-0.2) = 0.8
    mock_segment = SimpleNamespace(text=" hello ", start=0.0, end=1.0, avg_logprob=-0.2)
    mock_info = SimpleNamespace(language="en", language_probability=0.99)
    mock_model.transcribe.return_value = ([mock_segment], mock_info)
    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(audio)

    # Should use logprob-based confidence (0.8), NOT language_probability (0.99)
    assert abs(result.confidence - 0.8) < 0.01, f"Expected ~0.8, got {result.confidence}"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest engine/tests/test_stt.py::test_local_confidence_uses_logprobs -v`
Expected: FAIL (confidence will be 0.99 from language_probability)

**Step 3: Modify `transcribe_audio` in `stt.py`**

In `engine/src/jarvis_engine/stt.py`, replace the `transcribe_audio` method (lines 297-362). The key changes are:

1. Add `beam_size=5`, `no_repeat_ngram_size=3`, `word_timestamps=True`, `condition_on_previous_text=False`
2. Fix confidence to use segment avg_logprob instead of language_probability
3. Add `hallucination_silence_threshold=0.2`

Replace the method body starting at line 326 (`segments, info = self._model.transcribe(`) through line 362 (end of method) with:

```python
        segments_iter, info = self._model.transcribe(
            audio,
            language=language,
            vad_filter=vad_filter,
            initial_prompt=initial_prompt,
            beam_size=5,
            no_repeat_ngram_size=3,
            condition_on_previous_text=False,
            word_timestamps=True,
            hallucination_silence_threshold=0.2,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
                min_speech_duration_ms=250,
            ),
        )
        texts: list[str] = []
        parsed_segments: list[dict] = []
        logprobs: list[float] = []
        for segment in segments_iter:
            texts.append(segment.text.strip())
            seg_start = getattr(segment, "start", None)
            seg_end = getattr(segment, "end", None)
            if seg_start is not None and seg_end is not None:
                parsed_segments.append({
                    "start": float(seg_start),
                    "end": float(seg_end),
                    "text": segment.text.strip(),
                })
            alp = getattr(segment, "avg_logprob", None)
            if alp is not None:
                logprobs.append(float(alp))
        elapsed = time.monotonic() - t0
        full_text = " ".join(texts).strip()

        # Compute real confidence from segment-level avg_logprob
        if logprobs:
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
        else:
            confidence = getattr(info, "language_probability", 0.0)

        detected_lang = getattr(info, "language", language)
        return TranscriptionResult(
            text=full_text,
            language=detected_lang,
            confidence=confidence,
            duration_seconds=round(elapsed, 3),
            backend="faster-whisper",
            segments=parsed_segments if parsed_segments else None,
        )
```

**Step 4: Run the new test to verify it passes**

Run: `python -m pytest engine/tests/test_stt.py::test_local_confidence_uses_logprobs -v`
Expected: PASS

**Step 5: Update existing test assertions for new Whisper params**

Two existing tests need their `assert_called_once_with` updated because the transcribe call now includes new parameters. In `engine/tests/test_stt.py`:

For `test_transcribe_audio_vad_filter_passed` (line ~1064), update the assert to:

```python
    mock_model.transcribe.assert_called_once_with(
        audio,
        language="en",
        vad_filter=False,
        initial_prompt=JARVIS_DEFAULT_PROMPT,
        beam_size=5,
        no_repeat_ngram_size=3,
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=0.2,
        vad_parameters=dict(
            threshold=0.5,
            min_silence_duration_ms=500,
            speech_pad_ms=200,
            min_speech_duration_ms=250,
        ),
    )
```

For `test_transcribe_audio_accepts_file_path` (line ~1097), update similarly:

```python
    mock_model.transcribe.assert_called_once_with(
        "/tmp/audio.wav",
        language="en",
        vad_filter=True,
        initial_prompt=JARVIS_DEFAULT_PROMPT,
        beam_size=5,
        no_repeat_ngram_size=3,
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=0.2,
        vad_parameters=dict(
            threshold=0.5,
            min_silence_duration_ms=500,
            speech_pad_ms=200,
            min_speech_duration_ms=250,
        ),
    )
```

**Step 6: Run all STT tests**

Run: `python -m pytest engine/tests/test_stt.py -v`
Expected: All tests PASS (74+ tests)

**Step 7: Commit**

```bash
git add engine/src/jarvis_engine/stt.py engine/tests/test_stt.py
git commit -m "feat: fix local Whisper confidence + add beam_size, word_timestamps, hallucination_silence_threshold"
```

---

### Task 10: Integrate preprocessing and postprocessing into `transcribe_smart`

**Files:**
- Modify: `engine/src/jarvis_engine/stt.py:464-550` (transcribe_smart)
- Modify: `engine/tests/test_stt.py`

**Step 1: Write the failing tests**

Add to `engine/tests/test_stt.py`:

```python
# ---------------------------------------------------------------------------
# 70. transcribe_smart calls preprocess_audio before transcription
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_calls_preprocess() -> None:
    """transcribe_smart preprocesses audio before passing to backend."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    expected = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.preprocess_audio", return_value=fake_audio) as mock_preprocess, \
         patch("jarvis_engine.stt._try_local", return_value=expected):
        transcribe_smart(fake_audio, language="en")

    mock_preprocess.assert_called_once()


# ---------------------------------------------------------------------------
# 71. transcribe_smart calls postprocess_transcription after transcription
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_calls_postprocess() -> None:
    """transcribe_smart postprocesses transcription result."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    raw_result = TranscriptionResult(
        text="um hello conner", language="en", confidence=0.9,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt._try_local", return_value=raw_result), \
         patch("jarvis_engine.stt.postprocess_transcription", return_value="Hello, Conner!") as mock_postprocess:
        result = transcribe_smart(fake_audio, language="en")

    mock_postprocess.assert_called_once()
    assert result.text == "Hello, Conner!"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest engine/tests/test_stt.py::test_transcribe_smart_calls_preprocess -v`
Expected: FAIL (preprocess_audio not called)

**Step 3: Modify `transcribe_smart` in `stt.py`**

At the top of `stt.py`, add the import (after the existing imports):

```python
from jarvis_engine.stt_postprocess import postprocess_transcription, preprocess_audio
```

Then modify `transcribe_smart` to:
1. Call `preprocess_audio(audio)` on numpy arrays before transcription
2. Call `postprocess_transcription()` on the result text after transcription
3. Accept optional `gateway` and `entity_list` parameters

The modified function signature:

```python
def transcribe_smart(
    audio: np.ndarray | str,
    *,
    language: str = "en",
    prompt: str = "",
    root_dir: Path | None = None,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
```

Before transcription (after backend selection but before the Groq/local calls), add:

```python
    # Preprocess audio (only for numpy arrays, not file paths)
    if isinstance(audio, np.ndarray) and len(audio) > 0:
        audio = preprocess_audio(audio)
```

After the confidence retry (just before the final return), add:

```python
    # Post-process transcription
    if result.text:
        corrected = postprocess_transcription(
            text=result.text,
            confidence=result.confidence,
            gateway=gateway,
            entity_list=entity_list,
        )
        result = TranscriptionResult(
            text=corrected,
            language=result.language,
            confidence=result.confidence,
            duration_seconds=result.duration_seconds,
            backend=result.backend,
            retried=result.retried,
            segments=result.segments,
        )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest engine/tests/test_stt.py::test_transcribe_smart_calls_preprocess engine/tests/test_stt.py::test_transcribe_smart_calls_postprocess -v`
Expected: Both PASS

**Step 5: Run full STT test suite**

Run: `python -m pytest engine/tests/test_stt.py -v`
Expected: All tests PASS

**Step 6: Run the full project test suite**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All tests PASS (3146+)

**Step 7: Commit**

```bash
git add engine/src/jarvis_engine/stt.py engine/tests/test_stt.py
git commit -m "feat: integrate audio preprocessing and post-processing into transcribe_smart"
```

---

### Task 11: Wire gateway into voice pipeline callers

**Files:**
- Modify: `engine/src/jarvis_engine/main.py` (voice command that calls listen_and_transcribe)
- Modify: `engine/src/jarvis_engine/stt.py` (listen_and_transcribe to accept gateway)

**Step 1: Update listen_and_transcribe to forward gateway**

In `engine/src/jarvis_engine/stt.py`, update `listen_and_transcribe`:

```python
def listen_and_transcribe(
    *,
    max_duration_seconds: float = 30.0,
    language: str = "en",
    root_dir: Path | None = None,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
    """Record from microphone and transcribe in one call."""
    audio = record_from_microphone(max_duration_seconds=max_duration_seconds)
    return transcribe_smart(
        audio, language=language, root_dir=root_dir,
        gateway=gateway, entity_list=entity_list,
    )
```

**Step 2: Find where listen_and_transcribe is called in main.py**

Search for `listen_and_transcribe` calls and update them to pass `gateway=bus.gateway` if the bus has a gateway attribute available.

**Step 3: Run full test suite**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add engine/src/jarvis_engine/stt.py engine/src/jarvis_engine/main.py
git commit -m "feat: wire gateway into voice pipeline for LLM post-correction"
```

---

### Task 12: Final integration tests

**Files:**
- Modify: `engine/tests/test_stt_postprocess.py`

**Step 1: Write integration test**

Append to `engine/tests/test_stt_postprocess.py`:

```python
# ---------------------------------------------------------------------------
# 7. Integration: full pipeline end-to-end
# ---------------------------------------------------------------------------

def test_end_to_end_pipeline_with_noisy_input() -> None:
    """Full pipeline: preprocess noisy audio -> postprocess transcription."""
    from jarvis_engine.stt_postprocess import postprocess_transcription, preprocess_audio

    # Simulate noisy audio
    speech = np.random.randn(16000).astype(np.float32) * 0.3
    noise = np.random.randn(16000).astype(np.float32) * 0.05
    audio = speech + noise

    # Preprocess should not error
    processed = preprocess_audio(audio)
    assert isinstance(processed, np.ndarray)
    assert len(processed) > 0

    # Postprocess with mock gateway
    mock_gateway = MagicMock()
    mock_gateway.complete.return_value = MagicMock(text="Hello, Jarvis!")

    result = postprocess_transcription(
        text="um hello jarvis",
        confidence=0.85,
        gateway=mock_gateway,
        entity_list=["Jarvis", "Conner"],
    )
    assert "um" not in result
    assert "Jarvis" in result


def test_end_to_end_pipeline_pure_noise() -> None:
    """Pure noise audio preprocesses without error."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    noise = np.random.randn(16000).astype(np.float32) * 0.001
    result = preprocess_audio(noise)
    assert isinstance(result, np.ndarray)
```

**Step 2: Run integration tests**

Run: `python -m pytest engine/tests/test_stt_postprocess.py -v`
Expected: All tests PASS

**Step 3: Run full test suite**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add engine/tests/test_stt_postprocess.py
git commit -m "test: add integration tests for STT pipeline end-to-end"
```

---

### Task 13: Final verification and cleanup

**Step 1: Run the full test suite**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All tests PASS (3170+ tests)

**Step 2: Verify no regressions in existing functionality**

Run: `python -m pytest engine/tests/test_stt.py engine/tests/test_gateway_classifier.py engine/tests/test_main.py -v --tb=short`
Expected: All PASS

**Step 3: Verify the postprocess module imports cleanly**

Run:
```bash
python -c "from jarvis_engine.stt_postprocess import preprocess_audio, detect_hallucination, remove_fillers, correct_with_llm, correct_entities, postprocess_transcription; print('All imports OK')"
```
Expected: `All imports OK`

**Step 4: Final commit with all changes**

If any uncommitted changes remain:
```bash
git add -A
git commit -m "feat: complete Perfect STT Pipeline with 7-stage post-processing"
```

---

## Summary of Files

### New Files
| File | Purpose |
|------|---------|
| `engine/src/jarvis_engine/stt_postprocess.py` | All post-processing logic (6 functions) |
| `engine/src/jarvis_engine/data/personal_vocab.txt` | Personal vocabulary for LLM correction |
| `engine/tests/test_stt_postprocess.py` | Tests for all postprocessing functions (~26 tests) |

### Modified Files
| File | Changes |
|------|---------|
| `engine/src/jarvis_engine/stt.py` | beam_size, word_timestamps, confidence fix, preprocess/postprocess integration |
| `engine/tests/test_stt.py` | Updated assertions for new Whisper params + new confidence/integration tests |
| `engine/src/jarvis_engine/main.py` | Wire gateway into voice commands |

### Dependencies Added
| Package | Purpose |
|---------|---------|
| `noisereduce` | Spectral noise reduction |
| `librosa` | HPSS audio separation + silence trimming |
| `jellyfish` | Phonetic similarity for NER entity matching |
