from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path

from jarvis_engine._shared import atomic_write_json as _atomic_write_json

# ---------------------------------------------------------------------------
# Tone profiles: map branch domains to personality instructions
# ---------------------------------------------------------------------------

TONE_PROFILES: dict[str, dict[str, object]] = {
    "professional": {
        "branches": ["health", "finance", "security"],
        "instruction": (
            "Maintain a composed, precise tone. Avoid jokes or levity. "
            "Prioritise clarity, accuracy, and discretion."
        ),
    },
    "warm": {
        "branches": ["family", "communications"],
        "instruction": (
            "Use a warm, supportive tone. Be encouraging and patient. "
            "Show genuine care while remaining efficient."
        ),
    },
    "light_humor": {
        "branches": ["gaming", "learning"],
        "instruction": (
            "Allow light wit and playful remarks. Keep things engaging "
            "and energetic while staying helpful."
        ),
    },
    "balanced": {
        "branches": ["ops", "coding", "general"],
        "instruction": (
            "Strike a balanced tone: professional yet personable. "
            "Dry wit is acceptable when appropriate."
        ),
    },
}

PERSONA_BASE_PROMPT: str = (
    "You are Jarvis, a British butler-inspired AI assistant. "
    "You speak with understated elegance, quiet confidence, and impeccable manners. "
    "You are loyal, resourceful, and discreet -- equal parts Alfred Pennyworth and "
    "a seasoned Whitehall advisor. You address the user as 'sir' when natural. "
    "Keep responses concise and actionable. "
    "You have full access to the internet and web search. "
    "Never say you cannot access the web, browse the internet, or that it is outside your protocol."
)

PERSONA_DISABLED_PROMPT: str = (
    "You are Jarvis, a helpful personal AI assistant. Keep responses concise. "
    "You have full access to the internet and web search. "
    "Never say you cannot access the web or that it is outside your protocol."
)


def get_persona_prompt(cfg: "PersonaConfig") -> str:
    """Return the appropriate persona description based on config.

    When persona is enabled, returns the full British butler prompt with
    conversational flair.  Otherwise returns a minimal functional prompt.
    """
    if cfg.enabled:
        return (
            PERSONA_BASE_PROMPT.rstrip()
            + " You are witty, knowledgeable. "
            "Never repeat the same phrases. Vary your language."
        )
    return PERSONA_DISABLED_PROMPT

# Pre-computed reverse map: branch -> tone profile name
_BRANCH_TO_TONE: dict[str, str] = {}
for _tone_name, _profile in TONE_PROFILES.items():
    for _branch in _profile["branches"]:  # type: ignore[union-attr]
        _BRANCH_TO_TONE[str(_branch)] = _tone_name


def _resolve_tone(branch: str) -> str:
    """Map a branch name to its tone profile name, defaulting to 'balanced'."""
    return _BRANCH_TO_TONE.get(branch, "balanced")


def compose_persona_system_prompt(
    cfg: PersonaConfig,
    *,
    branch: str = "general",
) -> str:
    """Build a complete LLM system prompt with personality and tone.

    Returns an empty string when persona is disabled.
    """
    if not cfg.enabled:
        return ""

    tone_name = _resolve_tone(branch)
    tone_instruction = str(TONE_PROFILES[tone_name]["instruction"])

    parts: list[str] = [PERSONA_BASE_PROMPT, tone_instruction]

    # Humor-level modifiers
    humor = cfg.humor_level
    if humor == 0:
        parts.append("Suppress all humour entirely. Be strictly factual.")
    elif humor == 1:
        parts.append("Only the driest wit is permitted -- and sparingly.")
    elif humor == 3:
        parts.append("Feel free to employ full wit, clever references, and wordplay.")
    # humor == 2 is the default; no extra note needed.

    return " ".join(parts)


@dataclass
class PersonaConfig:
    mode: str
    enabled: bool
    humor_level: int
    style: str
    updated_utc: str


