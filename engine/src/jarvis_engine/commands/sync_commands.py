"""Command dataclasses for the sync subsystem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncPushCommand:
    """Push encrypted changes from a remote device."""

    device_id: str = ""
    encrypted_payload: str = ""


@dataclass
class SyncPushResult:
    applied: int = 0
    conflicts_resolved: int = 0
    message: str = ""


@dataclass(frozen=True)
class SyncPullCommand:
    """Pull changes for a remote device (returns encrypted payload)."""

    device_id: str = ""
    cursors: str = "{}"  # JSON-encoded dict


@dataclass
class SyncPullResult:
    encrypted_payload: str = ""
    new_cursors: str = "{}"
    has_more: bool = False
    message: str = ""


@dataclass(frozen=True)
class SyncStatusCommand:
    """Query current sync status."""

    pass


@dataclass
class SyncStatusResult:
    changelog_size: int = 0
    cursors: str = "{}"
    message: str = ""
