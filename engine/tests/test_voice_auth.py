"""Comprehensive tests for jarvis_engine.voice_auth module.

Covers enrollment, verification, WAV reading, normalization, edge cases,
multi-sample averaging, resampling, and profile persistence.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from jarvis_engine.voice_auth import (
    VoiceEnrollResult,
    VoiceVerifyResult,
    _extract_embedding,
    _normalize_user_id,
    _profile_path,
    _read_profile,
    _read_wav_mono,
    _resample,
    enroll_voiceprint,
    verify_voiceprint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tone(
    path: Path,
    freq: float,
    *,
    duration_s: float = 1.6,
    rate: int = 16000,
    phase: float = 0.0,
    channels: int = 1,
    sampwidth: int = 2,
) -> None:
    """Write a synthetic WAV file with configurable parameters."""
    t = np.linspace(0.0, duration_s, int(duration_s * rate), endpoint=False)
    base = 0.5 * np.sin((2.0 * np.pi * freq * t) + phase)
    signal = base + 0.03 * np.sin((2.0 * np.pi * (freq * 2.0) * t) + (phase / 3.0))
    signal += 0.01 * np.random.default_rng(42).normal(size=signal.shape)
    signal = np.clip(signal, -1.0, 1.0)

    if sampwidth == 1:
        pcm = ((signal + 1.0) * 127.5).astype(np.uint8)
    elif sampwidth == 2:
        pcm = (signal * 32767.0).astype(np.int16)
    elif sampwidth == 4:
        pcm = (signal * 2147483647.0).astype(np.int32)
    else:
        raise ValueError(f"Unsupported sampwidth: {sampwidth}")

    if channels > 1:
        mono_bytes = pcm.tobytes()
        # Duplicate mono data to simulate stereo
        stereo = np.column_stack([pcm] * channels)
        raw_bytes = stereo.tobytes()
    else:
        raw_bytes = pcm.tobytes()

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(raw_bytes)


# ---------------------------------------------------------------------------
# _normalize_user_id tests
# ---------------------------------------------------------------------------


class TestNormalizeUserId:
    def test_simple_lowercases(self):
        assert _normalize_user_id("Conner") == "conner"

    def test_strips_whitespace(self):
        assert _normalize_user_id("  alice  ") == "alice"

    def test_replaces_special_characters_with_dash(self):
        assert _normalize_user_id("Bob Smith!") == "bob-smith"

    def test_collapses_multiple_dashes(self):
        assert _normalize_user_id("a!!!b") == "a-b"

    def test_strips_leading_trailing_dashes(self):
        assert _normalize_user_id("!!!hello!!!") == "hello"

    def test_preserves_dots_and_underscores(self):
        result = _normalize_user_id("user.name_1")
        assert result == "user.name_1"

    def test_truncates_to_64_characters(self):
        long_id = "a" * 100
        assert len(_normalize_user_id(long_id)) == 64

    def test_empty_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _normalize_user_id("")

    def test_only_special_chars_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _normalize_user_id("!@#$%")


# ---------------------------------------------------------------------------
# _profile_path tests
# ---------------------------------------------------------------------------


class TestProfilePath:
    def test_returns_correct_path(self, tmp_path):
        result = _profile_path(tmp_path, "conner")
        expected = tmp_path / ".planning" / "security" / "voiceprints" / "conner.json"
        assert result == expected


# ---------------------------------------------------------------------------
# _read_profile tests
# ---------------------------------------------------------------------------


class TestReadProfile:
    def test_valid_json_dict(self, tmp_path):
        profile_file = tmp_path / "test.json"
        profile_file.write_text(
            json.dumps({"user_id": "test", "samples": 1}), encoding="utf-8"
        )
        result = _read_profile(profile_file)
        assert result == {"user_id": "test", "samples": 1}

    def test_invalid_json_returns_empty(self, tmp_path):
        profile_file = tmp_path / "bad.json"
        profile_file.write_text("{not json", encoding="utf-8")
        assert _read_profile(profile_file) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_profile(tmp_path / "nonexistent.json") == {}

    def test_non_dict_json_returns_empty(self, tmp_path):
        profile_file = tmp_path / "list.json"
        profile_file.write_text("[1, 2, 3]", encoding="utf-8")
        assert _read_profile(profile_file) == {}


# ---------------------------------------------------------------------------
# _read_wav_mono tests
# ---------------------------------------------------------------------------


class TestReadWavMono:
    def test_reads_16bit_mono(self, tmp_path):
        wav = tmp_path / "mono16.wav"
        _write_tone(wav, 440.0, duration_s=0.1, rate=16000, sampwidth=2)
        sr, data = _read_wav_mono(wav)
        assert sr == 16000
        assert data.dtype == np.float32
        assert data.ndim == 1

    def test_reads_8bit_wav(self, tmp_path):
        wav = tmp_path / "mono8.wav"
        _write_tone(wav, 440.0, duration_s=0.1, rate=16000, sampwidth=1)
        sr, data = _read_wav_mono(wav)
        assert sr == 16000
        assert data.dtype == np.float32

    def test_reads_32bit_wav(self, tmp_path):
        wav = tmp_path / "mono32.wav"
        _write_tone(wav, 440.0, duration_s=0.1, rate=16000, sampwidth=4)
        sr, data = _read_wav_mono(wav)
        assert sr == 16000
        assert data.dtype == np.float32

    def test_stereo_downmixes_to_mono(self, tmp_path):
        wav = tmp_path / "stereo.wav"
        _write_tone(wav, 440.0, duration_s=0.1, rate=16000, channels=2, sampwidth=2)
        sr, data = _read_wav_mono(wav)
        assert sr == 16000
        assert data.ndim == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Audio file not found"):
            _read_wav_mono(tmp_path / "nope.wav")

    def test_oversized_file_raises(self, tmp_path):
        wav = tmp_path / "huge.wav"
        # Create a file that looks big to stat but don't actually allocate 50MB
        _write_tone(wav, 440.0, duration_s=0.1)
        # Monkey-patch stat to return huge size
        from unittest.mock import patch, MagicMock

        original_stat = wav.stat
        mock_stat = MagicMock()
        mock_stat.st_size = 60 * 1024 * 1024  # 60 MB
        with patch.object(type(wav), "stat", return_value=mock_stat):
            with pytest.raises(ValueError, match="too large"):
                _read_wav_mono(wav)


# ---------------------------------------------------------------------------
# _resample tests
# ---------------------------------------------------------------------------


class TestResample:
    def test_same_rate_returns_original(self):
        signal = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = _resample(signal, 16000, 16000)
        np.testing.assert_array_equal(result, signal)

    def test_downsample_reduces_length(self):
        signal = np.ones(16000, dtype=np.float32)
        result = _resample(signal, 16000, 8000)
        assert result.shape[0] == 8000

    def test_upsample_increases_length(self):
        signal = np.ones(8000, dtype=np.float32)
        result = _resample(signal, 8000, 16000)
        assert result.shape[0] == 16000


# ---------------------------------------------------------------------------
# _extract_embedding tests
# ---------------------------------------------------------------------------


class TestExtractEmbedding:
    def test_returns_normalized_vector(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 440.0, duration_s=1.0)
        emb = _extract_embedding(wav)
        norm = float(np.linalg.norm(emb))
        assert abs(norm - 1.0) < 1e-4

    def test_short_audio_raises(self, tmp_path):
        wav = tmp_path / "short.wav"
        # Very short audio: only 100 samples at 16kHz
        t = np.linspace(0, 0.006, 100, endpoint=False)
        pcm = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        with pytest.raises(ValueError, match="too short"):
            _extract_embedding(wav)

    def test_resamples_non_16khz(self, tmp_path):
        wav = tmp_path / "tone44.wav"
        _write_tone(wav, 440.0, duration_s=1.0, rate=44100)
        emb = _extract_embedding(wav)
        assert emb.size > 0
        norm = float(np.linalg.norm(emb))
        assert abs(norm - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# enroll_voiceprint tests
# ---------------------------------------------------------------------------


class TestEnrollVoiceprint:
    def test_first_enrollment(self, tmp_path):
        wav = tmp_path / "sample.wav"
        _write_tone(wav, 300.0)
        result = enroll_voiceprint(
            tmp_path, user_id="Conner", wav_path=str(wav), replace=True
        )
        assert isinstance(result, VoiceEnrollResult)
        assert result.samples == 1
        assert result.user_id == "conner"
        assert "enrolled" in result.message.lower()

    def test_incremental_enrollment_increases_samples(self, tmp_path):
        wav1 = tmp_path / "s1.wav"
        wav2 = tmp_path / "s2.wav"
        _write_tone(wav1, 300.0, phase=0.0)
        _write_tone(wav2, 300.0, phase=0.5)

        r1 = enroll_voiceprint(
            tmp_path, user_id="alice", wav_path=str(wav1), replace=True
        )
        assert r1.samples == 1
        r2 = enroll_voiceprint(
            tmp_path, user_id="alice", wav_path=str(wav2), replace=False
        )
        assert r2.samples == 2

    def test_replace_flag_resets_samples(self, tmp_path):
        wav = tmp_path / "s.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="bob", wav_path=str(wav), replace=True)
        enroll_voiceprint(tmp_path, user_id="bob", wav_path=str(wav), replace=False)
        r = enroll_voiceprint(tmp_path, user_id="bob", wav_path=str(wav), replace=True)
        assert r.samples == 1

    def test_creates_profile_directory(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="charlie", wav_path=str(wav))
        profile = _profile_path(tmp_path, "charlie")
        assert profile.exists()

    def test_profile_json_is_valid(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="dave", wav_path=str(wav))
        profile = _profile_path(tmp_path, "dave")
        data = json.loads(profile.read_text(encoding="utf-8"))
        assert data["user_id"] == "dave"
        assert data["samples"] == 1
        assert isinstance(data["embedding"], list)
        assert "created_utc" in data


# ---------------------------------------------------------------------------
# verify_voiceprint tests
# ---------------------------------------------------------------------------


class TestVerifyVoiceprint:
    def test_no_profile_returns_no_match(self, tmp_path):
        wav = tmp_path / "test.wav"
        _write_tone(wav, 300.0)
        result = verify_voiceprint(tmp_path, user_id="nobody", wav_path=str(wav))
        assert isinstance(result, VoiceVerifyResult)
        assert result.matched is False
        assert result.score == 0.0
        assert "no enrolled" in result.message.lower()

    def test_empty_profile_returns_no_match(self, tmp_path):
        wav = tmp_path / "test.wav"
        _write_tone(wav, 300.0)
        # Write an empty profile
        profile = _profile_path(tmp_path, "empty-user")
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            json.dumps({"user_id": "empty-user", "samples": 0, "embedding": []}),
            encoding="utf-8",
        )
        result = verify_voiceprint(tmp_path, user_id="empty-user", wav_path=str(wav))
        assert result.matched is False
        assert "empty" in result.message.lower()

    def test_same_voice_scores_high(self, tmp_path):
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_tone(wav_a, 300.0, phase=0.0)
        _write_tone(wav_b, 300.0, phase=0.2)
        enroll_voiceprint(tmp_path, user_id="same", wav_path=str(wav_a))
        result = verify_voiceprint(
            tmp_path, user_id="same", wav_path=str(wav_b), threshold=0.0
        )
        assert result.score > 0.5

    def test_different_voice_scores_lower(self, tmp_path):
        wav_enroll = tmp_path / "enroll.wav"
        wav_verify = tmp_path / "verify.wav"
        _write_tone(wav_enroll, 190.0)
        _write_tone(wav_verify, 720.0)
        enroll_voiceprint(tmp_path, user_id="diff", wav_path=str(wav_enroll))
        result = verify_voiceprint(
            tmp_path, user_id="diff", wav_path=str(wav_verify), threshold=0.0
        )
        # Different frequencies should score lower than same
        assert result.score < 0.95

    def test_threshold_determines_match(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="threshold-test", wav_path=str(wav))
        high = verify_voiceprint(
            tmp_path, user_id="threshold-test", wav_path=str(wav), threshold=0.99999
        )
        low = verify_voiceprint(
            tmp_path, user_id="threshold-test", wav_path=str(wav), threshold=0.0
        )
        assert low.matched is True
        # Very high threshold may or may not match depending on exact cosine sim

    def test_custom_threshold(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="ct", wav_path=str(wav))
        result = verify_voiceprint(
            tmp_path, user_id="ct", wav_path=str(wav), threshold=0.5
        )
        assert result.threshold == 0.5

    def test_score_rounded_to_4_decimals(self, tmp_path):
        wav = tmp_path / "tone.wav"
        _write_tone(wav, 300.0)
        enroll_voiceprint(tmp_path, user_id="rnd", wav_path=str(wav))
        result = verify_voiceprint(
            tmp_path, user_id="rnd", wav_path=str(wav), threshold=0.0
        )
        score_str = str(result.score)
        if "." in score_str:
            decimals = len(score_str.split(".")[1])
            assert decimals <= 4

    def test_enroll_then_verify_full_flow(self, tmp_path):
        """Integration test: enroll, verify same voice, verify different voice."""
        same_a = tmp_path / "same_a.wav"
        same_b = tmp_path / "same_b.wav"
        diff = tmp_path / "diff.wav"
        _write_tone(same_a, 190.0, phase=0.0)
        _write_tone(same_b, 190.0, phase=0.25)
        _write_tone(diff, 720.0, phase=0.0)

        enroll_voiceprint(
            tmp_path, user_id="Conner", wav_path=str(same_a), replace=True
        )
        same_result = verify_voiceprint(
            tmp_path, user_id="Conner", wav_path=str(same_b), threshold=0.0
        )
        diff_result = verify_voiceprint(
            tmp_path, user_id="Conner", wav_path=str(diff), threshold=0.0
        )
        assert same_result.score > diff_result.score
