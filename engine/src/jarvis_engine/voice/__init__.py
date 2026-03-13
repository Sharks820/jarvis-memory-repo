"""Voice subpackage — re-exports for backward compatibility.

Modules
-------
core        – TTS synthesis (edge-tts / Windows SAPI)
auth        – Voiceprint enroll / verify
context     – Smart context building for voice pipeline
extractors  – Text cleaning, wake-word stripping, URL shortening
intents     – Voice intent dispatch + CLI entry-point
pipeline    – Full voice-pipeline orchestration
telemetry   – Voice UX telemetry & SLO tracking
wakeword    – Wake-word detection
"""

# Lazy backward-compat shims so that old import paths keep working:
#   from jarvis_engine.voice import speak_text          (was voice.py)
#   from jarvis_engine.voice import VoiceSpeakResult    (was voice.py)

from jarvis_engine.voice.core import (  # noqa: F401
    VoiceSpeakResult,
    list_edge_voices,
    list_windows_voices,
    speak_text,
    choose_voice,
)
