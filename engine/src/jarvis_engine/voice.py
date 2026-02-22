from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class VoiceSpeakResult:
    voice_name: str
    output_wav: str
    message: str


def _run_ps(script: str, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
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
            "en-GB",
            "British",
            "George",
            "David",
            "James",
            "Male",
        ]
    return ["en-US", "Male"]


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
    voices = list_windows_voices(refresh=False)
    if not voices:
        voices = list_windows_voices(refresh=True)
    voice = choose_voice(voices, profile=profile, custom_pattern=custom_voice_pattern)
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
