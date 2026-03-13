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
from importlib import import_module
from typing import Any, Protocol, cast

import numpy as np

from jarvis_engine._constants import DEFAULT_CLOUD_MODEL
from jarvis_engine.stt_contracts import TranscriptionSegment

logger = logging.getLogger(__name__)


class _LLMCompletionResponse(Protocol):
    text: str


class _LLMCompletionGateway(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        route_reason: str,
    ) -> _LLMCompletionResponse: ...


class _LibrosaDecomposeModule(Protocol):
    def hpss(self, stft: np.ndarray, margin: float = 1.0) -> tuple[np.ndarray, np.ndarray]: ...


class _LibrosaEffectsModule(Protocol):
    def trim(self, audio: np.ndarray, top_db: float) -> tuple[np.ndarray, Any]: ...


class _LibrosaModule(Protocol):
    decompose: _LibrosaDecomposeModule
    effects: _LibrosaEffectsModule

    def stft(self, audio: np.ndarray) -> np.ndarray: ...
    def istft(self, harmonic: np.ndarray, *, length: int) -> np.ndarray: ...


class _NoiseReduceModule(Protocol):
    def reduce_noise(
        self,
        *,
        y: np.ndarray,
        sr: int,
        prop_decrease: float,
        stationary: bool,
    ) -> np.ndarray: ...


class _JellyfishModule(Protocol):
    def metaphone(self, value: str) -> str: ...
    def jaro_winkler_similarity(self, left: str, right: str) -> float: ...


def _load_librosa() -> _LibrosaModule:
    return cast(_LibrosaModule, import_module("librosa"))


def _load_noisereduce() -> _NoiseReduceModule:
    return cast(_NoiseReduceModule, import_module("noisereduce"))


def _load_jellyfish() -> _JellyfishModule:
    return cast(_JellyfishModule, import_module("jellyfish"))


# ---------------------------------------------------------------------------
# Stage 1: Audio Preprocessing
# ---------------------------------------------------------------------------


def preprocess_audio(
    audio: np.ndarray,
    *,
    sample_rate: int = 16000,
    target_dbfs: float = -3.0,
    silence_threshold_db: float = -30.0,
    skip_hpss: bool = False,
) -> np.ndarray:
    """Preprocess audio for optimal Whisper transcription.

    Pipeline:
    1. Peak normalize to target_dbfs
    2. HPSS: separate speech harmonics from percussive noise (skipped
       for short voice commands where consonant destruction hurts more
       than background noise)
    3. Spectral noise reduction
    4. Trim leading/trailing silence
    """
    if len(audio) == 0:
        return audio.astype(np.float32)

    audio = audio.astype(np.float32)
    duration_s = len(audio) / sample_rate

    # 1. Peak normalize
    peak = np.max(np.abs(audio))
    if peak > 0:
        target_amplitude = 10 ** (target_dbfs / 20)  # -3dBFS = 0.708
        audio = audio * (target_amplitude / peak)

    # 2. HPSS: keep harmonic (speech), discard percussive (clicks, keyboard)
    # Skip for short voice commands (<5s) — HPSS destroys plosive/fricative
    # consonants (p, t, k, s, f, v) that are critical for short utterances.
    if skip_hpss or duration_s < 5.0:
        logger.debug(
            "Skipping HPSS (duration=%.1fs, skip_hpss=%s)", duration_s, skip_hpss
        )
    else:
        try:
            librosa = _load_librosa()

            stft = librosa.stft(audio)
            harmonic, _ = librosa.decompose.hpss(stft, margin=3.0)
            audio = librosa.istft(harmonic, length=len(audio))
            audio = audio.astype(np.float32)
        except ImportError:
            logger.warning("librosa not installed, skipping HPSS")
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.warning("HPSS failed, skipping: %s", exc)

    # 3. Spectral noise reduction
    try:
        nr = _load_noisereduce()

        audio = nr.reduce_noise(
            y=audio, sr=sample_rate, prop_decrease=0.6, stationary=True
        )
        audio = audio.astype(np.float32)
    except ImportError:
        logger.warning("noisereduce not installed, skipping noise reduction")
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        logger.warning("Noise reduction failed, skipping: %s", exc)

    # 4. Trim silence
    try:
        librosa = _load_librosa()

        trimmed, _ = librosa.effects.trim(audio, top_db=abs(silence_threshold_db))
        audio = trimmed.astype(np.float32)
    except ImportError:
        logger.debug("librosa not available for silence trimming")
        pass
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        logger.warning("Silence trimming failed, skipping: %s", exc)

    # 5. Re-normalize after processing (HPSS and noise reduction can lower peak)
    if len(audio) == 0:
        return np.array([], dtype=np.float32)
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

