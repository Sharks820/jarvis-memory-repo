"""Backward-compatibility shim — canonical location is jarvis_engine.memory.conversation_state."""
from jarvis_engine.memory.conversation_state import *  # noqa: F401,F403
from jarvis_engine.memory.conversation_state import (  # noqa: F401 — re-export private names
    _DEFAULT_STATE_DIR,
    _ENCRYPTED_HEADER,
    _ENCRYPTION_ENV_KEY,
    _KDF_ITERATIONS,
    _MAX_ENTITIES_PER_TURN,
    _MAX_ENTITY_LENGTH,
    _MAX_ROLLING_SUMMARY_CHARS,
    _SALT_FILENAME,
    _STATE_FILENAME,
    _TIMELINE_DB_FILENAME,
    _TIMELINE_MAX_AGE_DAYS,
    _TIMELINE_PRUNE_INTERVAL,
    _TIMELINE_VACUUM_THRESHOLD,
    _redact_snippet,
    _state_holder,
)
