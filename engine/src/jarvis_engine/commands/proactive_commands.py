"""Command dataclasses for proactive intelligence, wake word, cost reduction, and self-testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProactiveCheckCommand:
    """Manually trigger proactive evaluation against snapshot data."""

    snapshot_path: str = ""


@dataclass
class ProactiveCheckResult:
    alerts_fired: int = 0
    alerts: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    diagnostics: str = ""


@dataclass(frozen=True)
class WakeWordStartCommand:
    """Start wake word detection."""

    threshold: float = 0.5


@dataclass
class WakeWordStartResult:
    started: bool = False
    message: str = ""


@dataclass(frozen=True)
class CostReductionCommand:
    """Show local vs cloud query ratio and cost reduction trend."""

    days: int = 30


@dataclass
class CostReductionResult:
    local_pct: float = 0.0
    cloud_cost_usd: float = 0.0
    failed_count: int = 0
    failed_cost_usd: float = 0.0
    trend: str = ""
    message: str = ""


@dataclass(frozen=True)
class SelfTestCommand:
    """Run adversarial memory quiz to test retained knowledge."""

    score_threshold: float = 0.5


@dataclass
class SelfTestResult:
    average_score: float = 0.0
    tasks_run: int = 0
    regression_detected: bool = False
    message: str = ""
    per_task_scores: list = field(default_factory=list)