# Phrases that are hallucinations only when they ARE the entire transcription
_EXACT_HALLUCINATION_PHRASES: set[str] = {
    "[music]",
    "[applause]",
    "[laughter]",
    "[silence]",
    "you",
    "...",
    "the end",
    "bye bye",
    "bye",
}

# Phrases that indicate hallucination when found anywhere in the text (substring)
_SUBSTRING_HALLUCINATION_PHRASES: set[str] = {
    "thanks for watching",
    "thank you for watching",
    "thank you for listening",
    "thanks for listening",
    "please subscribe",
    "subscribe and like",
    "like and subscribe",
    "click the bell",
    "hit the like button",
    "leave a comment",
    "subtitles by",
    "copyright",
    "cc by",
}

# Foreign-language artifacts that Whisper hallucinates from corrupted audio
# at segment boundaries.  Checked only at the START of a transcription.
_FOREIGN_HALLUCINATION_PREFIXES: set[str] = {
    "essen",
    "untertitel",
    "untertitelung",
    "vielen dank",
    "sous-titres",
    "sous titres",
    "merci",
    "bonjour",
    "gracias",
    "hola",
    "buenos",
    "arigato",
    "konichiwa",
    "konnichiwa",
    "danke",
    "bitte",
    "guten",
    "spasibo",
    "privet",
    "xie xie",
    "ni hao",
}


