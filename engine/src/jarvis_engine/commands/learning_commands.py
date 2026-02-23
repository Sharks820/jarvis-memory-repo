"""Command dataclasses for continuous learning operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LearnInteractionCommand:
    """Learn from a user/assistant interaction pair."""

    user_message: str = ""
    assistant_response: str = ""
    task_id: str = ""


@dataclass
class LearnInteractionResult:
    records_created: int = 0
    message: str = ""


@dataclass(frozen=True)
class CrossBranchQueryCommand:
    """Query across knowledge branches for cross-domain connections."""

    query: str = ""
    k: int = 10


@dataclass
class CrossBranchQueryResult:
    direct_results: list = field(default_factory=list)
    cross_branch_connections: list = field(default_factory=list)
    branches_involved: list = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class FlagExpiredFactsCommand:
    """Flag facts whose expiration date has passed."""

    pass


@dataclass
class FlagExpiredFactsResult:
    expired_count: int = 0
    message: str = ""
