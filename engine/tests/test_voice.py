"""Tests for voice.py -- TTS voice selection and text chunking."""

from __future__ import annotations

from jarvis_engine.voice import (
    VoiceSpeakResult,
    choose_voice,
    _chunk_text_for_streaming,
    _preferred_voice_patterns,
)


# ---------------------------------------------------------------------------
# choose_voice
# ---------------------------------------------------------------------------

def test_choose_voice_prefers_jarvis_like_patterns() -> None:
    voices = [
        "Microsoft Zira Desktop",
        "Microsoft David Desktop",
        "Microsoft Hazel Desktop - English (Great Britain)",
    ]
    selected = choose_voice(voices, profile="jarvis_like")
    assert selected in voices
    assert "David" in selected or "Great Britain" in selected or "English" in selected


def test_choose_voice_edge_tts_british_first() -> None:
    voices = ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-JennyNeural"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "en-GB-RyanNeural"


def test_choose_voice_custom_pattern_priority() -> None:
    voices = ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-JennyNeural"]
    result = choose_voice(voices, profile="jarvis_like", custom_pattern="Jenny")
    assert result == "en-US-JennyNeural"


def test_choose_voice_fallback_to_first() -> None:
    voices = ["zh-CN-XiaomoNeural", "ja-JP-KeitaNeural"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "zh-CN-XiaomoNeural"


def test_choose_voice_empty_list() -> None:
    result = choose_voice([], profile="jarvis_like")
    assert result == ""


def test_choose_voice_case_insensitive() -> None:
    voices = ["EN-GB-RYANNEURAL"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "EN-GB-RYANNEURAL"


def test_choose_voice_default_profile() -> None:
    voices = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
    result = choose_voice(voices, profile="default")
    assert result == "Microsoft David Desktop"


# ---------------------------------------------------------------------------
# _preferred_voice_patterns
# ---------------------------------------------------------------------------

def test_jarvis_like_prefers_british() -> None:
    patterns = _preferred_voice_patterns("jarvis_like")
    assert patterns[0] == "en-GB-RyanNeural"
    assert len(patterns) > 5


def test_default_profile_patterns() -> None:
    patterns = _preferred_voice_patterns("default")
    assert "David" in patterns


# ---------------------------------------------------------------------------
# _chunk_text_for_streaming
# ---------------------------------------------------------------------------

def test_chunk_empty_text() -> None:
    assert _chunk_text_for_streaming("") == []
    assert _chunk_text_for_streaming("   ") == []


def test_chunk_short_text_single_chunk() -> None:
    text = "Hello world."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world."


def test_chunk_three_sentences() -> None:
    text = "First sentence. Second sentence. Third sentence."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1


def test_chunk_four_sentences() -> None:
    text = "One. Two. Three. Four."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 2
    assert "One" in chunks[0]
    assert "Four" in chunks[1]


def test_chunk_many_sentences() -> None:
    text = "A. B. C. D. E. F. G. H. I."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 3


def test_chunk_preserves_exclamation_and_question() -> None:
    text = "Hello! How are you? I am fine."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1
    assert "Hello!" in chunks[0]


def test_chunk_custom_sentences_per_chunk() -> None:
    text = "A. B. C. D."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=2)
    assert len(chunks) == 2


# ---------------------------------------------------------------------------
# VoiceSpeakResult dataclass
# ---------------------------------------------------------------------------

def test_voice_speak_result() -> None:
    r = VoiceSpeakResult(voice_name="test", output_wav="/tmp/out.wav", message="done")
    assert r.voice_name == "test"
    assert r.output_wav == "/tmp/out.wav"
    assert r.message == "done"

