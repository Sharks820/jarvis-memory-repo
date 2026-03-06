"""Tests for transcribe_smart multi-backend pipeline, fallback chain, and integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 14. Confidence retry triggers when confidence < 0.6
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_triggers_on_low_confidence() -> None:
    """When earlier backends return low confidence, fallback chain continues to find better result."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Parakeet: returns low confidence
    low_conf_result = TranscriptionResult(
        text="hmm maybe",
        language="en",
        confidence=0.4,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )
    # Groq: returns higher confidence (above threshold)
    high_conf_result = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.9,
        duration_seconds=1.0,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_conf_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=high_conf_result), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello jarvis"
    assert result.confidence == 0.9
    assert result.backend == "groq-whisper"


# ---------------------------------------------------------------------------
# 15. Higher-confidence result is kept after retry
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_keeps_higher_confidence() -> None:
    """When later backends return lower confidence, keep the best result."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Parakeet returns low confidence
    parakeet_result = TranscriptionResult(
        text="set timer",
        language="en",
        confidence=0.5,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )
    # Groq returns even lower
    groq_result = TranscriptionResult(
        text="set time",
        language="en",
        confidence=0.3,
        duration_seconds=1.0,
        backend="groq-whisper",
    )
    # Emergency local also returns lower
    local_result = TranscriptionResult(
        text="set time",
        language="en",
        confidence=0.2,
        duration_seconds=2.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=groq_result), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=local_result), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Parakeet result should be kept (highest confidence)
    assert result.text == "set timer"
    assert result.confidence == 0.5
    assert result.backend == "parakeet-tdt"


# ---------------------------------------------------------------------------
# 16. No retry when confidence >= 0.6
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_no_retry_when_confidence_sufficient() -> None:
    """When first backend returns high confidence, chain stops immediately."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="turn on lights",
        language="en",
        confidence=0.85,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=good_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "turn on lights"
    assert result.confidence == 0.85
    assert result.retried is False
    # Only parakeet should have been called (high confidence -> chain stops)
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# 17. Metric logging writes correct JSONL
# ---------------------------------------------------------------------------

def test_stt_metric_logging_writes_jsonl() -> None:
    """_log_stt_metric writes valid JSONL to the expected path."""
    from jarvis_engine.stt import _log_stt_metric

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _log_stt_metric(
            root,
            backend="groq-whisper",
            confidence=0.92,
            latency_ms=450.3,
            text_length=15,
            retried=False,
        )
        _log_stt_metric(
            root,
            backend="faster-whisper",
            confidence=0.78,
            latency_ms=1200.0,
            text_length=22,
            retried=True,
        )

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        assert metrics_path.exists()

        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        record1 = json.loads(lines[0])
        assert record1["backend"] == "groq-whisper"
        assert record1["confidence"] == 0.92
        assert record1["latency_ms"] == 450.3
        assert record1["text_length"] == 15
        assert record1["retried"] is False
        assert "ts" in record1

        record2 = json.loads(lines[1])
        assert record2["backend"] == "faster-whisper"
        assert record2["confidence"] == 0.78
        assert record2["retried"] is True


# ---------------------------------------------------------------------------
# 18. Metric logging with None root_dir is a no-op
# ---------------------------------------------------------------------------

def test_stt_metric_logging_none_root_is_noop() -> None:
    """_log_stt_metric with root_dir=None should silently do nothing."""
    from jarvis_engine.stt import _log_stt_metric

    # Should not raise -- None root_dir means no file to write to
    result = _log_stt_metric(
        None,
        backend="groq-whisper",
        confidence=0.95,
        latency_ms=300.0,
        text_length=10,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 19. Retry is graceful -- failed retry returns original result
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_graceful_on_failure() -> None:
    """If only one backend returns a result, that low-confidence result is used."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_conf = TranscriptionResult(
        text="something",
        language="en",
        confidence=0.3,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=low_conf), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Low-confidence result returned (only backend that worked)
    assert result.text == "something"
    assert result.confidence == 0.3


# ---------------------------------------------------------------------------
# 20. Local primary retries with Groq when GROQ_API_KEY available
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_auto_mode_tries_all_chain_backends() -> None:
    """In auto mode with low-confidence results, the fallback chain tries all backends."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_result = TranscriptionResult(
        text="maybe hello",
        language="en",
        confidence=0.4,
        duration_seconds=1.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram", return_value=None) as mock_dg, \
         patch("jarvis_engine.stt._try_groq", return_value=None) as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency", return_value=low_result) as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "maybe hello"
    assert result.confidence == 0.4
    # All backends in the chain should have been tried
    mock_pk.assert_called_once()
    mock_dg.assert_called_once()
    mock_groq.assert_called_once()
    mock_local.assert_called_once()


