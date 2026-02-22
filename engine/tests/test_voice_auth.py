from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from jarvis_engine.voice_auth import enroll_voiceprint, verify_voiceprint


def _write_tone(path: Path, freq: float, *, duration_s: float = 1.6, rate: int = 16000, phase: float = 0.0) -> None:
    t = np.linspace(0.0, duration_s, int(duration_s * rate), endpoint=False)
    base = 0.5 * np.sin((2.0 * np.pi * freq * t) + phase)
    # Add low-amplitude harmonic/noise so signals are not perfectly identical.
    signal = base + 0.03 * np.sin((2.0 * np.pi * (freq * 2.0) * t) + (phase / 3.0))
    signal += 0.01 * np.random.default_rng(42).normal(size=signal.shape)
    signal = np.clip(signal, -1.0, 1.0)
    pcm = (signal * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


def test_voice_auth_enroll_and_verify(tmp_path: Path) -> None:
    same_a = tmp_path / "same_a.wav"
    same_b = tmp_path / "same_b.wav"
    diff = tmp_path / "diff.wav"
    _write_tone(same_a, 190.0, phase=0.0)
    _write_tone(same_b, 190.0, phase=0.25)
    _write_tone(diff, 720.0, phase=0.0)

    enrolled = enroll_voiceprint(tmp_path, user_id="Conner", wav_path=str(same_a), replace=True)
    assert enrolled.samples == 1

    same_score = verify_voiceprint(tmp_path, user_id="Conner", wav_path=str(same_b), threshold=0.0)
    diff_score = verify_voiceprint(tmp_path, user_id="Conner", wav_path=str(diff), threshold=0.0)
    assert same_score.score > diff_score.score

    threshold = (same_score.score + diff_score.score) / 2.0
    assert verify_voiceprint(tmp_path, user_id="Conner", wav_path=str(same_b), threshold=threshold).matched is True
    assert verify_voiceprint(tmp_path, user_id="Conner", wav_path=str(diff), threshold=threshold).matched is False
