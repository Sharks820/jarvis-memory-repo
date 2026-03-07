"""Tests for stt_backends.py — STT backend implementations.

Covers WAV conversion, Deepgram transcription (mocked), keyterm loading,
and microphone recording with both Silero VAD and RMS fallback.
"""
from __future__ import annotations

import io
import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import httpx

from jarvis_engine.stt_backends import (
    _load_keyterms,
    _numpy_to_wav_bytes,
    _try_deepgram,
    record_from_microphone,
)


# ===========================================================================
# _numpy_to_wav_bytes
# ===========================================================================


class TestNumpyToWavBytes:
    def test_returns_bytes(self) -> None:
        audio = np.array([0.0, 0.5, -0.5], dtype=np.float32)
        result = _numpy_to_wav_bytes(audio)
        assert isinstance(result, bytes)

    def test_starts_with_riff_header(self) -> None:
        audio = np.zeros(100, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        assert wav[:4] == b"RIFF"

    def test_contains_wave_format(self) -> None:
        audio = np.zeros(100, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        assert b"WAVE" in wav[:12]

    def test_correct_data_size(self) -> None:
        audio = np.zeros(100, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        # Data chunk size = num_samples * 2 bytes (int16)
        buf = io.BytesIO(wav)
        buf.seek(4)
        riff_size = struct.unpack("<I", buf.read(4))[0]
        assert riff_size == 36 + 100 * 2

    def test_correct_sample_rate(self) -> None:
        audio = np.zeros(10, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio, sample_rate=44100)
        buf = io.BytesIO(wav)
        buf.seek(24)  # sample rate offset in WAV header
        sr = struct.unpack("<I", buf.read(4))[0]
        assert sr == 44100

    def test_clipping_high_values(self) -> None:
        """Values above 1.0 are clipped to int16 max."""
        audio = np.array([2.0], dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        # Extract the data portion (after 44-byte header)
        data = wav[44:]
        sample = struct.unpack("<h", data[:2])[0]
        assert sample == 32767

    def test_clipping_low_values(self) -> None:
        """Values below -1.0 are clipped to int16 min."""
        audio = np.array([-2.0], dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        data = wav[44:]
        sample = struct.unpack("<h", data[:2])[0]
        assert sample == -32768

    def test_zero_audio(self) -> None:
        audio = np.array([0.0], dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        data = wav[44:]
        sample = struct.unpack("<h", data[:2])[0]
        assert sample == 0

    def test_empty_audio(self) -> None:
        audio = np.array([], dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        assert wav[:4] == b"RIFF"
        # Data size should be 0
        data_size_offset = 40
        buf = io.BytesIO(wav)
        buf.seek(data_size_offset)
        data_size = struct.unpack("<I", buf.read(4))[0]
        assert data_size == 0

    def test_pcm_format_tag(self) -> None:
        """WAV header must indicate PCM format (1)."""
        audio = np.zeros(10, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        buf = io.BytesIO(wav)
        buf.seek(20)  # format tag offset
        fmt = struct.unpack("<H", buf.read(2))[0]
        assert fmt == 1

    def test_mono_channel(self) -> None:
        """WAV header must indicate 1 channel (mono)."""
        audio = np.zeros(10, dtype=np.float32)
        wav = _numpy_to_wav_bytes(audio)
        buf = io.BytesIO(wav)
        buf.seek(22)  # channels offset
        channels = struct.unpack("<H", buf.read(2))[0]
        assert channels == 1


# ===========================================================================
# _load_keyterms
# ===========================================================================


class TestLoadKeyterms:
    def test_returns_list(self) -> None:
        with patch("jarvis_engine._shared.load_personal_vocab_lines", return_value=["Conner", "Jarvis"]):
            result = _load_keyterms()
        assert isinstance(result, list)

    def test_passes_strip_parens(self) -> None:
        with patch("jarvis_engine._shared.load_personal_vocab_lines", return_value=[]) as mock_fn:
            _load_keyterms()
        mock_fn.assert_called_once_with(strip_parens=True)

    def test_returns_vocab_lines(self) -> None:
        expected = ["Conner", "Jarvis", "Austin"]
        with patch("jarvis_engine._shared.load_personal_vocab_lines", return_value=expected):
            result = _load_keyterms()
        assert result == expected


# ===========================================================================
# _try_deepgram
# ===========================================================================


class TestTryDeepgram:
    def test_returns_none_without_api_key(self) -> None:
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False):
            result = _try_deepgram(np.zeros(1000, dtype=np.float32), language="en")
        assert result is None

    def test_returns_none_when_httpx_missing(self) -> None:
        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": None}):
            result = _try_deepgram(np.zeros(1000, dtype=np.float32), language="en")
        assert result is None

    def test_successful_transcription(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "hello world",
                        "confidence": 0.95,
                        "words": [
                            {"word": "hello", "start": 0.0, "end": 0.5},
                            {"word": "world", "start": 0.5, "end": 1.0},
                        ],
                    }]
                }]
            }
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": mock_httpx}), \
             patch("jarvis_engine.stt_backends._load_keyterms", return_value=["test"]):
            result = _try_deepgram(np.zeros(16000, dtype=np.float32), language="en")

        assert result is not None
        assert result.text == "hello world"
        assert result.confidence == 0.95
        assert result.backend == "deepgram-nova3"
        assert result.segments is not None
        assert len(result.segments) == 2

    def test_non_200_returns_none(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": mock_httpx}), \
             patch("jarvis_engine.stt_backends._load_keyterms", return_value=[]):
            result = _try_deepgram(np.zeros(16000, dtype=np.float32), language="en")

        assert result is None

    def test_empty_channels_returns_none(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": {"channels": []}}

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": mock_httpx}), \
             patch("jarvis_engine.stt_backends._load_keyterms", return_value=[]):
            result = _try_deepgram(np.zeros(16000, dtype=np.float32), language="en")

        assert result is None

    def test_file_path_input(self) -> None:
        """When audio is a file path string, it reads from file."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "file test",
                        "confidence": 0.9,
                    }]
                }]
            }
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": mock_httpx}), \
             patch("jarvis_engine.stt_backends._load_keyterms", return_value=[]), \
             patch("builtins.open", MagicMock(return_value=io.BytesIO(b"fake wav data"))):
            result = _try_deepgram("/tmp/test.wav", language="en")

        assert result is not None
        assert result.text == "file test"

    def test_uses_provided_keyterms(self) -> None:
        """Explicit keyterms list is used instead of auto-loading."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "test",
                        "confidence": 0.8,
                    }]
                }]
            }
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import sys
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False), \
             patch.dict(sys.modules, {"httpx": mock_httpx}), \
             patch("jarvis_engine.stt_backends._load_keyterms") as mock_load:
            _try_deepgram(np.zeros(16000, dtype=np.float32), language="en", keyterms=["custom"])

        # _load_keyterms should NOT have been called when keyterms provided
        mock_load.assert_not_called()