# ---------------------------------------------------------------------------
# 21. Metrics logged during transcribe_smart with root_dir
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_logs_metrics_with_root_dir() -> None:
    """transcribe_smart logs metrics when root_dir is provided."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="turn on lights",
        language="en",
        confidence=0.92,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        with patch("jarvis_engine.stt._try_parakeet", return_value=good_result), \
             patch("jarvis_engine.stt._try_deepgram", return_value=None), \
             patch("jarvis_engine.stt._try_groq", return_value=None), \
             patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
             patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
             patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
            result = transcribe_smart(fake_audio, root_dir=root)

        assert result.text == "turn on lights"

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        assert metrics_path.exists()
        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["confidence"] == 0.92


# ---------------------------------------------------------------------------
# 32. transcribe_smart forced groq backend
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "groq"}, clear=False)
def test_transcribe_smart_forced_groq() -> None:
    """JARVIS_STT_BACKEND=groq forces Groq and skips local fallback."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    groq_result = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=0.3, backend="groq-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=groq_result) as mock_groq, \
         patch("jarvis_engine.stt._try_local") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello"
    assert result.backend == "groq-whisper"
    mock_groq.assert_called_once()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# 33. transcribe_smart forced local backend
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "local"}, clear=False)
def test_transcribe_smart_forced_local() -> None:
    """JARVIS_STT_BACKEND=local forces local and skips Groq."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    local_result = TranscriptionResult(
        text="local hello", language="en", confidence=0.8,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_local", return_value=local_result), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_parakeet") as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.backend == "faster-whisper"
    mock_groq.assert_not_called()
    mock_pk.assert_not_called()
    mock_dg.assert_not_called()


# ---------------------------------------------------------------------------
# 34. transcribe_smart auto mode -- all backends fail
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_all_backends_fail() -> None:
    """When all backends fail, returns empty result with backend='none'."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "none"


# ---------------------------------------------------------------------------
# 45. Metric logging thread safety (concurrent writes)
# ---------------------------------------------------------------------------

def test_metric_logging_concurrent_writes() -> None:
    """Multiple concurrent _log_stt_metric calls don't corrupt the file."""
    import threading
    from jarvis_engine.stt import _log_stt_metric

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=_log_stt_metric,
                args=(root,),
                kwargs={
                    "backend": f"backend-{i}",
                    "confidence": 0.8,
                    "latency_ms": 100.0,
                    "text_length": 10,
                },
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 10

        for line in lines:
            record = json.loads(line)
            assert "backend" in record
            assert "confidence" in record


# ---------------------------------------------------------------------------
# XX. transcribe_smart calls preprocess_audio
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_transcribe_smart_calls_preprocess() -> None:
    """transcribe_smart calls preprocess_audio on numpy audio."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    mock_result = TranscriptionResult(
        text="hello world",
        language="en",
        confidence=0.9,
        duration_seconds=0.5,
        backend="groq",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=mock_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio) as mock_preprocess, \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="hello world"):
        result = transcribe_smart(fake_audio)
        mock_preprocess.assert_called_once()


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_calls_postprocess() -> None:
    """transcribe_smart calls postprocess_transcription on result text."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    mock_result = TranscriptionResult(
        text="um hello conner",
        language="en",
        confidence=0.8,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=mock_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="Hello, Conner!") as mock_post:
        mock_gateway = MagicMock()
        result = transcribe_smart(fake_audio, gateway=mock_gateway, entity_list=["Conner"])
        mock_post.assert_called_once_with(
            "um hello conner",
            0.8,
            gateway=mock_gateway,
            entity_list=["Conner"],
        )
        assert result.text == "Hello, Conner!"


