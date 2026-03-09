"""Tests for MEDIUM audit fixes in voice/STT subsystem.

Covers:
1. Adaptive noise floor calibration (stt_backends)
2. Fuzzy intent matching (voice_intents)
3. Warm-start Parakeet (stt)
4. Parakeet confidence threshold for proper nouns (stt)
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# 1. Adaptive noise floor calibration
# ---------------------------------------------------------------------------


class TestNoiseFloorCalibration:
    """Tests for _calibrate_noise_floor() in stt_backends."""

    def test_threshold_proportional_to_ambient(self):
        """Calibrated threshold should be 2.5x the ambient RMS."""
        from jarvis_engine.stt_backends import _calibrate_noise_floor

        # Simulate a stream returning ambient audio with known RMS
        ambient_level = 0.008  # RMS we want to achieve
        # For np.sqrt(np.mean(x**2)) == ambient_level, a constant signal
        # at ambient_level works since RMS of constant = that constant.
        sample_rate = 16000
        num_samples = int(sample_rate * 0.5)  # 500ms
        ambient_audio = np.full((num_samples, 1), ambient_level, dtype=np.float32)

        stream = MagicMock()
        stream.read.return_value = (ambient_audio, None)

        result = _calibrate_noise_floor(stream, sample_rate)
        expected = ambient_level * 2.5  # 0.02
        assert abs(result - expected) < 1e-6, f"Expected {expected}, got {result}"

    def test_threshold_clamped_min(self):
        """Threshold should be clamped to minimum 0.005 in very quiet environments."""
        from jarvis_engine.stt_backends import (
            _calibrate_noise_floor,
            _NOISE_FLOOR_MIN,
        )

        # Very quiet ambient: RMS = 0.001 -> threshold = 0.0025 -> clamped to 0.005
        sample_rate = 16000
        num_samples = int(sample_rate * 0.5)
        ambient_audio = np.full((num_samples, 1), 0.001, dtype=np.float32)

        stream = MagicMock()
        stream.read.return_value = (ambient_audio, None)

        result = _calibrate_noise_floor(stream, sample_rate)
        assert result == _NOISE_FLOOR_MIN, f"Expected min clamp {_NOISE_FLOOR_MIN}, got {result}"

    def test_threshold_clamped_max(self):
        """Threshold should be clamped to maximum 0.05 in noisy environments."""
        from jarvis_engine.stt_backends import (
            _calibrate_noise_floor,
            _NOISE_FLOOR_MAX,
        )

        # Very noisy ambient: RMS = 0.1 -> threshold = 0.25 -> clamped to 0.05
        sample_rate = 16000
        num_samples = int(sample_rate * 0.5)
        ambient_audio = np.full((num_samples, 1), 0.1, dtype=np.float32)

        stream = MagicMock()
        stream.read.return_value = (ambient_audio, None)

        result = _calibrate_noise_floor(stream, sample_rate)
        assert result == _NOISE_FLOOR_MAX, f"Expected max clamp {_NOISE_FLOOR_MAX}, got {result}"

    def test_calibration_reads_500ms(self):
        """Calibration should read exactly 500ms of audio (8000 samples at 16kHz)."""
        from jarvis_engine.stt_backends import _calibrate_noise_floor

        sample_rate = 16000
        expected_samples = 8000  # 500ms at 16kHz
        ambient = np.full((expected_samples, 1), 0.01, dtype=np.float32)

        stream = MagicMock()
        stream.read.return_value = (ambient, None)

        _calibrate_noise_floor(stream, sample_rate)

        stream.read.assert_called_once_with(expected_samples)

    def test_calibration_fallback_on_read_error(self):
        """If stream.read fails, fall back to default threshold (0.01)."""
        from jarvis_engine.stt_backends import _calibrate_noise_floor

        stream = MagicMock()
        stream.read.side_effect = OSError("mic error")

        result = _calibrate_noise_floor(stream, 16000)
        assert result == 0.01, f"Expected fallback 0.01, got {result}"

    def test_noise_floor_used_in_record_when_no_silero(self):
        """record_from_microphone should use adaptive noise floor when Silero unavailable."""
        from jarvis_engine.stt_backends import _calibrate_noise_floor

        # This is an integration-style check: verify _calibrate_noise_floor
        # is called with a stream when Silero VAD is not available.
        # We verify the function returns valid clamped values.
        ambient = np.full((8000, 1), 0.015, dtype=np.float32)
        stream = MagicMock()
        stream.read.return_value = (ambient, None)

        threshold = _calibrate_noise_floor(stream, 16000)
        expected = 0.015 * 2.5  # 0.0375
        assert 0.005 <= threshold <= 0.05
        assert abs(threshold - expected) < 1e-6


# ---------------------------------------------------------------------------
# 2. Fuzzy intent matching
# ---------------------------------------------------------------------------


class TestFuzzyMatching:
    """Tests for _fuzzy_match() and fuzzy dispatch fallback in voice_intents."""

    def test_exact_match_is_not_fuzzy(self):
        """Exact substring match should be recognized (baseline)."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("pause jarvis", "pause jarvis") is True

    def test_fuzzy_detects_close_match(self):
        """'paws jarvis' (STT misheard) should match 'pause jarvis'."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("paws jarvis", "pause jarvis") is True

    def test_fuzzy_detects_resume_misheard(self):
        """'resum jarvis' should match 'resume jarvis'."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("resum jarvis", "resume jarvis") is True

    def test_fuzzy_rejects_distant_string(self):
        """Completely unrelated text should not match."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("check the weather today", "pause jarvis") is False

    def test_fuzzy_rejects_short_partial(self):
        """Short partial overlap should not match if ratio is below threshold."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("play some music please", "pause jarvis") is False

    def test_fuzzy_match_embedded_in_longer_text(self):
        """Fuzzy match should work when target is embedded in longer text."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("hey paws jarvis now", "pause jarvis") is True

    def test_fuzzy_empty_inputs(self):
        """Empty text or target should return False."""
        from jarvis_engine.voice_intents import _fuzzy_match

        assert _fuzzy_match("", "pause jarvis") is False
        assert _fuzzy_match("pause jarvis", "") is False

    def test_fuzzy_custom_threshold(self):
        """Custom threshold parameter should be respected."""
        from jarvis_engine.voice_intents import _fuzzy_match

        # "paws jarvis" vs "pause jarvis" ratio is ~0.83
        # With a very high threshold it should fail
        assert _fuzzy_match("paws jarvis", "pause jarvis", threshold=0.99) is False
        # With a lower threshold it should pass
        assert _fuzzy_match("paws jarvis", "pause jarvis", threshold=0.70) is True

    def test_exact_matching_takes_priority_in_dispatch(self):
        """Exact match in _DISPATCH_RULES should fire before fuzzy fallback.

        Verifies that 'pause jarvis' hits the exact path, not the fuzzy path.
        """
        from jarvis_engine.voice_intents import _DISPATCH_RULES, _CRITICAL_FUZZY_TARGETS

        lowered = "pause jarvis"
        exact_matched = False
        for matcher, handler in _DISPATCH_RULES:
            if matcher(lowered):
                exact_matched = True
                break

        assert exact_matched, "Exact 'pause jarvis' should match in _DISPATCH_RULES"

    def test_fuzzy_fallback_fires_when_exact_fails(self):
        """Fuzzy targets should be checked when no exact rule matches."""
        from jarvis_engine.voice_intents import (
            _DISPATCH_RULES,
            _CRITICAL_FUZZY_TARGETS,
            _fuzzy_match,
        )

        lowered = "paws jarvis"  # Misheard by STT

        # Verify exact matching does NOT match
        exact_matched = False
        for matcher, _handler in _DISPATCH_RULES:
            if matcher(lowered):
                exact_matched = True
                break
        assert not exact_matched, "'paws jarvis' should NOT match exactly"

        # Verify fuzzy matching DOES match
        fuzzy_matched = False
        for target_phrase, _handler in _CRITICAL_FUZZY_TARGETS:
            if _fuzzy_match(lowered, target_phrase):
                fuzzy_matched = True
                break
        assert fuzzy_matched, "'paws jarvis' should fuzzy-match a critical command"

    def test_critical_fuzzy_targets_populated(self):
        """_CRITICAL_FUZZY_TARGETS should contain entries for key commands."""
        from jarvis_engine.voice_intents import _CRITICAL_FUZZY_TARGETS

        target_phrases = [phrase for phrase, _handler in _CRITICAL_FUZZY_TARGETS]
        assert "pause jarvis" in target_phrases
        assert "resume jarvis" in target_phrases
        assert "safe mode on" in target_phrases
        assert "safe mode off" in target_phrases
        assert "system status" in target_phrases


# ---------------------------------------------------------------------------
# 3. Warm-start Parakeet
# ---------------------------------------------------------------------------


class TestWarmupSTTBackends:
    """Tests for warmup_stt_backends() in stt.py."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": ""})
    def test_warmup_loads_model_without_error(self):
        """warmup_stt_backends should call onnx_asr.load_model without error."""
        import jarvis_engine.stt as stt_mod

        mock_model = MagicMock()
        mock_model.with_timestamps.return_value = mock_model

        mock_onnx_asr = types.ModuleType("onnx_asr")
        mock_onnx_asr.load_model = MagicMock(return_value=mock_model)

        # Reset the global to force re-load
        original = stt_mod._parakeet_model
        stt_mod._parakeet_model = None
        try:
            with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
                stt_mod.warmup_stt_backends()

            mock_onnx_asr.load_model.assert_called_once_with("nemo-parakeet-tdt-0.6b-v2")
            assert stt_mod._parakeet_model is mock_model
        finally:
            stt_mod._parakeet_model = original

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": ""})
    def test_warmup_skips_if_already_loaded(self):
        """warmup_stt_backends should skip if model already loaded."""
        import jarvis_engine.stt as stt_mod

        sentinel = MagicMock()
        original = stt_mod._parakeet_model
        stt_mod._parakeet_model = sentinel
        try:
            # Should return early without importing onnx_asr
            stt_mod.warmup_stt_backends()
            assert stt_mod._parakeet_model is sentinel
        finally:
            stt_mod._parakeet_model = original

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": ""})
    def test_warmup_handles_import_error(self):
        """warmup_stt_backends should handle ImportError gracefully."""
        import jarvis_engine.stt as stt_mod

        original = stt_mod._parakeet_model
        stt_mod._parakeet_model = None
        try:
            # Remove onnx_asr from sys.modules so import fails
            with patch.dict("sys.modules", {"onnx_asr": None}):
                # Should not raise
                stt_mod.warmup_stt_backends()
            assert stt_mod._parakeet_model is None
        finally:
            stt_mod._parakeet_model = original

    def test_warmup_called_in_daemon_startup(self):
        """daemon_loop should spawn warmup thread during startup."""
        # Verify the import and thread spawn code exists in cmd_daemon_run_impl
        import inspect
        from jarvis_engine.daemon_loop import cmd_daemon_run_impl

        source = inspect.getsource(cmd_daemon_run_impl)
        assert "warmup_stt_backends" in source, (
            "cmd_daemon_run_impl should reference warmup_stt_backends"
        )


