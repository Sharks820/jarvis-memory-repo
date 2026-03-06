"""Command dataclasses for voice operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_engine._constants import ACTIONS_FILENAME, OPS_SNAPSHOT_FILENAME


@dataclass(frozen=True)
class VoiceListCommand:
    pass


@dataclass
class VoiceListResult:
    windows_voices: list[str] = field(default_factory=list)
    edge_voices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceSayCommand:
    text: str
    profile: str = "jarvis_like"
    voice_pattern: str = ""
    output_wav: str = ""
    rate: int = -1


@dataclass
class VoiceSayResult:
    voice_name: str = ""
    output_wav: str = ""
    message: str = ""


@dataclass(frozen=True)
class VoiceEnrollCommand:
    user_id: str
    wav_path: str
    replace: bool = False


@dataclass
class VoiceEnrollResult:
    user_id: str = ""
    profile_path: str = ""
    samples: int = 0
    message: str = ""


@dataclass(frozen=True)
class VoiceVerifyCommand:
    user_id: str
    wav_path: str
    threshold: float = 0.82


@dataclass
class VoiceVerifyResult:
    user_id: str = ""
    score: float = 0.0
    threshold: float = 0.82
    matched: bool = False
    message: str = ""


@dataclass(frozen=True)
class VoiceListenCommand:
    max_duration_seconds: float = 30.0
    language: str = "en"


@dataclass
class VoiceListenResult:
    text: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    message: str = ""


@dataclass(frozen=True)
class VoiceRunCommand:
    text: str
    execute: bool = False
    approve_privileged: bool = False
    speak: bool = False
    snapshot_path: Path = Path(OPS_SNAPSHOT_FILENAME)
    actions_path: Path = Path(ACTIONS_FILENAME)
    voice_user: str = "conner"
    voice_auth_wav: str = ""
    voice_threshold: float = 0.82
    master_password: str = ""
    model_override: str = ""
    skip_voice_auth_guard: bool = False


@dataclass
class VoiceRunResult:
    return_code: int = 0
    intent: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PersonaComposeCommand:
    """Request a personality-aware LLM response for a user query."""

    query: str
    branch: str = "general"
    model: str = ""


@dataclass
class PersonaComposeResult:
    text: str = ""
    branch: str = ""
    tone: str = ""
    message: str = ""
