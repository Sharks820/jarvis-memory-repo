"""Tests for Groq STT backend (transcribe_groq) and WAV conversion utilities."""

from __future__ import annotations

import os
import struct
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 22-23, 29-31, 47, 49-50. Groq confidence calculation (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response_json, expected_confidence",
    [
        pytest.param(
            {
                "text": "hello jarvis",
                "language": "en",
                "segments": [
                    {
                        "text": "hello jarvis",
                        "start": 0.0,
                        "end": 1.5,
                        "avg_logprob": -0.15,
                    }
                ],
            },
            round(min(1.0, max(0.0, 1.0 + (-0.15))), 4),  # 0.85
            id="single_segment_logprob",
        ),
        pytest.param(
            {"text": "hello", "language": "en"},
            0.90,
            id="no_segments_key_fallback",
        ),
        pytest.param(
            {
                "text": "hello world",
                "language": "en",
                "segments": [
                    {"text": "hello", "avg_logprob": -0.1, "no_speech_prob": 0.01},
                    {"text": "world", "avg_logprob": -0.2, "no_speech_prob": 0.03},
                ],
            },
            round(
                min(1.0, max(0.0, 1.0 + (-0.15))), 4
            ),  # avg(-0.1,-0.2) = -0.15 -> 0.85
            id="multi_segment_averaged",
        ),
        pytest.param(
            {"text": "hello", "language": "en", "segments": []},
            0.90,
            id="empty_segments_list_fallback",
        ),
        pytest.param(
            {
                "text": "hello",
                "language": "en",
                "segments": [{"text": "hello"}],
            },  # no avg_logprob
            0.90,
            id="segments_without_logprobs_fallback",
        ),
        pytest.param(
            {
                "text": "noise",
                "language": "en",
                "segments": [
                    {"text": "noise", "avg_logprob": -100.0, "no_speech_prob": 0.0}
                ],
            },
            0.0,  # 1.0 + (-100) clamped to 0.0
            id="extreme_logprob_clamped_to_zero",
        ),
        pytest.param(
            {
                "text": "test",
                "language": "en",
                "segments": [
                    {"text": "test", "avg_logprob": float("nan"), "no_speech_prob": 0.0}
                ],
            },
            0.90,  # NaN skipped -> no valid logprobs -> fallback
            id="nan_logprob_skipped_fallback",
        ),
        pytest.param(
            {
                "text": "maybe noise",
                "language": "en",
                "segments": [
                    {"text": "maybe noise", "avg_logprob": -0.1, "no_speech_prob": 0.9}
                ],
            },
            round(
                min(1.0, max(0.0, 1.0 + (-0.1))) * (1.0 - 0.9), 4
            ),  # 0.9 * 0.1 = 0.09
            id="high_no_speech_prob_penalty",
        ),
    ],
)
@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_calculation(response_json, expected_confidence) -> None:
    """transcribe_groq computes confidence correctly from various segment configurations."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_json

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    assert result.text == response_json["text"]
    assert result.backend == "groq-whisper"
    assert result.confidence == expected_confidence


# ---------------------------------------------------------------------------
# 24. _numpy_to_wav_bytes produces valid WAV header
# ---------------------------------------------------------------------------


def test_numpy_to_wav_bytes_valid_header() -> None:
    """WAV bytes start with RIFF header and contain correct format."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    audio = np.zeros(16000, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=16000)

    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
    assert wav_bytes[12:16] == b"fmt "
    assert wav_bytes[36:40] == b"data"


def test_numpy_to_wav_bytes_correct_data_size() -> None:
    """WAV data section size equals num_samples * 2 (16-bit)."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    num_samples = 8000
    audio = np.zeros(num_samples, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=16000)

    # Data chunk size is at offset 40 (little-endian uint32)
    data_size = struct.unpack("<I", wav_bytes[40:44])[0]
    assert data_size == num_samples * 2


def test_numpy_to_wav_bytes_custom_sample_rate() -> None:
    """Custom sample rate is encoded in the WAV header."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    audio = np.zeros(100, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=44100)

    # Sample rate at offset 24 (little-endian uint32)
    sr = struct.unpack("<I", wav_bytes[24:28])[0]
    assert sr == 44100


def test_numpy_to_wav_bytes_clips_values() -> None:
    """Audio values outside [-1, 1] are clipped to int16 range."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    # Audio with values beyond [-1, 1]
    audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio)

    # Extract int16 data (after 44-byte header)
    data = np.frombuffer(wav_bytes[44:], dtype=np.int16)
    assert data[0] == 32767  # clipped to max
    assert data[1] == -32768  # clipped to min
    assert 16000 < data[2] < 16500  # roughly 0.5 * 32767


# ---------------------------------------------------------------------------
# 25. Groq transcription with file path input
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_file_path_input() -> None:
    """transcribe_groq can accept a file path string."""
    from jarvis_engine.stt import transcribe_groq

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello from file",
        "language": "en",
        "segments": [],
    }

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake wav data")
        temp_path = f.name

    try:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = transcribe_groq(temp_path)

        assert result.text == "hello from file"
        # Filename should be the basename of the temp file
        files_arg = mock_client.post.call_args[1]["files"]
        assert files_arg["file"][0] == os.path.basename(temp_path)
    finally:
        os.unlink(temp_path)


# ---------------------------------------------------------------------------
# 26. Groq transcription API error
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_api_error_raises() -> None:
    """Non-200 status code raises RuntimeError."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limited"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Groq STT API error 429"):
            transcribe_groq(fake_audio)


