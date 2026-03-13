"""Command dataclasses for memory operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis_engine.commands.base import ResultBase


@dataclass(frozen=True)
class BrainStatusCommand:
    as_json: bool = False


@dataclass
class BrainStatusResult(ResultBase):
    status: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainContextCommand:
    query: str
    max_items: int = 10
    max_chars: int = 2400
    as_json: bool = False


@dataclass
class BrainContextResult(ResultBase):
    packet: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainCompactCommand:
    keep_recent: int = 1800
    as_json: bool = False


@dataclass
class BrainCompactResult(ResultBase):
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainRegressionCommand:
    as_json: bool = False


@dataclass
class BrainRegressionResult(ResultBase):
    report: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestCommand:
    source: str
    kind: str
    task_id: str
    content: str


@dataclass
class IngestResult(ResultBase):
    record_id: str = ""
    source: str = ""
    kind: str = ""
    task_id: str = ""


@dataclass(frozen=True)
class MemorySnapshotCommand:
    create: bool = False
    verify_path: str | None = None
    note: str = ""


@dataclass
class MemorySnapshotResult:
    created: bool = False
    snapshot_path: str = ""
    metadata_path: str = ""
    signature_path: str = ""
    sha256: str = ""
    file_count: int = 0
    verified: bool = False
    ok: bool = False
    reason: str = ""
    expected_sha256: str = ""
    actual_sha256: str = ""
    message: str = ""


@dataclass(frozen=True)
class MemoryMaintenanceCommand:
    keep_recent: int = 1800
    snapshot_note: str = "nightly"


@dataclass
class MemoryMaintenanceResult(ResultBase):
    report: dict[str, Any] = field(default_factory=dict)
