from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

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
    last_updated: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config() -> EngineConfig:
    config_path = repo_root() / ".planning" / "config.json"
    try:
        data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load config from %s: %s; using defaults", config_path, exc)
        return EngineConfig()

    # Keep startup resilient if new keys are added to config.json later.
    allowed = {f.name for f in fields(EngineConfig)}
    filtered = {k: v for k, v in data.items() if k in allowed}
    return EngineConfig(**filtered)