def test_transcribe_smart_skips_preprocess_for_file_path() -> None:
    """transcribe_smart does NOT preprocess when audio is a file path string."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    mock_result = TranscriptionResult(
        text="hello world",
        language="en",
        confidence=0.9,
        duration_seconds=0.5,
        backend="local",
    )

    with patch.dict("os.environ", {"JARVIS_STT_BACKEND": "local"}), \
         patch("jarvis_engine.stt._try_local", return_value=mock_result), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio") as mock_preprocess, \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="hello world"):
        result = transcribe_smart("/tmp/audio.wav")
        mock_preprocess.assert_not_called()


# ===========================================================================
# 4-tier fallback chain tests (Plan 01-04)
# ===========================================================================


# ---------------------------------------------------------------------------
# FC1. test_transcribe_smart_fallback_chain_order
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_fallback_chain_order():
    """Fallback chain tries backends in order: parakeet -> deepgram -> groq -> local."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    call_order = []

    def mock_parakeet(*a, **kw):
        call_order.append("parakeet")
        return None

    def mock_deepgram(*a, **kw):
        call_order.append("deepgram")
        return TranscriptionResult(
            text="low conf", language="en", confidence=0.3,
            duration_seconds=0.5, backend="deepgram-nova3",
        )

    def mock_groq(*a, **kw):
        call_order.append("groq")
        return TranscriptionResult(
            text="hello jarvis", language="en", confidence=0.9,
            duration_seconds=0.4, backend="groq-whisper",
        )

    def mock_local(*a, **kw):
        call_order.append("local")
        return None

    with patch("jarvis_engine.stt._try_parakeet", side_effect=mock_parakeet), \
         patch("jarvis_engine.stt._try_deepgram", side_effect=mock_deepgram), \
         patch("jarvis_engine.stt._try_groq", side_effect=mock_groq), \
         patch("jarvis_engine.stt._try_local_emergency", side_effect=mock_local), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Groq result is used (highest confidence, above threshold)
    assert result.text == "hello jarvis"
    assert result.confidence == 0.9
    assert result.backend == "groq-whisper"
    # Chain tried parakeet, deepgram, groq in order (stopped at groq due to high confidence)
    assert call_order == ["parakeet", "deepgram", "groq"]


# ---------------------------------------------------------------------------
# FC2. test_transcribe_smart_parakeet_primary
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_parakeet_primary():
    """Parakeet with high confidence stops the chain immediately."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="hello world", language="en", confidence=0.95,
        duration_seconds=0.2, backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=good_result), \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello world"
    assert result.backend == "parakeet-tdt"
    # Other backends should NOT have been called
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC3. test_transcribe_smart_all_fail_fallback_chain
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_all_fail_fallback_chain():
    """All backends returning None gives empty result with backend='none'."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "none"


