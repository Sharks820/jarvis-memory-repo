"""Command dataclasses for knowledge harvesting operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis_engine.commands.base import ResultBase


@dataclass(frozen=True)
class HarvestTopicCommand:
    """Harvest knowledge about a topic from external AI sources."""

    topic: str
    providers: list[str] | None = None  # None = all available
    max_tokens: int = 2048


@dataclass
class HarvestTopicResult(ResultBase):
    topic: str = ""
    results: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class IngestSessionCommand:
    """Ingest knowledge from a Claude Code or Codex session file."""

    source: str = ""  # "claude" or "codex"
    session_path: str | None = None  # None = discover recent sessions
    project_path: str | None = None  # For Claude Code: scope to project


@dataclass
class IngestSessionResult(ResultBase):
    source: str = ""
    sessions_processed: int = 0
    records_created: int = 0


@dataclass(frozen=True)
class HarvestBudgetCommand:
    """View or set harvest budget limits."""

    action: str = "status"  # "status", "set"
    provider: str | None = None
    period: str | None = None  # "daily" or "monthly"
    limit_usd: float | None = None
    limit_requests: int | None = None


@dataclass
class HarvestBudgetResult(ResultBase):
    summary: dict = field(default_factory=dict)