# ===========================================================================
# record_from_microphone
# ===========================================================================


class TestRecordFromMicrophone:
    def test_raises_without_sounddevice(self) -> None:
        import sys
        with patch.dict(sys.modules, {"sounddevice": None}):
            with pytest.raises(RuntimeError, match="sounddevice"):
                record_from_microphone()

    def test_returns_numpy_array(self) -> None:
        """Basic recording returns float32 numpy array."""
        mock_sd = MagicMock()
        mock_stream = MagicMock()

        # Return silent audio for 1 chunk then trigger silence exit
        chunk = np.zeros((160, 1), dtype=np.float32)
        mock_stream.read.return_value = (chunk, False)
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_sd.InputStream.return_value = mock_stream

        import sys
        with patch.dict(sys.modules, {"sounddevice": mock_sd}), \
             patch("jarvis_engine.stt_backends.sd", mock_sd, create=True):
            # Patch the import inside the function
            import jarvis_engine.stt_backends as stt_mod
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def _mock_sd(name, *args, **kwargs):
                if name == "sounddevice":
                    return mock_sd
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_mock_sd):
                result = record_from_microphone(max_duration_seconds=0.1, sample_rate=16000)

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32

    def test_empty_frames_returns_empty_array(self) -> None:
        """If no frames recorded, returns empty array."""
        mock_sd = MagicMock()
        mock_stream = MagicMock()
        # Raise OSError to trigger RuntimeError
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        # Return 0-length read to simulate empty
        mock_stream.read.return_value = (np.zeros((0, 1), dtype=np.float32), False)
        mock_sd.InputStream.return_value = mock_stream

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def _mock_sd(name, *args, **kwargs):
            if name == "sounddevice":
                return mock_sd
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_sd):
            result = record_from_microphone(max_duration_seconds=0.01, sample_rate=16000)

        assert isinstance(result, np.ndarray)

    def test_os_error_raises_runtime_error(self) -> None:
        """OSError from microphone hardware raises RuntimeError."""
        mock_sd = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(side_effect=OSError("No audio device"))
        mock_sd.InputStream.return_value = mock_stream

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def _mock_sd(name, *args, **kwargs):
            if name == "sounddevice":
                return mock_sd
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_sd):
            with pytest.raises(RuntimeError, match="Microphone recording failed"):
                record_from_microphone()

    def test_drain_seconds(self) -> None:
        """drain_seconds causes initial audio to be discarded."""
        mock_sd = MagicMock()
        mock_stream = MagicMock()

        chunk = np.zeros((160, 1), dtype=np.float32)
        mock_stream.read.return_value = (chunk, False)
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_sd.InputStream.return_value = mock_stream

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def _mock_sd(name, *args, **kwargs):
            if name == "sounddevice":
                return mock_sd
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_sd):
            record_from_microphone(
                max_duration_seconds=0.1,
                sample_rate=16000,
                drain_seconds=0.1,
            )

        # First read call should be the drain (16000 * 0.1 = 1600 samples)
        first_read_args = mock_stream.read.call_args_list[0]
        assert first_read_args[0][0] == 1600