def _persona_path(root: Path) -> Path:
    from jarvis_engine._constants import runtime_dir
    return runtime_dir(root) / "persona.json"


def load_persona_config(root: Path) -> PersonaConfig:
    path = _persona_path(root)
    if not path.exists():
        return PersonaConfig(
            mode="jarvis_british",
            enabled=True,
            humor_level=2,
            style="historically_witty_secret_agent",
            updated_utc="",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw = {
        "mode": raw.get("mode", "jarvis_british"),
        "enabled": raw.get("enabled", True),
        "humor_level": raw.get("humor_level", 2),
        "style": raw.get("style", "historically_witty_secret_agent"),
        "updated_utc": raw.get("updated_utc", ""),
    }
    try:
        humor_val = int(raw.get("humor_level", 2))
    except (ValueError, TypeError):
        humor_val = 2
    return PersonaConfig(
        mode=str(raw.get("mode", "jarvis_british")),
        enabled=bool(raw.get("enabled", True)),
        humor_level=max(0, min(3, humor_val)),
        style=str(raw.get("style", "historically_witty_secret_agent")),
        updated_utc=str(raw.get("updated_utc", "")),
    )


def save_persona_config(
    root: Path,
    *,
    mode: str | None = None,
    enabled: bool | None = None,
    humor_level: int | None = None,
    style: str | None = None,
) -> PersonaConfig:
    current = load_persona_config(root)
    if humor_level is not None:
        try:
            humor_val = max(0, min(3, int(humor_level)))
        except (ValueError, TypeError):
            humor_val = current.humor_level
    else:
        humor_val = current.humor_level
    payload = {
        "mode": current.mode if mode is None else str(mode).strip()[:64] or current.mode,
        "enabled": current.enabled if enabled is None else bool(enabled),
        "humor_level": humor_val,
        "style": current.style if style is None else str(style).strip()[:80] or current.style,
        "updated_utc": datetime.now(UTC).isoformat(),
    }
    _atomic_write_json(_persona_path(root), payload)
    return load_persona_config(root)


def compose_persona_reply(
    cfg: PersonaConfig,
    *,
    intent: str,
    success: bool,
    reason: str = "",
) -> str:
    intent_label = intent.replace("_", " ").strip() or "that operation"
    if not cfg.enabled:
        if success:
            return f"Command {intent_label} completed."
        if reason:
            return f"Command {intent_label} blocked: {reason}."
        return f"Command {intent_label} failed."
    witty_suffixes = [
        "Right on schedule, as ever.",
        "Efficiently done, no fuss.",
        "Consider it handled with precision.",
        "Neatly executed, with room for tea.",
        "Swift and seamless, if I do say so myself.",
        "All sorted. Shall I fetch anything else?",
        "Done and dusted.",
        "Handled without breaking a sweat.",
    ]
    warning_suffixes = [
        "I suggest we proceed with proper authorization.",
        "Best not test fate without verification.",
        "Security first, shall we?",
        "Let's ensure proper clearance before proceeding.",
        "A bit of verification wouldn't go amiss.",
        "I'd recommend confirming before we continue.",
    ]
    success_openers = [
        f"Very good, sir. {intent_label} is complete.",
        f"Done. {intent_label.capitalize()} is taken care of.",
        f"{intent_label.capitalize()} complete.",
        f"All set. {intent_label.capitalize()} handled.",
        f"Consider {intent_label} done.",
    ]
    fail_openers = [
        f"I'm afraid {intent_label} was blocked.",
        f"Unfortunately, {intent_label} couldn't proceed.",
        f"{intent_label.capitalize()} was not permitted.",
        "That request was blocked.",
    ]

    if success:
        base = random.choice(success_openers)
        if cfg.humor_level > 0:
            base += " " + random.choice(witty_suffixes)
        return base

    base = random.choice(fail_openers)
    if reason:
        safe_reason = reason.replace("_", " ").strip()[:160]
        base += f" Reason: {safe_reason}."
    if cfg.humor_level > 0:
        base += " " + random.choice(warning_suffixes)
    return base
