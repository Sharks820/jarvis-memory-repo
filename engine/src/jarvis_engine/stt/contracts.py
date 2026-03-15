"""Shared STT result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NotRequired, TypedDict


class TranscriptionSegment(TypedDict):
    """Timed transcript span returned by an STT backend."""

    start: float
    end: float
    text: str
    kind: NotRequired[str]


@dataclass
class TranscriptionResult:
    """Result of a speech-to-text transcription."""

    text: str = ""
    raw_text: str = ""  # Pre-postprocessing transcription (before NER, spelling, etc.)
    language: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    backend: str = ""
    retried: bool = False
    segments: list[TranscriptionSegment] | None = None
    needs_confirmation: bool = False
    pipeline_latency_ms: float = 0.0


class VoiceUtterance(TypedDict):
    """Structured STT metadata carried alongside command text."""

    raw_text: str
    command_text: str
    language: str
    confidence: float
    backend: str
    segments: NotRequired[list[TranscriptionSegment]]
