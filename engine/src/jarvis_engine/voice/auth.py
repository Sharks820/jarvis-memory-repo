from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np

from jarvis_engine._shared import now_iso


class VoiceProfile(TypedDict):
    """Voice profile stored on disk and returned by :func:`_read_profile`."""

    user_id: str
    samples: int
    embedding: list[float]
    created_utc: str


@dataclass
class VoiceEnrollResult:
    user_id: str
    profile_path: str
    samples: int
    message: str


@dataclass
class VoiceVerifyResult:
    user_id: str
    score: float
    threshold: float
    matched: bool
    message: str


def enroll_voiceprint(
    repo_root: Path,
    *,
    user_id: str,
    wav_path: str,
    replace: bool = False,
) -> VoiceEnrollResult:
    safe_user = _normalize_user_id(user_id)
    profile_path = _profile_path(repo_root, safe_user)
    embedding = _extract_embedding(Path(wav_path))

    existing_samples = 0
    if profile_path.exists() and not replace:
        raw = _read_profile(profile_path)
        existing_samples = int(raw.get("samples", 0))
        prior = np.asarray(raw.get("embedding", []), dtype=np.float32)
        if prior.size == embedding.size and existing_samples > 0:
            embedding = ((prior * existing_samples) + embedding) / float(
                existing_samples + 1
            )
        existing_samples += 1
    else:
        existing_samples = 1

    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    payload = {
        "user_id": safe_user,
        "samples": existing_samples,
        "embedding": embedding.tolist(),
        "created_utc": now_iso(),
    }
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    return VoiceEnrollResult(
        user_id=safe_user,
        profile_path=str(profile_path),
        samples=existing_samples,
        message="Voice profile enrolled.",
    )


def verify_voiceprint(
    repo_root: Path,
    *,
    user_id: str,
    wav_path: str,
    threshold: float = 0.82,
) -> VoiceVerifyResult:
    safe_user = _normalize_user_id(user_id)
    profile_path = _profile_path(repo_root, safe_user)
    if not profile_path.exists():
        return VoiceVerifyResult(
            user_id=safe_user,
            score=0.0,
            threshold=threshold,
            matched=False,
            message=f"No enrolled profile for user_id={safe_user}.",
        )

    profile = _read_profile(profile_path)
    base = np.asarray(profile.get("embedding", []), dtype=np.float32)
    if base.size == 0:
        return VoiceVerifyResult(
            user_id=safe_user,
            score=0.0,
            threshold=threshold,
            matched=False,
            message=f"Profile for user_id={safe_user} is empty.",
        )

    candidate = _extract_embedding(Path(wav_path))
    if candidate.size != base.size:
        return VoiceVerifyResult(
            user_id=safe_user,
            score=0.0,
            threshold=threshold,
            matched=False,
            message="Voice embedding shape mismatch.",
        )

    base_norm = float(np.linalg.norm(base))
    cand_norm = float(np.linalg.norm(candidate))
    if base_norm <= 0 or cand_norm <= 0:
        score = 0.0
    else:
        score = float(np.dot(base, candidate) / (base_norm * cand_norm))
    matched = score >= threshold
    return VoiceVerifyResult(
        user_id=safe_user,
        score=round(score, 4),
        threshold=threshold,
        matched=matched,
        message="Voice match confirmed." if matched else "Voice match failed.",
    )


def _normalize_user_id(user_id: str) -> str:
    value = user_id.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if not value:
        raise ValueError("Invalid user_id.")
    return value[:64]


def _profile_path(repo_root: Path, user_id: str) -> Path:
    return repo_root / ".planning" / "security" / "voiceprints" / f"{user_id}.json"


def _read_profile(path: Path) -> VoiceProfile:
    from jarvis_engine._shared import load_json_file

    return load_json_file(path, {}, expected_type=dict)


def _extract_embedding(path: Path) -> np.ndarray:
    sample_rate, signal = _read_wav_mono(path)
    if signal.size < 800:
        raise ValueError("Voice sample too short; provide at least ~0.05s of audio.")

    target_sr = 16000
    if sample_rate != target_sr:
        signal = _resample(signal, sample_rate, target_sr)
        sample_rate = target_sr

    signal = signal.astype(np.float32)
    signal -= float(np.mean(signal))
    max_abs = float(np.max(np.abs(signal))) if signal.size else 0.0
    if max_abs > 0:
        signal = signal / max_abs

    frame_size = int(0.025 * sample_rate)
    hop = int(0.010 * sample_rate)
    if frame_size <= 0 or hop <= 0:
        raise ValueError("Invalid frame configuration.")

    window = np.hamming(frame_size).astype(np.float32)
    frames: list[np.ndarray] = []
    for start in range(0, signal.size - frame_size + 1, hop):
        frame = signal[start : start + frame_size] * window
        frames.append(frame)
    if not frames:
        raise ValueError("Voice sample too short after framing.")

    spectra = []
    zcr_values = []
    rms_values = []
    for frame in frames:
        spec = np.abs(np.fft.rfft(frame)) + 1e-8
        spectra.append(np.log(spec))
        zcr = float(np.mean(np.abs(np.diff(np.signbit(frame).astype(np.int8)))))
        rms = float(np.sqrt(np.mean(frame * frame)))
        zcr_values.append(zcr)
        rms_values.append(rms)

    matrix = np.vstack(spectra)
    bins = min(80, matrix.shape[1])
    mean_bins = np.mean(matrix[:, :bins], axis=0)
    std_bins = np.std(matrix[:, :bins], axis=0)
    global_stats = np.asarray(
        [
            float(np.mean(rms_values)),
            float(np.std(rms_values)),
            float(np.mean(zcr_values)),
            float(np.std(zcr_values)),
        ],
        dtype=np.float32,
    )

    embedding = np.concatenate(
        [mean_bins.astype(np.float32), std_bins.astype(np.float32), global_stats]
    )
    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm
    return embedding


_MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50 MB


def _read_wav_mono(path: Path) -> tuple[int, np.ndarray]:
    if not path.exists():
        raise ValueError(f"Audio file not found: {path}")
    if path.stat().st_size > _MAX_AUDIO_BYTES:
        raise ValueError("Audio file too large (max 50MB)")
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sample_rate <= 0:
        raise ValueError("Invalid WAV sample rate.")
    if channels <= 0:
        raise ValueError("Invalid WAV channels.")

    if sampwidth == 1:
        raw = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        raw = (raw - 128.0) / 128.0
    elif sampwidth == 2:
        raw = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        raw = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth}")

    if channels > 1:
        raw = raw.reshape(-1, channels)[:, 0]
    return sample_rate, raw


def _resample(signal: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return signal
    duration = signal.size / float(from_rate)
    target_size = max(1, int(round(duration * to_rate)))
    old_x = np.linspace(0.0, duration, num=signal.size, endpoint=False)
    new_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
    return np.interp(new_x, old_x, signal).astype(np.float32)
