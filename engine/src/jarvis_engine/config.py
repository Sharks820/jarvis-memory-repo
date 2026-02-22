from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class EngineConfig:
    profile: str
    primary_runtime: str
    secondary_runtime: str
    security_strictness: str
    operation_mode: str
    cloud_burst_enabled: bool
    access_channels: list[str]
    regression_gate_enabled: bool
    capability_mode: str
    last_updated: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config() -> EngineConfig:
    config_path = repo_root() / ".planning" / "config.json"
    data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))

    # Keep startup resilient if new keys are added to config.json later.
    allowed = {f.name for f in fields(EngineConfig)}
    filtered = {k: v for k, v in data.items() if k in allowed}
    return EngineConfig(**filtered)
