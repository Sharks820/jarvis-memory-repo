from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jarvis_engine.persona import (
    PersonaConfig,
    _resolve_tone,
    compose_persona_reply,
    compose_persona_system_prompt,
    load_persona_config,
    save_persona_config,
)
from jarvis_engine.commands.voice_commands import (
    PersonaComposeCommand,
    PersonaComposeResult,
)
from jarvis_engine.handlers.voice_handlers import PersonaComposeHandler


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------


def test_persona_config_roundtrip(tmp_path: Path) -> None:
    cfg = save_persona_config(
        tmp_path,
        enabled=True,
        humor_level=3,
        mode="jarvis_british",
        style="secret_agent",
    )
    assert cfg.enabled is True
    assert cfg.humor_level == 3
    loaded = load_persona_config(tmp_path)
    assert loaded.mode == "jarvis_british"


def test_persona_reply_shapes() -> None:
    class Cfg:
        enabled = True
        humor_level = 2

    ok_line = compose_persona_reply(Cfg(), intent="runtime_status", success=True)
    fail_line = compose_persona_reply(
        Cfg(), intent="runtime_pause", success=False, reason="voice auth required"
    )
    assert any(
        k in ok_line.lower() for k in ("runtime status", "done", "complete", "handled")
    )
    assert any(
        k in fail_line.lower() for k in ("blocked", "couldn't proceed", "not permitted")
    )


# ---------------------------------------------------------------------------
# New tests: _resolve_tone
# ---------------------------------------------------------------------------


def test_resolve_tone_health_returns_professional() -> None:
    assert _resolve_tone("health") == "professional"


def test_resolve_tone_finance_returns_professional() -> None:
    assert _resolve_tone("finance") == "professional"


def test_resolve_tone_security_returns_professional() -> None:
    assert _resolve_tone("security") == "professional"


def test_resolve_tone_gaming_returns_light_humor() -> None:
    assert _resolve_tone("gaming") == "light_humor"


def test_resolve_tone_learning_returns_light_humor() -> None:
    assert _resolve_tone("learning") == "light_humor"


def test_resolve_tone_family_returns_warm() -> None:
    assert _resolve_tone("family") == "warm"


def test_resolve_tone_communications_returns_warm() -> None:
    assert _resolve_tone("communications") == "warm"


def test_resolve_tone_ops_returns_balanced() -> None:
    assert _resolve_tone("ops") == "balanced"


def test_resolve_tone_coding_returns_balanced() -> None:
    assert _resolve_tone("coding") == "balanced"


def test_resolve_tone_general_returns_balanced() -> None:
    assert _resolve_tone("general") == "balanced"


def test_resolve_tone_unknown_returns_balanced() -> None:
    assert _resolve_tone("totally_unknown_branch") == "balanced"


# ---------------------------------------------------------------------------
# New tests: compose_persona_system_prompt
# ---------------------------------------------------------------------------


def _make_cfg(enabled: bool = True, humor_level: int = 2) -> PersonaConfig:
    return PersonaConfig(
        mode="jarvis_british",
        enabled=enabled,
        humor_level=humor_level,
        style="historically_witty_secret_agent",
        updated_utc="",
    )


def test_compose_system_prompt_contains_base() -> None:
    prompt = compose_persona_system_prompt(_make_cfg(), branch="general")
    assert "Jarvis" in prompt
    assert "butler" in prompt


def test_compose_system_prompt_professional_no_humor() -> None:
    prompt = compose_persona_system_prompt(_make_cfg(humor_level=0), branch="health")
    assert "composed, precise" in prompt.lower() or "precise" in prompt.lower()
    assert "humour" in prompt.lower() or "factual" in prompt.lower()
    # Should NOT have wit/wordplay encouragement
    assert "wordplay" not in prompt.lower()


def test_compose_system_prompt_disabled_returns_empty() -> None:
    prompt = compose_persona_system_prompt(_make_cfg(enabled=False), branch="gaming")
    assert prompt == ""


def test_compose_system_prompt_humor_level_3() -> None:
    prompt = compose_persona_system_prompt(_make_cfg(humor_level=3), branch="gaming")
    assert "wit" in prompt.lower() or "wordplay" in prompt.lower()


def test_compose_system_prompt_humor_level_1() -> None:
    prompt = compose_persona_system_prompt(_make_cfg(humor_level=1), branch="ops")
    assert "driest" in prompt.lower()


def test_compose_system_prompt_humor_level_2_no_extra() -> None:
    """Humor level 2 is the default and should add no extra humor note."""
    prompt = compose_persona_system_prompt(_make_cfg(humor_level=2), branch="ops")
    assert "suppress" not in prompt.lower()
    assert "driest" not in prompt.lower()
    assert "wordplay" not in prompt.lower()


def test_compose_system_prompt_distinct_per_domain() -> None:
    """Different domains should produce different system prompts."""
    prompt_health = compose_persona_system_prompt(_make_cfg(), branch="health")
    prompt_gaming = compose_persona_system_prompt(_make_cfg(), branch="gaming")
    prompt_family = compose_persona_system_prompt(_make_cfg(), branch="family")
    # All share base but have different tone instructions
    assert prompt_health != prompt_gaming
    assert prompt_health != prompt_family
    assert prompt_gaming != prompt_family


# ---------------------------------------------------------------------------
# New tests: PersonaComposeHandler
# ---------------------------------------------------------------------------


def test_persona_compose_handler_success(tmp_path: Path) -> None:
    mock_gateway = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Very good, sir. Here is your answer."
    mock_gateway.complete.return_value = mock_resp

    handler = PersonaComposeHandler(tmp_path, gateway=mock_gateway)
    cmd = PersonaComposeCommand(query="What is the weather?", branch="ops")
    result = handler.handle(cmd)

    assert isinstance(result, PersonaComposeResult)
    assert result.text == "Very good, sir. Here is your answer."
    assert result.branch == "ops"
    assert result.tone == "balanced"
    assert result.message == ""

    # Verify gateway was called with messages including system prompt
    call_args = mock_gateway.complete.call_args
    messages = (
        call_args.kwargs.get("messages")
        or call_args[1].get("messages")
        or call_args[0][0]
    )
    assert any(m["role"] == "system" for m in messages)
    assert any(
        m["role"] == "user" and "weather" in m["content"].lower() for m in messages
    )


def test_persona_compose_handler_no_gateway(tmp_path: Path) -> None:
    handler = PersonaComposeHandler(tmp_path, gateway=None)
    cmd = PersonaComposeCommand(query="Hello")
    result = handler.handle(cmd)

    assert isinstance(result, PersonaComposeResult)
    assert result.text == ""
    assert "error" in result.message.lower()


def test_compose_persona_reply_unchanged() -> None:
    """Regression: compose_persona_reply produces varied, relevant responses."""
    cfg = _make_cfg(enabled=True, humor_level=2)
    ok = compose_persona_reply(cfg, intent="test_op", success=True)
    fail = compose_persona_reply(cfg, intent="test_op", success=False, reason="auth")
    assert "test op" in ok.lower() or "done" in ok.lower() or "complete" in ok.lower()
    assert any(
        k in fail.lower() for k in ("blocked", "not permitted", "couldn't proceed")
    )


def test_compose_persona_reply_disabled() -> None:
    """Regression: disabled persona gives plain messages."""
    cfg = _make_cfg(enabled=False)
    ok = compose_persona_reply(cfg, intent="deploy", success=True)
    assert ok == "Command deploy completed."
