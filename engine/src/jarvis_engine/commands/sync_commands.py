"""Command dataclasses for the sync subsystem."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_engine.commands.base import ResultBase


@dataclass(frozen=True)
class SyncPushCommand:
    """Push encrypted changes from a remote device."""

    device_id: str = ""
    encrypted_payload: str = ""


@dataclass
class SyncPushResult(ResultBase):
    applied: int = 0
    conflicts_resolved: int = 0


@dataclass(frozen=True)
class SyncPullCommand:
    """Pull changes for a remote device (returns encrypted payload)."""

    device_id: str = ""
    cursors: str = "{}"  # JSON-encoded dict


@dataclass
class SyncPullResult(ResultBase):
    encrypted_payload: str = ""
    new_cursors: str = "{}"
    has_more: bool = False


@dataclass(frozen=True)
class SyncStatusCommand:
    """Query current sync status."""

    pass


@dataclass
class SyncStatusResult(ResultBase):
    changelog_size: int = 0
    cursors: str = "{}"
