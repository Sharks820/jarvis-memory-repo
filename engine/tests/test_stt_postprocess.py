"""Tests for STT post-processing pipeline."""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock

import numpy as np
import pytest

from jarvis_engine.gateway.models import GatewayResponse, ModelGateway

_HAS_LIBROSA = bool(importlib.util.find_spec("librosa"))
_HAS_JELLYFISH = bool(importlib.util.find_spec("jellyfish"))


# ---------------------------------------------------------------------------
# 1. preprocess_audio
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

    audio = np.random.randn(16000).astype(np.float32) * 0.01
    result = preprocess_audio(audio)
    peak = np.max(np.abs(result))
    assert peak > 0.5, f"Peak {peak} too low after normalization"
    assert peak <= 1.0, f"Peak {peak} exceeds 1.0"


@pytest.mark.skipif(not _HAS_LIBROSA, reason="librosa not installed")
def test_preprocess_audio_trims_silence() -> None:
    """Leading and trailing silence is trimmed."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    silence = np.zeros(16000, dtype=np.float32)
    speech = np.random.randn(8000).astype(np.float32) * 0.5
    audio = np.concatenate([silence, speech, silence])
    result = preprocess_audio(audio)
    assert len(result) < len(audio), f"Expected trimming: {len(result)} >= {len(audio)}"


@pytest.mark.skipif(not _HAS_LIBROSA, reason="librosa not installed")
def test_preprocess_audio_handles_pure_silence() -> None:
    """Pure silence returns an empty or near-empty array without error."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    audio = np.zeros(16000, dtype=np.float32)
    result = preprocess_audio(audio)
    assert isinstance(result, np.ndarray)
    assert len(result) < 16000


def test_preprocess_audio_preserves_length_for_speech() -> None:
    """Speech-only audio is not drastically shortened."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    audio = np.random.randn(16000).astype(np.float32) * 0.3
    result = preprocess_audio(audio)
    assert len(result) > 8000, f"Too much audio removed: {len(result)}"


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


def test_detect_hallucination_you_not_false_positive() -> None:
    """'you' in normal speech must NOT trigger hallucination detection."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("Can you help me") is False
    assert detect_hallucination("Thank you") is False
    assert detect_hallucination("What do you think") is False
    assert detect_hallucination("Tell me about your features") is False
    # But standalone "you" IS a hallucination (Whisper artifact)
    assert detect_hallucination("you") is True


def test_detect_hallucination_exact_vs_substring() -> None:
    """Exact-match phrases only trigger when they ARE the full text."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    # Exact match: these are hallucinations when standalone
    assert detect_hallucination("the end") is True
    assert detect_hallucination("bye bye") is True
    assert detect_hallucination("...") is True
    # But NOT when embedded in longer speech
    assert detect_hallucination("That's the end of my question") is False
    assert detect_hallucination("Bye bye see you later") is False
    # Substring phrases still trigger inside longer text
    assert detect_hallucination("He said thanks for watching the game") is True


def test_detect_hallucination_repeated_sequences() -> None:
    """Repeated 3+ word sequences are flagged."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("the the the the the") is True
    assert detect_hallucination("I am good I am good I am good") is True


def test_detect_hallucination_high_compression() -> None:
    """Text with compression ratio > 2.4 is flagged."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    repeated = "hello world " * 50
    assert detect_hallucination(repeated) is True


def test_detect_hallucination_empty_text() -> None:
    """Empty text is flagged as hallucination."""
    from jarvis_engine.stt_postprocess import detect_hallucination

    assert detect_hallucination("") is True
    assert detect_hallucination("   ") is True


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

    assert "like" in remove_fillers("I like pizza")
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


def test_normalize_sentence_text_restores_sentence_case() -> None:
    """Sentence cleanup restores leading capitalization and standalone I."""
    from jarvis_engine.stt_postprocess import normalize_sentence_text

    result = normalize_sentence_text("i need jarvis to remind me tomorrow")
    assert result == "I need jarvis to remind me tomorrow"


# ---------------------------------------------------------------------------
# 4. correct_with_llm
# ---------------------------------------------------------------------------

def test_correct_with_llm_calls_gateway() -> None:
    """correct_with_llm calls ModelGateway.complete with the right prompt."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock(spec=ModelGateway)
    mock_gateway.complete.return_value = MagicMock(spec=GatewayResponse, text="Corrected text here.")

    result = correct_with_llm("corrected text here", mock_gateway, vocab_lines=["Conner"])
    mock_gateway.complete.assert_called_once()
    call_args = mock_gateway.complete.call_args
    assert call_args[1]["model"] == "kimi-k2"
    assert "Conner" in call_args[1]["messages"][0]["content"]