# ---------------------------------------------------------------------------
# FC4. test_transcribe_smart_forced_parakeet
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "parakeet"}, clear=False)
def test_transcribe_smart_forced_parakeet():
    """JARVIS_STT_BACKEND=parakeet forces only parakeet backend."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    pk_result = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=0.3, backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=pk_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello"
    assert result.backend == "parakeet-tdt"
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC5. test_transcribe_smart_forced_deepgram
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "deepgram", "DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_transcribe_smart_forced_deepgram():
    """JARVIS_STT_BACKEND=deepgram forces only deepgram backend."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    dg_result = TranscriptionResult(
        text="hello jarvis", language="en", confidence=0.88,
        duration_seconds=0.4, backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_deepgram", return_value=dg_result) as mock_dg, \
         patch("jarvis_engine.stt._try_parakeet") as mock_pk, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello jarvis"
    assert result.backend == "deepgram-nova3"
    mock_dg.assert_called_once()
    mock_pk.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC6. test_transcribe_smart_low_confidence_fallthrough
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_low_confidence_fallthrough():
    """Parakeet with low confidence falls through to next backends."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_conf = TranscriptionResult(
        text="maybe hello", language="en", confidence=0.3,
        duration_seconds=0.2, backend="parakeet-tdt",
    )
    high_conf = TranscriptionResult(
        text="hello jarvis", language="en", confidence=0.85,
        duration_seconds=0.5, backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_conf), \
         patch("jarvis_engine.stt._try_deepgram", return_value=high_conf), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Deepgram result used (above threshold)
    assert result.text == "hello jarvis"
    assert result.confidence == 0.85
    assert result.backend == "deepgram-nova3"
    # Groq and local should NOT have been called
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC7. test_fallback_chain_has_four_entries
# ---------------------------------------------------------------------------

def test_fallback_chain_has_four_entries():
    """FALLBACK_CHAIN module constant has 4 backend entries in correct order."""
    from jarvis_engine.stt import FALLBACK_CHAIN
    assert len(FALLBACK_CHAIN) == 4
    assert FALLBACK_CHAIN[0] == "parakeet"
    assert FALLBACK_CHAIN[1] == "deepgram"
    assert FALLBACK_CHAIN[2] == "groq"
    assert FALLBACK_CHAIN[3] == "local"


# ---------------------------------------------------------------------------
# FC8. test_try_local_emergency_uses_large_v3
# ---------------------------------------------------------------------------

def test_try_local_emergency_uses_large_v3():
    """_try_local_emergency creates SpeechToText with model_size='large-v3'."""
    import jarvis_engine.stt as stt_mod

    # Reset the singleton
    original = stt_mod._local_emergency_instance
    stt_mod._local_emergency_instance = None

    try:
        with patch("jarvis_engine.stt.SpeechToText") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.transcribe_audio.return_value = MagicMock(
                text="hello", language="en", confidence=0.8,
                duration_seconds=1.0, backend="faster-whisper"
            )
            mock_cls.return_value = mock_instance

            stt_mod._try_local_emergency(
                np.zeros(16000, dtype=np.float32), language="en"
            )

            # Verify large-v3 model was requested
            mock_cls.assert_called_once_with(model_size="large-v3")
    finally:
        stt_mod._local_emergency_instance = original


# ---------------------------------------------------------------------------
# FC9. test_forced_parakeet_failure
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "parakeet"}, clear=False)
def test_forced_parakeet_failure():
    """Forced parakeet mode returns error result when parakeet fails."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.backend == "parakeet-failed"


# ===========================================================================
# INTEGRATION TESTS: Full STT pipeline end-to-end (Plan 01-05)
# ===========================================================================


# ---------------------------------------------------------------------------
# INT-1. test_full_pipeline_parakeet_happy_path
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_parakeet_happy_path():
    """Parakeet returns high-confidence result; post-processing applied, metric logged,
    other backends never called."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    parakeet_result = TranscriptionResult(
        text="turn on the lights",
        language="en",
        confidence=0.94,
        duration_seconds=0.3,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t) as mock_pp, \
         patch("jarvis_engine.stt._log_stt_metric") as mock_metric:
        result = transcribe_smart(fake_audio)

    # Parakeet result used
    assert result.text == "turn on the lights"
    assert result.backend == "parakeet-tdt"
    assert result.confidence == 0.94
    # Post-processing was called
    mock_pp.assert_called_once()
    # Metric was logged with correct backend
    mock_metric.assert_called_once()
    assert mock_metric.call_args[1]["backend"] == "parakeet-tdt"
    # Other backends never called
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-2/3/4. Full pipeline fallback to specific backend (parametrized)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env_vars, expected_text, expected_backend, expected_confidence, "
    "pk_return, dg_return, groq_return, local_return",
    [
        pytest.param(
            {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"},
            "set a timer for five minutes", "deepgram-nova3", 0.92,
            None, "winner", None, None,
            id="fallback_to_deepgram",
        ),
        pytest.param(
            {"GROQ_API_KEY": "fake-key", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"},
            "what time is it", "groq-whisper", 0.88,
            None, None, "winner", None,
            id="fallback_to_groq",
        ),
        pytest.param(
            {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"},
            "brain status", "faster-whisper", 0.75,
            None, None, None, "winner",
            id="fallback_to_emergency_local",
        ),
    ],
)
def test_full_pipeline_fallback_to_backend(
    env_vars, expected_text, expected_backend, expected_confidence,
    pk_return, dg_return, groq_return, local_return,
) -> None:
    """Earlier backends fail (return None), correct fallback backend succeeds."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    success_result = TranscriptionResult(
        text=expected_text,
        language="en",
        confidence=expected_confidence,
        duration_seconds=1.0,
        backend=expected_backend,
    )

    # Replace "winner" sentinel with the actual result, keep None as None
    def _resolve(val):
        return success_result if val == "winner" else val

    with patch.dict("os.environ", env_vars, clear=False), \
         patch("jarvis_engine.stt._try_parakeet", return_value=_resolve(pk_return)), \
         patch("jarvis_engine.stt._try_deepgram", return_value=_resolve(dg_return)), \
         patch("jarvis_engine.stt._try_groq", return_value=_resolve(groq_return)), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=_resolve(local_return)), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == expected_text
    assert result.backend == expected_backend
    assert result.confidence == expected_confidence