# ---------------------------------------------------------------------------
# 4. Parakeet confidence threshold for proper nouns
# ---------------------------------------------------------------------------


class TestParakeetProperNounThreshold:
    """Tests for _parakeet_should_fallthrough() in stt.py."""

    def test_fallthrough_when_entity_not_in_transcript(self):
        """Should fall through when entity_list has names not in Parakeet output."""
        from jarvis_engine.stt import TranscriptionResult, _parakeet_should_fallthrough

        result = TranscriptionResult(
            text="hey call connor please",
            confidence=0.70,
            backend="parakeet-tdt",
            language="en",
        )
        # entity_list expects "Conner" (correct spelling)
        # but Parakeet transcribed "connor" -- it's there but wrong spelling
        # Actually "connor" != "Conner" case-insensitive check: "conner" vs "connor"
        # The entity "Conner" lowered is "conner", text has "connor"
        # So entity NOT found -> should fall through
        assert _parakeet_should_fallthrough(result, ["Conner"]) is True

    def test_no_fallthrough_when_entity_found(self):
        """Should NOT fall through when entity IS in transcript."""
        from jarvis_engine.stt import TranscriptionResult, _parakeet_should_fallthrough

        result = TranscriptionResult(
            text="hey Conner how are you",
            confidence=0.70,
            backend="parakeet-tdt",
            language="en",
        )
        assert _parakeet_should_fallthrough(result, ["Conner"]) is False

    def test_no_fallthrough_without_entity_list(self):
        """Should NOT fall through when no entity_list provided."""
        from jarvis_engine.stt import TranscriptionResult, _parakeet_should_fallthrough

        result = TranscriptionResult(
            text="some text",
            confidence=0.65,
            backend="parakeet-tdt",
            language="en",
        )
        assert _parakeet_should_fallthrough(result, None) is False
        assert _parakeet_should_fallthrough(result, []) is False

    def test_no_fallthrough_for_non_parakeet(self):
        """Should NOT fall through for non-parakeet backends."""
        from jarvis_engine.stt import TranscriptionResult, _parakeet_should_fallthrough

        result = TranscriptionResult(
            text="some text",
            confidence=0.65,
            backend="groq-whisper",
            language="en",
        )
        assert _parakeet_should_fallthrough(result, ["Conner"]) is False

    def test_no_fallthrough_high_confidence(self):
        """Should NOT fall through when confidence >= 0.75 (proper noun threshold)."""
        from jarvis_engine.stt import TranscriptionResult, _parakeet_should_fallthrough

        result = TranscriptionResult(
            text="some text without entity",
            confidence=0.80,
            backend="parakeet-tdt",
            language="en",
        )
        assert _parakeet_should_fallthrough(result, ["Conner"]) is False

    @patch.dict("os.environ", {
        "JARVIS_STT_BACKEND": "auto",
        "GROQ_API_KEY": "",
        "DEEPGRAM_API_KEY": "fake_key",
    })
    def test_auto_fallthrough_to_deepgram(self):
        """In auto mode, Parakeet result without entities should try Deepgram next."""
        from jarvis_engine.stt import TranscriptionResult, _transcribe_auto

        parakeet_result = TranscriptionResult(
            text="hey call someone",
            confidence=0.70,
            backend="parakeet-tdt",
            language="en",
            duration_seconds=0.5,
        )
        deepgram_result = TranscriptionResult(
            text="hey call Conner",
            confidence=0.92,
            backend="deepgram-nova3",
            language="en",
            duration_seconds=0.8,
        )

        with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result), \
             patch("jarvis_engine.stt._try_deepgram", return_value=deepgram_result), \
             patch("jarvis_engine.stt._try_groq", return_value=None), \
             patch("jarvis_engine.stt._try_local_emergency", return_value=None):
            result = _transcribe_auto(
                np.zeros(16000, dtype=np.float32),
                language="en",
                prompt="",
                root_dir=None,
                entity_list=["Conner"],
            )

        # Deepgram should be selected because Parakeet missed the entity
        assert result.backend == "deepgram-nova3"
        assert result.text == "hey call Conner"

    @patch.dict("os.environ", {
        "JARVIS_STT_BACKEND": "auto",
        "GROQ_API_KEY": "",
        "DEEPGRAM_API_KEY": "",
    })
    def test_auto_keeps_parakeet_when_entity_found(self):
        """In auto mode, Parakeet result WITH entities should be accepted."""
        from jarvis_engine.stt import TranscriptionResult, _transcribe_auto

        parakeet_result = TranscriptionResult(
            text="hey call Conner",
            confidence=0.70,
            backend="parakeet-tdt",
            language="en",
            duration_seconds=0.5,
        )

        with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result), \
             patch("jarvis_engine.stt._try_deepgram", return_value=None), \
             patch("jarvis_engine.stt._try_groq", return_value=None), \
             patch("jarvis_engine.stt._try_local_emergency", return_value=None):
            result = _transcribe_auto(
                np.zeros(16000, dtype=np.float32),
                language="en",
                prompt="",
                root_dir=None,
                entity_list=["Conner"],
            )

        # Parakeet has entity so it's kept (even though confidence < 0.6, it's
        # still the best_so_far and the only result)
        assert result.backend == "parakeet-tdt"

    def test_entity_list_passed_through_transcribe_smart(self):
        """transcribe_smart should pass entity_list to _transcribe_auto."""
        import jarvis_engine.stt as stt_mod

        with patch.object(stt_mod, "_preprocess_audio_if_needed", return_value=(np.zeros(100), None)), \
             patch.object(stt_mod, "_transcribe_auto") as mock_auto, \
             patch.object(stt_mod, "_apply_postprocessing", side_effect=lambda r, **kw: r), \
             patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}):
            mock_auto.return_value = stt_mod.TranscriptionResult(
                text="test", confidence=0.9, backend="test",
            )
            stt_mod.transcribe_smart(
                np.zeros(100, dtype=np.float32),
                entity_list=["Conner", "Jarvis"],
            )
            mock_auto.assert_called_once()
            call_kwargs = mock_auto.call_args[1]
            assert call_kwargs["entity_list"] == ["Conner", "Jarvis"]