def test_correct_with_llm_returns_corrected_text() -> None:
    """The corrected text from the LLM is returned."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock(spec=ModelGateway)
    mock_gateway.complete.return_value = MagicMock(spec=GatewayResponse, text="Hello, Conner!")

    result = correct_with_llm("hello conner", mock_gateway)
    assert result == "Hello, Conner!"


def test_correct_with_llm_fallback_on_error() -> None:
    """On gateway error, original text is returned unchanged."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    mock_gateway = MagicMock(spec=ModelGateway)
    mock_gateway.complete.side_effect = RuntimeError("API error")

    result = correct_with_llm("hello conner", mock_gateway)
    assert result == "hello conner"


def test_correct_with_llm_skips_when_no_gateway() -> None:
    """When gateway is None, original text is returned."""
    from jarvis_engine.stt_postprocess import correct_with_llm

    result = correct_with_llm("hello world", None)
    assert result == "hello world"


# ---------------------------------------------------------------------------
# 5. correct_entities
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_JELLYFISH, reason="jellyfish not installed")
def test_correct_entities_exact_match() -> None:
    """Exact case-insensitive matches are corrected."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("ask jarvis about it", ["Jarvis"])
    assert result == "ask Jarvis about it"


@pytest.mark.skipif(not _HAS_JELLYFISH, reason="jellyfish not installed")
def test_correct_entities_phonetic_match() -> None:
    """Phonetically similar names are corrected."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("tell Connor about it", ["Conner"])
    assert "Conner" in result


def test_postprocess_short_command_still_corrects_entities() -> None:
    """Short-command skip path should still preserve Jarvis-specific entity fixes."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    result = postprocess_transcription(
        "hey jarvis open ollama",
        0.99,
        entity_list=["Jarvis", "Ollama"],
    )
    assert result == "Hey Jarvis open Ollama"


def test_postprocess_longer_text_normalizes_after_filler_removal() -> None:
    """Sentence cleanup should survive filler stripping and keep readable phrasing."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    result = postprocess_transcription(
        "um i think jarvis should check the knowledge graph",
        0.8,
        gateway=None,
        entity_list=["Jarvis"],
    )
    assert result == "I think Jarvis should check the knowledge graph"


@pytest.mark.skipif(not _HAS_JELLYFISH, reason="jellyfish not installed")
def test_correct_entities_no_match() -> None:
    """Text without entity matches passes through unchanged."""
    from jarvis_engine.stt_postprocess import correct_entities

    result = correct_entities("the weather is nice", ["Conner", "Jarvis"])
    assert result == "the weather is nice"


@pytest.mark.skipif(not _HAS_JELLYFISH, reason="jellyfish not installed")
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


# ---------------------------------------------------------------------------
# 6. postprocess_transcription (full pipeline)
# ---------------------------------------------------------------------------

def test_postprocess_full_pipeline() -> None:
    """Full pipeline runs all stages in order."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    mock_gateway = MagicMock(spec=ModelGateway)
    mock_gateway.complete.return_value = MagicMock(spec=GatewayResponse, text="Hello, Conner!")

    result = postprocess_transcription(
        text="um hello conner",
        confidence=0.8,
        gateway=mock_gateway,
        entity_list=["Conner"],
    )
    assert "um" not in result
    assert result  # Non-empty


def test_postprocess_skip_path_short_command() -> None:
    """Short high-confidence commands skip LLM and NER stages."""
    from jarvis_engine.stt_postprocess import postprocess_transcription

    mock_gateway = MagicMock(spec=ModelGateway)

    result = postprocess_transcription(
        text="brain status",
        confidence=0.98,
        gateway=mock_gateway,
        entity_list=["Conner"],
    )
    mock_gateway.complete.assert_not_called()
    assert result == "Brain status"


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


@pytest.mark.skipif(not _HAS_JELLYFISH, reason="jellyfish not installed")
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
    mock_gateway = MagicMock(spec=ModelGateway)
    mock_gateway.complete.return_value = MagicMock(spec=GatewayResponse, text="Hello, Jarvis!")

    result = postprocess_transcription(
        text="um hello jarvis",
        confidence=0.85,
        gateway=mock_gateway,
        entity_list=["Jarvis", "Conner"],
    )
    assert "um" not in result
    assert result  # Non-empty


def test_end_to_end_pipeline_pure_noise() -> None:
    """Pure noise audio preprocesses without error."""
    from jarvis_engine.stt_postprocess import preprocess_audio

    noise = np.random.randn(16000).astype(np.float32) * 0.001
    result = preprocess_audio(noise)
    assert isinstance(result, np.ndarray)
