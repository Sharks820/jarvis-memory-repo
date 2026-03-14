"""Shared temporal-grounding helper for LLM system prompts."""

from __future__ import annotations

from datetime import datetime

from jarvis_engine._compat import UTC


def get_datetime_prompt() -> str:
    """Return a deterministic date/time context line for model grounding.

    This is the single source of truth for injecting temporal awareness
    into any LLM call.  Import and prepend/append this to system prompts
    wherever the model needs to know "what time is it now."
    """
    local_now = datetime.now().astimezone()
    utc_now = local_now.astimezone(UTC)
    local_iso = local_now.isoformat(timespec="seconds")
    utc_iso = utc_now.isoformat(timespec="seconds")
    unix_epoch = int(utc_now.timestamp())
    human_now = local_now.strftime("%A, %B %d, %Y %H:%M %Z")
    return (
        f"Current date/time: {human_now} (local ISO {local_iso}; UTC {utc_iso}; epoch {unix_epoch}). "
        "Treat this as the present unless the user explicitly specifies another date. "
        "If relative-time reasoning conflicts with this clock context, prioritize this clock context."
    )
