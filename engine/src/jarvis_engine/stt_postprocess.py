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
import zlib
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

    # 5. Re-normalize after processing (HPSS and noise reduction can lower peak)
    peak = np.max(np.abs(audio))
    if peak > 0:
        target_amplitude = 10 ** (target_dbfs / 20)
        audio = audio * (target_amplitude / peak)
    elif peak == 0:
        # Pure silence after processing — return empty array
        return np.array([], dtype=np.float32)

    return audio


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

    # Check for repeated word sequences (1+ words repeated)
    words = lower.split()

    # Single-word repetition: same word appears 4+ times in sequence
    if len(words) >= 4:
        run_count = 1
        for i in range(1, len(words)):
            if words[i] == words[i - 1]:
                run_count += 1
                if run_count >= 4:
                    return True
            else:
                run_count = 1

    # Multi-word sequence repetition (3+ word sequences repeated)
    if len(words) >= 6:
        for n in range(3, len(words) // 2 + 1):
            for i in range(len(words) - 2 * n + 1):
                seq = tuple(words[i : i + n])
                rest = words[i + n :]
                for j in range(len(rest) - n + 1):
                    if tuple(rest[j : j + n]) == seq:
                        return True

    # Check compression ratio
    encoded = stripped.encode("utf-8")
    if len(encoded) > 10:
        compressed = zlib.compress(encoded, 9)
        ratio = len(encoded) / len(compressed)
        if ratio > 2.4:
            return True

    return False


# ---------------------------------------------------------------------------
# Stage 3: Smart Filler Word Removal
# ---------------------------------------------------------------------------

# Simple fillers: always remove
_SIMPLE_FILLERS = re.compile(
    r"\b(?:um|uh|er|ah|hmm|hm|mhm|erm)\b", re.IGNORECASE
)

# Multi-word fillers
_MULTI_WORD_FILLERS = re.compile(
    r"\b(?:you know|I mean|sort of|kind of)\b", re.IGNORECASE
)


def remove_fillers(text: str) -> str:
    """Remove filler words while preserving sentence structure.

    Removes:
    - Simple fillers: um, uh, er, ah, hmm
    - Multi-word fillers: "you know", "I mean", "sort of", "kind of"

    Returns cleaned text with normalized whitespace.
    """
    result = _SIMPLE_FILLERS.sub("", text)
    result = _MULTI_WORD_FILLERS.sub("", result)
    # Normalize whitespace
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result


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


# ---------------------------------------------------------------------------
# Stage 5: NER Entity Correction
# ---------------------------------------------------------------------------

def correct_entities(text: str, entity_list: list[str]) -> str:
    """Correct entity names using exact and phonetic matching.

    For each word in the text, checks:
    1. Case-insensitive exact match against entity_list
    2. Phonetic similarity (Metaphone) for near-misses
    """
    if not entity_list or not text:
        return text

    try:
        import jellyfish
    except ImportError:
        logger.warning("jellyfish not installed, skipping entity correction")
        return text

    # Build lookup maps
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


# ---------------------------------------------------------------------------
# Stage 6: Full Pipeline Orchestration
# ---------------------------------------------------------------------------

COMMAND_PATTERNS: set[str] = {
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

    The skip path triggers when text is < 5 words and confidence > 0.95,
    or when text matches a known command pattern.
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

    # Skip path: short high-confidence commands or known command patterns
    word_count = len(text.split())
    is_known_command = text.strip().lower() in COMMAND_PATTERNS
    is_short_command = (word_count < 5 and confidence > 0.95) or is_known_command

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
