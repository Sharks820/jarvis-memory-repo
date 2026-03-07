from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from jarvis_engine._shared import load_json_file

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    profile: str = "balanced"
    primary_runtime: str = "desktop_pc"
    secondary_runtime: str = ""
    security_strictness: str = "balanced"
    operation_mode: str = "normal"
    cloud_burst_enabled: bool = False
    access_channels: list[str] = field(default_factory=lambda: ["desktop"])
    regression_gate_enabled: bool = True
    capability_mode: str = "tiered_authorization"
    default_query_model: str = "claude-sonnet-4-5-20250929"
    last_updated: str = ""


@functools.lru_cache(maxsize=1)
def repo_root() -> Path:
    """Return the repository root directory.

    Resolution order:
    1. JARVIS_REPO_ROOT environment variable (explicit override).
    2. Walk up from this file looking for a directory that contains engine/.
    3. Fall back to Path(__file__).resolve().parents[3] (legacy default).

    Raises RuntimeError if none of the above yield a valid repo root.
    """
    # 1. Environment variable override
    env_root = os.getenv("JARVIS_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "engine").is_dir():
            return candidate
        logger.warning(
            "JARVIS_REPO_ROOT=%s does not contain engine/ directory; ignoring",
            env_root,
        )

    # 2. Walk up from this file
    current = Path(__file__).resolve().parent
    for _ in range(8):  # safety limit
        if (current / "engine").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break  # hit filesystem root
        current = parent

    # 3. Legacy fallback
    fallback = Path(__file__).resolve().parents[3]
    if (fallback / "engine").is_dir():
        return fallback

    raise RuntimeError(
        "Cannot determine repo root: no ancestor of "
        f"{Path(__file__).resolve()} contains an engine/ directory. "
        "Set JARVIS_REPO_ROOT environment variable to the repo root."
    )


def load_config() -> EngineConfig:
    """Load engine configuration from .planning/config.json.

    Falls back to defaults on missing file, JSON errors, or OS-level read errors.
    The JARVIS_ENGINE_PROFILE environment variable, if set, overrides the
    profile field from the config file.
    """
    config_path = repo_root() / ".planning" / "config.json"
    data: dict[str, Any] = load_json_file(config_path, None, expected_type=dict)
    if data is None:
        cfg = EngineConfig()
        env_profile = os.getenv("JARVIS_ENGINE_PROFILE", "").strip()
        if env_profile:
            cfg.profile = env_profile
        return cfg

    # Keep startup resilient if new keys are added to config.json later.
    allowed = {f.name for f in fields(EngineConfig)}
    filtered = {k: v for k, v in data.items() if k in allowed}
    cfg = EngineConfig(**filtered)

    # Allow environment variable override for the profile
    env_profile = os.getenv("JARVIS_ENGINE_PROFILE", "").strip()
    if env_profile:
        cfg.profile = env_profile
    return cfg
