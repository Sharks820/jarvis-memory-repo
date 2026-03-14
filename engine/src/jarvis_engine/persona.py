"""Backward-compat shim -- moved to jarvis_engine.memory.persona."""
from jarvis_engine.memory.persona import (  # noqa: F401
    PERSONA_BASE_PROMPT,
    PERSONA_DISABLED_PROMPT,
    TONE_PROFILES,
    PersonaConfig,
    ToneProfile,
    _BRANCH_TO_TONE,
    _resolve_tone,
    compose_persona_reply,
    compose_persona_system_prompt,
    get_persona_prompt,
    load_persona_config,
    save_persona_config,
)
