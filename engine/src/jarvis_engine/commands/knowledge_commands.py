"""Command dataclasses for knowledge graph operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnowledgeStatusCommand:
    as_json: bool = False


@dataclass
class KnowledgeStatusResult:
    node_count: int = 0
    edge_count: int = 0
    locked_count: int = 0
    pending_contradictions: int = 0
    graph_hash: str = ""
    message: str = ""


@dataclass(frozen=True)
class ContradictionListCommand:
    status: str = ""  # Empty = all; "pending" or "resolved" to filter
    limit: int = 20


@dataclass
class ContradictionListResult:
    contradictions: list = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class ContradictionResolveCommand:
    contradiction_id: int = 0
    resolution: str = ""  # "accept_new", "keep_old", "merge"
    merge_value: str = ""


@dataclass
class ContradictionResolveResult:
    success: bool = False
    node_id: str = ""
    resolution: str = ""
    message: str = ""


@dataclass(frozen=True)
class FactLockCommand:
    node_id: str = ""
    action: str = "lock"  # "lock" or "unlock"


@dataclass
class FactLockResult:
    success: bool = False
    node_id: str = ""
    locked: bool = False
    message: str = ""


@dataclass(frozen=True)
class KnowledgeRegressionCommand:
    snapshot_path: str = ""
    as_json: bool = False


@dataclass
class KnowledgeRegressionResult:
    report: dict = field(default_factory=dict)
    message: str = ""
