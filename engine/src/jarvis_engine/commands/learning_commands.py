"""Command dataclasses for continuous learning operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LearnInteractionCommand:
    """Learn from a user/assistant interaction pair."""

    user_message: str = ""
    assistant_response: str = ""
    task_id: str = ""
    route: str = (
        ""  # IntentClassifier route name (e.g., "routine", "complex", "math_logic")
    )
    topic: str = ""  # Topic hint for usage pattern mining (first 100 chars of query)


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


@dataclass(frozen=True)
class ConsolidateMemoryCommand:
    """Trigger memory consolidation of episodic records into semantic facts."""

    branch: str = ""  # Restrict to specific branch (empty = all)
    max_groups: int = 20  # Max groups to process
    dry_run: bool = False  # Compute clusters but don't write


@dataclass
class ConsolidateMemoryResult:
    groups_found: int = 0
    records_consolidated: int = 0
    new_facts_created: int = 0
    errors: list = field(default_factory=list)
    message: str = ""