def detect_hallucination(text: str) -> bool:
    """Detect Whisper hallucinations in transcribed text.

    Checks:
    1. Known hallucination phrases (exact match for short, substring for long)
    2. Repeated 3+ word sequences
    3. Compression ratio > 2.4 (highly repetitive)
    4. Empty/whitespace-only text

    Returns True if text appears to be a hallucination.
    """
    stripped = text.strip()
    if not stripped:
        return True

    lower = stripped.lower()

    # Exact match: text IS the hallucination phrase
    if lower in _EXACT_HALLUCINATION_PHRASES:
        return True

    # Substring match: phrase appears anywhere in text
    for phrase in _SUBSTRING_HALLUCINATION_PHRASES:
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

    # Multi-word sequence repetition (3+ word n-grams repeated 3+ times)
    # Hash-based O(n * max_n) approach with cap to avoid abuse on long inputs
    _MAX_WORDS = 200  # Cap for performance on very long transcripts
    capped = words[:_MAX_WORDS] if len(words) > _MAX_WORDS else words
    if len(capped) >= 6:
        for n in range(3, min(len(capped) // 2 + 1, 10)):
            ngram_counts: dict[tuple[str, ...], int] = {}
            for i in range(len(capped) - n + 1):
                ngram = tuple(capped[i : i + n])
                ngram_counts[ngram] = ngram_counts.get(ngram, 0) + 1
            max_count = max(ngram_counts.values()) if ngram_counts else 0
            if max_count >= 3:
                return True

    # Check compression ratio
    encoded = stripped.encode("utf-8")
    if len(encoded) > 10:
        compressed = zlib.compress(encoded, 9)
        ratio = len(encoded) / len(compressed)
        if ratio > 2.4:
            return True

    return False


def strip_foreign_prefix(text: str) -> str:
    """Remove foreign-language hallucination artifacts from the start of text.

    Whisper sometimes hallucinates foreign words (e.g. "Essen") from
    corrupted audio at the beginning of a recording.  When such a word
    appears before a recognisable English body, strip it rather than
    discarding the entire transcription.
    """
    lower = text.lower().strip()
    if not lower:
        return text

    for prefix in _FOREIGN_HALLUCINATION_PREFIXES:
        if lower.startswith(prefix):
            rest = text.strip()[len(prefix) :].strip().lstrip(",").strip()
            if rest:
                logger.info(
                    "Stripped foreign hallucination prefix %r from transcription",
                    prefix,
                )
                return rest
    return text


# ---------------------------------------------------------------------------
# Stage 3: Smart Filler Word Removal
# ---------------------------------------------------------------------------

# Simple fillers: always remove
_SIMPLE_FILLERS = re.compile(r"\b(?:um|uh|er|ah|hmm|hm|mhm|erm)\b", re.IGNORECASE)

# Multi-word fillers
_MULTI_WORD_FILLERS = re.compile(
    r"\b(?:you know|I mean|sort of|kind of)\b", re.IGNORECASE
)
_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)([a-z])")
_FIRST_PERSON_RE = re.compile(r"\bi\b")


def remove_fillers(text: str) -> str:
    """Remove filler words while preserving sentence structure.

    Removes:
    - Simple fillers: um, uh, er, ah, hmm
    - Multi-word fillers: "you know", "I mean", "sort of", "kind of"

    Returns cleaned text with normalized whitespace.
    """
    result = _SIMPLE_FILLERS.sub("", text)
    result = _MULTI_WORD_FILLERS.sub("", result)
    # Clean up orphaned punctuation left by filler removal
    result = re.sub(r",\s*,", ",", result)  # doubled commas
    result = re.sub(r"\.\s*,", ".", result)  # period-comma from removed filler
    result = re.sub(r",\s*\.", ".", result)  # comma-period from removed filler
    result = re.sub(r"^\s*[,;]\s*", "", result)  # leading comma/semicolon
    # Normalize whitespace
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result


def normalize_sentence_text(text: str) -> str:
    """Clean spacing and restore lightweight sentence casing without changing meaning."""
    if not text.strip():
        return ""
    result = re.sub(r"\s+([,.;:!?])", r"\1", text)
    result = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", result)
    result = re.sub(r"\s{2,}", " ", result).strip()
    result = _FIRST_PERSON_RE.sub("I", result)

    def _capitalize(match: re.Match[str]) -> str:
        prefix, letter = match.groups()
        return f"{prefix}{letter.upper()}"

    return _SENTENCE_START_RE.sub(_capitalize, result)


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
    """Load personal vocabulary from data file.

    Results are cached in ``_shared.load_personal_vocab_lines``.
    """
    from jarvis_engine._shared import load_personal_vocab_lines

    return load_personal_vocab_lines(strip_parens=False)


def _clean_llm_correction_candidate(text: str) -> str:
    """Normalize common wrapper noise around LLM correction output."""
    candidate = text.strip().strip("`")
    candidate = candidate.strip().strip('"').strip("'")
    lower = candidate.lower()
    for prefix in ("corrected text:", "corrected:", "transcription:", "output:"):
        if lower.startswith(prefix):
            candidate = candidate[len(prefix) :].strip().strip('"').strip("'")
            break
    return candidate


_COMMENTARY_PREFIXES: tuple[str, ...] = (
    "here is",
    "here's",
    "i corrected",
    "the corrected",
    "note:",
    "explanation:",
)
_WORDISH_RE = re.compile(r"[a-z0-9']+")


def _accept_llm_correction(original: str, candidate: str) -> bool:
    """Return True when an LLM correction stays close enough to the source."""
    if not candidate:
        return False
    lower = candidate.lower()
    if lower.startswith(_COMMENTARY_PREFIXES):
        return False
    if "\n" in candidate or "```" in candidate:
        return False
    if len(candidate) > max(len(original) * 2, len(original) + 40):
        return False

    original_words = _WORDISH_RE.findall(original.lower())
    candidate_words = _WORDISH_RE.findall(lower)
    if candidate_words and len(candidate_words) > max(
        len(original_words) + 4,
        int(len(original_words) * 1.5) + 2,
    ):
        return False

    if original_words:
        original_set = set(original_words)
        overlap = len(original_set & set(candidate_words))
        required_overlap = 1 if len(original_set) <= 2 else max(2, len(original_set) // 2)
        if overlap < required_overlap:
            return False

    return True


def correct_with_llm(
    text: str,
    gateway: _LLMCompletionGateway | None,
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
        vocab_section = "Personal vocabulary:\n" + "\n".join(
            f"- {v}" for v in vocab_lines
        )

    system_prompt = _LLM_SYSTEM_PROMPT.format(vocab_section=vocab_section)

    try:
        response = gateway.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            model=DEFAULT_CLOUD_MODEL,
            max_tokens=512,
            route_reason="stt-post-correction",
        )
        corrected = _clean_llm_correction_candidate(response.text)
        if _accept_llm_correction(text, corrected):
            return corrected
        return text
    except (
        RuntimeError,
        ValueError,
        TypeError,
        AttributeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as exc:
        logger.warning("LLM post-correction failed: %s", exc)
        return text


# ---------------------------------------------------------------------------
# Stage 5: NER Entity Correction
# ---------------------------------------------------------------------------


def correct_entities(text: str, entity_list: list[str]) -> str:
    """Correct entity names using exact phrase, exact token, and phonetic matching."""
    if not entity_list or not text:
        return text

    jellyfish: _JellyfishModule | None = None
    try:
        jellyfish = _load_jellyfish()
    except ImportError:
        logger.debug("jellyfish not installed, using exact-match entity correction only")

    cleaned_entities: list[str] = []
    seen_entities: set[str] = set()
    for entity in entity_list:
        cleaned = str(entity).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen_entities:
            continue
        seen_entities.add(lowered)
        cleaned_entities.append(cleaned)

    corrected_text = text
    for entity in sorted((value for value in cleaned_entities if " " in value), key=len, reverse=True):
        pattern = re.compile(
            rf"(?<!\w){re.escape(entity).replace(r'\ ', r'\s+')}(?!\w)",
            re.IGNORECASE,
        )
        corrected_text = pattern.sub(entity, corrected_text)

    exact_map: dict[str, str] = {}
    phonetic_map: dict[str, str] = {}
    for entity in cleaned_entities:
        exact_map[entity.lower()] = entity
        if jellyfish is not None and " " not in entity:
            try:
                code = jellyfish.metaphone(entity)
                phonetic_map[code] = entity
            except (ValueError, TypeError) as exc:
                logger.debug("Metaphone encoding failed for %r: %s", entity, exc)

    _PUNCT = ".,!?;:'\""
    words = corrected_text.split()
    corrected_words = []
    for word in words:
        stripped = word.strip(_PUNCT)
        if not stripped:
            corrected_words.append(word)
            continue
        prefix = word[: word.index(stripped[0])]
        suffix = word[word.rindex(stripped[-1]) + 1 :]

        if stripped.lower() in exact_map:
            corrected_words.append(prefix + exact_map[stripped.lower()] + suffix)
            continue

        if jellyfish is not None:
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
            except (ValueError, TypeError) as exc:
                logger.debug("Jaro-winkler similarity failed: %s", exc)

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

    # Stage 1b: Strip foreign-language hallucination prefixes
    text = strip_foreign_prefix(text)
    if not text.strip():
        return ""

    # Stage 2: Filler removal
    text = remove_fillers(text)
    text = normalize_sentence_text(text)

    if not text.strip():
        return ""

    # Skip path: short high-confidence commands or known command patterns
    word_count = len(text.split())
    is_known_command = text.strip().lower() in COMMAND_PATTERNS
    is_short_command = (word_count < 5 and confidence > 0.95) or is_known_command

    if is_short_command:
        logger.debug(
            "Skip path: short high-confidence command (%d words, %.2f conf)",
            word_count,
            confidence,
        )
        if entity_list:
            text = correct_entities(text, entity_list)
        return normalize_sentence_text(text)

    # Stage 3: LLM post-correction
    if gateway is not None:
        text = correct_with_llm(
            text,
            cast(_LLMCompletionGateway, gateway),
            vocab_lines=vocab_lines,
        )

    # Stage 4: NER entity correction
    if entity_list:
        text = correct_entities(text, entity_list)

    return normalize_sentence_text(text)


def postprocess_transcription_segments(
    segments: list[TranscriptionSegment] | None,
    *,
    entity_list: list[str] | None = None,
) -> list[TranscriptionSegment] | None:
    """Apply deterministic cleanup to timed transcript spans."""
    if not segments:
        return None

    cleaned_segments: list[TranscriptionSegment] = []
    for segment in segments:
        segment_text = str(segment["text"]).strip()
        if not segment_text:
            continue

        kind = str(segment.get("kind", "")).strip().lower()
        if kind == "word":
            cleaned_text = segment_text
            if entity_list:
                cleaned_text = correct_entities(cleaned_text, entity_list)
            cleaned_text = cleaned_text.strip()
        else:
            cleaned_text = postprocess_transcription(
                segment_text,
                confidence=0.9,
                gateway=None,
                entity_list=entity_list,
            )

        if not cleaned_text:
            continue

        cleaned_segment: TranscriptionSegment = {
            "start": segment["start"],
            "end": segment["end"],
            "text": cleaned_text,
        }
        if kind:
            cleaned_segment["kind"] = kind
        cleaned_segments.append(cleaned_segment)
    return cleaned_segments if cleaned_segments else None
