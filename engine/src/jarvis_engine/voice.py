from __future__ import annotations

import base64
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass
class VoiceSpeakResult:
    voice_name: str
    output_wav: str
    message: str


from jarvis_engine._shared import win_hidden_subprocess_kwargs as _win_hidden_subprocess_kwargs


def _run_ps(script: str, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
        **_win_hidden_subprocess_kwargs(),
    )


def _run_ps_encoded(
    script: str,
    env: dict[str, str] | None = None,
    timeout_s: int = 60,
) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=timeout_s,
        **_win_hidden_subprocess_kwargs(),
    )


@lru_cache(maxsize=1)
def _list_windows_voices_cached() -> tuple[str, ...]:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    proc = _run_ps(script, timeout_s=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to list voices.")
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def list_windows_voices(refresh: bool = False) -> list[str]:
    if refresh:
        _list_windows_voices_cached.cache_clear()
    return list(_list_windows_voices_cached())


def _preferred_voice_patterns(profile: str) -> list[str]:
    if profile == "jarvis_like":
        return [
            "en-GB-ThomasNeural",
            "en-GB-RyanNeural",
            "en-GB-EthanNeural",
            "en-GB",
            "British",
            "Thomas",
            "Ryan",
            "Ethan",
            "Hazel",
            "George",
            "David",
            "James",
            "Male",
        ]
    return ["en-US", "Male"]


def _edge_tts_executable() -> str:
    local = Path(sys.executable).with_name("edge-tts.exe")
    if local.exists():
        return str(local)
    found = shutil.which("edge-tts")
    return found or ""


@lru_cache(maxsize=1)
def _list_edge_voices_cached() -> tuple[str, ...]:
    exe = _edge_tts_executable()
    if not exe:
        return ()
    proc = subprocess.run(
        [exe, "--list-voices"],
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
        **_win_hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        return ()
    voices: list[str] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^\s*([a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+)", line)
        if match:
            voices.append(match.group(1))
    return tuple(voices)


def list_edge_voices(refresh: bool = False) -> list[str]:
    if refresh:
        _list_edge_voices_cached.cache_clear()
    return list(_list_edge_voices_cached())


def _choose_edge_voice(*, profile: str, custom_pattern: str = "") -> str:
    voices = list_edge_voices(refresh=False)
    if not voices:
        voices = list_edge_voices(refresh=True)
    if not voices:
        return ""
    return choose_voice(voices, profile=profile, custom_pattern=custom_pattern)


def _play_audio_file(path: str) -> None:
    env = os.environ.copy()
    env["JARVIS_VOICE_MEDIA"] = str(path)
    script = (
        "$p=$env:JARVIS_VOICE_MEDIA; "
        "if (-not (Test-Path $p)) { exit 0 }; "
        "$ext=[System.IO.Path]::GetExtension($p).ToLowerInvariant(); "
        "if ($ext -eq '.wav') { "
        "  Add-Type -AssemblyName System; "
        "  $sp=New-Object System.Media.SoundPlayer $p; "
        "  $sp.PlaySync(); "
        "} else { "
        "  Add-Type -AssemblyName presentationCore; "
        "  $player=New-Object System.Windows.Media.MediaPlayer; "
        "  $player.Open([Uri]$p); "
        "  $player.Play(); "
        "  Start-Sleep -Milliseconds 150; "
        "  while (-not $player.NaturalDuration.HasTimeSpan) { Start-Sleep -Milliseconds 120 }; "
        "  Start-Sleep -Milliseconds ([int]$player.NaturalDuration.TimeSpan.TotalMilliseconds + 150); "
        "  $player.Close(); "
        "}"
    )
    _run_ps_encoded(script, env=env, timeout_s=180)


def _speak_text_edge(
    text: str,
    *,
    profile: str,
    custom_voice_pattern: str,
    output_wav: str,
    rate: int,
) -> VoiceSpeakResult:
    exe = _edge_tts_executable()
    if not exe:
        raise RuntimeError("edge-tts executable not found.")

    voice = _choose_edge_voice(profile=profile, custom_pattern=custom_voice_pattern)
    if not voice:
        raise RuntimeError("No edge-tts voices found.")

    if output_wav:
        out_path = str(Path(output_wav).resolve())
    else:
        fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_voice_")
        os.close(fd)
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Unable to prepare voice output path: {exc}") from exc

    rate_pct = int(max(-50, min(50, rate * 5)))
    cmd = [
        exe,
        "--text",
        text,
        "--voice",
        voice,
        f"--rate={rate_pct:+d}%",
        "--write-media",
        out_path,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        **_win_hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "edge-tts synthesis failed.")

    if not output_wav:
        _play_audio_file(out_path)

    return VoiceSpeakResult(
        voice_name=voice,
        output_wav=out_path,
        message="Edge neural voice output completed.",
    )


def _chunk_text_for_streaming(text: str, *, sentences_per_chunk: int = 3) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", stripped) if part.strip()]
    if len(sentences) <= sentences_per_chunk:
        return [stripped]
    chunks: list[str] = []
    for idx in range(0, len(sentences), sentences_per_chunk):
        chunks.append(" ".join(sentences[idx : idx + sentences_per_chunk]).strip())
    return [chunk for chunk in chunks if chunk]


def _speak_text_edge_streamed(
    text: str,
    *,
    profile: str,
    custom_voice_pattern: str,
    rate: int,
) -> VoiceSpeakResult:
    exe = _edge_tts_executable()
    if not exe:
        raise RuntimeError("edge-tts executable not found.")

    voice = _choose_edge_voice(profile=profile, custom_pattern=custom_voice_pattern)
    if not voice:
        raise RuntimeError("No edge-tts voices found.")

    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    if len(chunks) <= 1:
        return _speak_text_edge(
            text,
            profile=profile,
            custom_voice_pattern=custom_voice_pattern,
            output_wav="",
            rate=rate,
        )

    rate_pct = int(max(-50, min(50, rate * 5)))
    out_dir = Path(tempfile.mkdtemp(prefix="jarvis_edge_stream_"))
    # mkdtemp creates the directory with restricted permissions
    q: "queue.Queue[str | None]" = queue.Queue(maxsize=6)
    err: list[Exception] = []

    def producer() -> None:
        try:
            for idx, chunk in enumerate(chunks):
                media_path = out_dir / f"chunk_{idx:03}.mp3"
                cmd = [
                    exe,
                    "--text",
                    chunk,
                    "--voice",
                    voice,
                    f"--rate={rate_pct:+d}%",
                    "--write-media",
                    str(media_path),
                ]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                    **_win_hidden_subprocess_kwargs(),
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or "edge-tts synthesis failed.")
                q.put(str(media_path))
        except Exception as exc:  # noqa: BLE001
            err.append(exc)
        finally:
            q.put(None)

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    while True:
        item = q.get()
        if item is None:
            break
        _play_audio_file(item)
    # Clean up streamed chunk files
    try:
        for f in out_dir.glob("chunk_*.mp3"):
            f.unlink(missing_ok=True)
    except OSError:
        pass
    if err:
        raise RuntimeError(str(err[0]))

    return VoiceSpeakResult(
        voice_name=voice,
        output_wav="",
        message=f"Edge neural streaming voice output completed (chunks={len(chunks)}).",
    )


def choose_voice(voices: list[str], profile: str, custom_pattern: str = "") -> str:
    patterns = []
    if custom_pattern.strip():
        patterns.append(custom_pattern.strip())
    patterns.extend(_preferred_voice_patterns(profile))

    lowered = [(v, v.lower()) for v in voices]
    for pattern in patterns:
        p = pattern.lower()
        for raw, low in lowered:
            if p in low:
                return raw
    return voices[0] if voices else ""


def speak_text(
    text: str,
    *,
    profile: str = "jarvis_like",
    custom_voice_pattern: str = "",
    output_wav: str = "",
    rate: int = 0,
) -> VoiceSpeakResult:
    env_pattern = os.getenv("JARVIS_VOICE_PATTERN", "").strip()
    effective_pattern = custom_voice_pattern.strip() or env_pattern
    engine_pref = os.getenv("JARVIS_TTS_ENGINE", "auto").strip().lower()
    if engine_pref in {"edge", "edge_tts", "auto"}:
        try:
            if (not output_wav) and len(text.strip()) > 180:
                return _speak_text_edge_streamed(
                    text,
                    profile=profile,
                    custom_voice_pattern=effective_pattern,
                    rate=rate,
                )
            return _speak_text_edge(
                text,
                profile=profile,
                custom_voice_pattern=effective_pattern,
                output_wav=output_wav,
                rate=rate,
            )
        except Exception:
            if engine_pref in {"edge", "edge_tts"}:
                raise

    voices = list_windows_voices(refresh=False)
    if not voices:
        voices = list_windows_voices(refresh=True)
    voice = choose_voice(voices, profile=profile, custom_pattern=effective_pattern)
    if not voice:
        raise RuntimeError("No Windows voices found.")

    rate = max(-10, min(10, rate))
    out_path = str(Path(output_wav).resolve()) if output_wav else ""
    env = os.environ.copy()
    env["JARVIS_VOICE_TEXT"] = text
    env["JARVIS_VOICE_NAME"] = voice
    env["JARVIS_VOICE_RATE"] = str(rate)
    env["JARVIS_VOICE_OUTPUT"] = out_path

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice=$env:JARVIS_VOICE_NAME; "
        "$text=$env:JARVIS_VOICE_TEXT; "
        "$rate=[int]$env:JARVIS_VOICE_RATE; "
        "$out=$env:JARVIS_VOICE_OUTPUT; "
        "$s.Rate=$rate; "
        "$s.SelectVoice($voice); "
        "if ($out) { $s.SetOutputToWaveFile($out); } "
        "$s.Speak($text); "
        "$s.Dispose()"
    )

    proc = _run_ps_encoded(script, env=env, timeout_s=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Voice synthesis failed.")

    return VoiceSpeakResult(
        voice_name=voice,
        output_wav=out_path,
        message="Voice output completed.",
    )
