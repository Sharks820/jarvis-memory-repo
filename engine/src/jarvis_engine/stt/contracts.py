"""Shared STT result contracts."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class TranscriptionSegment(TypedDict):
    """Timed transcript span returned by an STT backend."""

    start: float
    end: float
    text: str
    kind: NotRequired[str]


class VoiceUtterance(TypedDict):
    """Structured STT metadata carried alongside command text."""

    raw_text: str
    command_text: str
    language: str
    confidence: float
    backend: str
    segments: NotRequired[list[TranscriptionSegment]]