# ---------------------------------------------------------------------------
# INT-5. test_full_pipeline_confidence_fallthrough
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_confidence_fallthrough():
    """Parakeet returns confidence=0.4 (below threshold), Deepgram returns 0.9.
    Deepgram result used because it exceeds the threshold."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_result = TranscriptionResult(
        text="muffled words",
        language="en",
        confidence=0.4,
        duration_seconds=0.3,
        backend="parakeet-tdt",
    )
    high_result = TranscriptionResult(
        text="clear words here",
        language="en",
        confidence=0.9,
        duration_seconds=0.8,
        backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=high_result), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Deepgram result used (confidence 0.9 >= threshold 0.6)
    assert result.text == "clear words here"
    assert result.backend == "deepgram-nova3"
    assert result.confidence == 0.9
    # Chain stopped at Deepgram (no need for Groq/local)
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-6. test_full_pipeline_postprocessing_integration
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_postprocessing_integration():
    """Post-processing pipeline transforms backend output: filler removal + entity correction."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Backend returns text with fillers
    raw_result = TranscriptionResult(
        text="um uh Hello Connor how are you doing today",
        language="en",
        confidence=0.85,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    # Mock postprocess_transcription to simulate filler removal + entity correction
    def mock_postprocess(text, confidence, **kwargs):
        return "Hello Conner how are you doing today"

    with patch("jarvis_engine.stt._try_parakeet", return_value=raw_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=mock_postprocess):
        result = transcribe_smart(fake_audio)

    # Post-processing applied: fillers removed, entity corrected
    assert result.text == "Hello Conner how are you doing today"
    assert "um" not in result.text
    assert "uh" not in result.text
    assert "Connor" not in result.text


# ---------------------------------------------------------------------------
# INT-7. test_full_pipeline_personal_vocab_flows
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_personal_vocab_flows():
    """Personal vocab flows to Deepgram as keyterms and to post-processing as entity_list."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    deepgram_result = TranscriptionResult(
        text="ask Conner about the project",
        language="en",
        confidence=0.91,
        duration_seconds=0.7,
        backend="deepgram-nova3",
    )

    vocab_terms = ["Conner", "Jarvis", "Ollama"]

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=deepgram_result) as mock_dg, \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt._load_keyterms", return_value=vocab_terms), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t) as mock_pp:
        result = transcribe_smart(fake_audio, entity_list=vocab_terms)

    # Verify Deepgram received keyterms
    dg_call_kwargs = mock_dg.call_args[1]
    assert dg_call_kwargs["keyterms"] == vocab_terms

    # Verify postprocess_transcription received entity_list
    pp_call_kwargs = mock_pp.call_args[1]
    assert pp_call_kwargs["entity_list"] == vocab_terms

    assert result.text == "ask Conner about the project"


# ---------------------------------------------------------------------------
# INT-8. test_listen_and_transcribe_uses_new_pipeline
# ---------------------------------------------------------------------------

def test_listen_and_transcribe_uses_new_pipeline():
    """listen_and_transcribe() calls record_from_microphone + transcribe_smart
    and returns the result from the new fallback chain."""
    from jarvis_engine.stt import TranscriptionResult, listen_and_transcribe

    fake_audio = np.zeros(32000, dtype=np.float32)
    pipeline_result = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.92,
        duration_seconds=1.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio) as mock_record, \
         patch("jarvis_engine.stt.transcribe_smart", return_value=pipeline_result) as mock_smart:
        result = listen_and_transcribe(max_duration_seconds=10.0)

    # record_from_microphone was called
    mock_record.assert_called_once_with(max_duration_seconds=10.0)
    # transcribe_smart was called with the recorded audio
    mock_smart.assert_called_once()
    call_args = mock_smart.call_args
    np.testing.assert_array_equal(call_args[0][0], fake_audio)
    # Result comes from transcribe_smart
    assert result.text == "hello jarvis"
    assert result.backend == "parakeet-tdt"


# ---------------------------------------------------------------------------
# INT-9. test_caller_voice_handler_integration
# ---------------------------------------------------------------------------

def test_caller_voice_handler_integration():
    """VoiceListenHandler calls listen_and_transcribe() and works with new return format."""
    from jarvis_engine.stt import TranscriptionResult
    from jarvis_engine.handlers.voice_handlers import VoiceListenHandler
    from jarvis_engine.commands.voice_commands import VoiceListenCommand

    pipeline_result = TranscriptionResult(
        text="set timer for five minutes",
        language="en",
        confidence=0.93,
        duration_seconds=2.1,
        backend="deepgram-nova3",
        segments=[{"start": 0.0, "end": 2.0, "text": "set timer for five minutes"}],
    )

    handler = VoiceListenHandler(root=Path("."))

    with patch("jarvis_engine.stt.listen_and_transcribe", return_value=pipeline_result):
        result = handler.handle(VoiceListenCommand())

    assert result.text == "set timer for five minutes"
    assert result.confidence == 0.93
    assert result.duration_seconds == 2.1
    assert result.message == ""


# ---------------------------------------------------------------------------
# INT-10. test_caller_proactive_handler_integration
# ---------------------------------------------------------------------------

def test_caller_proactive_handler_integration():
    """WakeWordStartHandler._on_detected() callback works with new
    record_from_microphone (Silero VAD) and transcribe_smart (fallback chain)."""
    from jarvis_engine.stt import TranscriptionResult
    from jarvis_engine.handlers.proactive_handlers import WakeWordStartHandler
    from jarvis_engine.commands.proactive_commands import WakeWordStartCommand

    fake_audio = np.zeros(16000, dtype=np.float32)
    pipeline_result = TranscriptionResult(
        text="jarvis brain status",
        language="en",
        confidence=0.95,
        duration_seconds=1.0,
        backend="parakeet-tdt",
    )

    handler = WakeWordStartHandler(root=Path("."))

    # Mock the WakeWordDetector so start() captures the on_detected callback
    # then we invoke it manually to test the integration.
    # WakeWordDetector is lazy-imported inside handle(), so patch at its source.
    captured_callback = {}

    class MockDetector:
        def __init__(self, **kwargs):
            self.pause_called = False
            self.resume_called = False

        def start(self, on_detected, stop_event=None, mic_lock=None):
            captured_callback["fn"] = on_detected
            # Simulate detection loop ending immediately
            if stop_event:
                stop_event.set()

        def pause(self):
            self.pause_called = True

        def resume(self, sd_module=None):
            self.resume_called = True

    with patch("jarvis_engine.wakeword.WakeWordDetector", MockDetector):
        handler.handle(WakeWordStartCommand(threshold=0.5))

    # Now invoke the captured callback with mocked STT functions
    assert "fn" in captured_callback, "on_detected callback should have been captured"

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=pipeline_result), \
         patch("jarvis_engine.stt_postprocess._load_personal_vocab", return_value=["Conner"]), \
         patch("jarvis_engine.handlers.proactive_handlers._time_mod") as mock_time:
        mock_time.sleep = MagicMock()
        mock_time.time.return_value = 0.0
        # The callback will try to dispatch via cmd_voice_run_impl
        with patch("jarvis_engine.voice_pipeline.cmd_voice_run_impl"):
            with patch("jarvis_engine.config.repo_root", return_value=Path(".")):
                captured_callback["fn"]()

    # If we got here without error, the callback worked with the new pipeline