# ---------------------------------------------------------------------------
# 27. Groq transcription missing API key
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": ""}, clear=False)
def test_groq_transcription_no_api_key_raises() -> None:
    """Missing GROQ_API_KEY raises RuntimeError."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY not set"):
        transcribe_groq(fake_audio)


# ---------------------------------------------------------------------------
# 28. Groq transcription custom prompt
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_custom_prompt() -> None:
    """Custom prompt is passed to the API (truncated to 224 chars)."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "custom",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        long_prompt = "a" * 300
        transcribe_groq(fake_audio, prompt=long_prompt)

        data_arg = mock_client.post.call_args[1]["data"]
        assert len(data_arg["prompt"]) == 224


# ---------------------------------------------------------------------------
# 35. _try_groq returns None on failure
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_try_groq_returns_none_on_exception() -> None:
    """_try_groq catches exceptions and returns None."""
    from jarvis_engine.stt import _try_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch(
        "jarvis_engine.stt.transcribe_groq", side_effect=RuntimeError("API error")
    ):
        result = _try_groq(fake_audio, language="en", prompt="")

    assert result is None


# ---------------------------------------------------------------------------
# 51. Groq transcription detected language propagated
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_detected_language() -> None:
    """Detected language from API is used instead of hint."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "bonjour",
        "language": "fr",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio, language="en")

    assert result.language == "fr"


# ---------------------------------------------------------------------------
# 54. Groq retry on 500 response
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_500_response() -> None:
    """transcribe_groq retries once on 5xx and succeeds on second attempt."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_500_response = MagicMock()
    mock_500_response.status_code = 500
    mock_500_response.text = "Internal Server Error"

    mock_ok_response = MagicMock()
    mock_ok_response.status_code = 200
    mock_ok_response.json.return_value = {
        "text": "hello after retry",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [mock_500_response, mock_ok_response]
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        assert result.text == "hello after retry"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 55. Groq retry on connection error
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_connection_error() -> None:
    """transcribe_groq retries once on ConnectError; returns None-like result after exhaustion."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.ConnectError("connection refused")
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        # After 2 failed attempts, returns empty TranscriptionResult
        assert result is not None
        assert result.text == ""
        assert result.confidence == 0.0
        assert result.backend == "groq-whisper"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 56. Groq retry on ReadTimeout
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_read_timeout() -> None:
    """transcribe_groq retries once on ReadTimeout; returns empty result after exhaustion."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.ReadTimeout("read timed out")
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        assert result is not None
        assert result.text == ""
        assert result.confidence == 0.0
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 57. Groq connection error recovers on second attempt
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_connection_error_recovers_on_retry() -> None:
    """First attempt gets ConnectError, second succeeds."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_ok_response = MagicMock()
    mock_ok_response.status_code = 200
    mock_ok_response.json.return_value = {
        "text": "recovered",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [
            _httpx.ConnectError("connection refused"),
            mock_ok_response,
        ]
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep"):
            result = transcribe_groq(fake_audio)

        assert result.text == "recovered"
        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# 61. Groq API uses GROQ_STT_MODEL constant in request
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_api_uses_model_constant() -> None:
    """transcribe_groq sends the GROQ_STT_MODEL value in the API request."""
    from jarvis_engine.stt import transcribe_groq, GROQ_STT_MODEL

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "test", "language": "en", "segments": []}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        transcribe_groq(fake_audio)

        data_arg = mock_client.post.call_args[1]["data"]
        assert data_arg["model"] == GROQ_STT_MODEL


# ---------------------------------------------------------------------------
# 66. Minimum audio duration: transcribe_groq returns None for short audio
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_too_short_returns_none() -> None:
    """Audio shorter than 0.1s (1600 samples) returns None without API call."""
    from jarvis_engine.stt import transcribe_groq

    short_audio = np.zeros(500, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        result = transcribe_groq(short_audio)

    assert result is None
    # httpx.Client should NOT have been called
    mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 67. Minimum audio duration: exactly 1600 samples passes
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_exact_threshold_passes() -> None:
    """Audio with exactly 1600 samples is sent to the API."""
    from jarvis_engine.stt import transcribe_groq

    audio = np.zeros(1600, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "short but valid",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(audio)

    assert result is not None
    assert result.text == "short but valid"


# ---------------------------------------------------------------------------
# 68. Minimum audio duration: file path input bypasses check
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_file_path_bypasses_duration_check() -> None:
    """File path input is not subject to minimum duration check."""
    from jarvis_engine.stt import transcribe_groq

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "from file",
        "language": "en",
        "segments": [],
    }

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake wav data")
        temp_path = f.name

    try:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = transcribe_groq(temp_path)

        assert result is not None
        assert result.text == "from file"
    finally:
        os.unlink(temp_path)
