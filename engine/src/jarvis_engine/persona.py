from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    "Keep responses concise and actionable."
)

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
    return root / ".planning" / "runtime" / "persona.json"


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
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
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
    path = _persona_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return load_persona_config(root)


def compose_persona_reply(
    cfg: PersonaConfig,
    *,
    intent: str,
    success: bool,
    reason: str = "",
) -> str:
    style_value = str(getattr(cfg, "style", "historically_witty_secret_agent"))
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
    ]
    warning_suffixes = [
        "I suggest we proceed with proper authorization.",
        "Best not test fate without verification.",
        "Even brilliance prefers guardrails.",
        "Security first, heroics second.",
    ]
    historical_quips = [
        "As Churchill might note, this is not the end, merely excellent progress.",
        "By Bletchley standards, that was delightfully efficient.",
        "Even Holmes would call that deduction elementary.",
        "Consider it dispatched with Bond-like discretion.",
    ]

    if success:
        base = f"Very good, sir. {intent_label} is complete."
        if cfg.humor_level > 0:
            base += " " + witty_suffixes[min(cfg.humor_level, len(witty_suffixes) - 1)]
            if "histor" in style_value.lower():
                base += " " + historical_quips[min(cfg.humor_level, len(historical_quips) - 1)]
        return base

    base = f"I am afraid {intent_label} was blocked."
    if reason:
        safe_reason = reason.replace("_", " ").strip()[:160]
        base += f" Reason: {safe_reason}."
    if cfg.humor_level > 0:
        base += " " + warning_suffixes[min(cfg.humor_level, len(warning_suffixes) - 1)]
        if "histor" in style_value.lower():
            base += " " + historical_quips[min(cfg.humor_level, len(historical_quips) - 1)]
    return base
