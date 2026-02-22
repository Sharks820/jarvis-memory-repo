from __future__ import annotations

from jarvis_engine.voice import choose_voice


def test_choose_voice_prefers_jarvis_like_patterns() -> None:
    voices = [
        "Microsoft Zira Desktop",
        "Microsoft David Desktop",
        "Microsoft Hazel Desktop - English (Great Britain)",
    ]
    selected = choose_voice(voices, profile="jarvis_like")
    assert selected in voices
    assert "David" in selected or "Great Britain" in selected or "English" in selected

