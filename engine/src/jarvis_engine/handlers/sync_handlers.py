"""Handler classes for sync commands."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis_engine.sync.engine import SyncEngine
    from jarvis_engine.sync.transport import SyncTransport

from jarvis_engine._constants import SUBSYSTEM_ERRORS, SUBSYSTEM_ERRORS_DB

from jarvis_engine.commands.sync_commands import (
    SyncPullCommand,
    SyncPullResult,
    SyncPushCommand,
    SyncPushResult,
    SyncStatusCommand,
    SyncStatusResult,
)

try:
    from cryptography.fernet import InvalidToken as _InvalidToken
except ImportError:  # cryptography not installed

    class _InvalidToken(Exception):  # type: ignore[no-redef]
        """Placeholder — never raised when cryptography is absent."""


logger = logging.getLogger(__name__)

_SUBSYSTEM_ERRORS_DB = SUBSYSTEM_ERRORS_DB


class SyncPullHandler:
    """Compute outgoing changes, encrypt, and return."""

    def __init__(
        self,
        root: Path,
        sync_engine: SyncEngine | None = None,
        transport: SyncTransport | None = None,
    ) -> None:
        self._root = root
        self._sync_engine = sync_engine
        self._transport = transport

    def handle(self, cmd: SyncPullCommand) -> SyncPullResult:
        if self._sync_engine is None:
            return SyncPullResult(message="Sync engine not available.")
        if self._transport is None:
            return SyncPullResult(message="Sync transport not available.")
        if not cmd.device_id:
            return SyncPullResult(message="device_id is required.")

        try:
            outgoing = self._sync_engine.compute_outgoing(cmd.device_id)
        except _SUBSYSTEM_ERRORS_DB as exc:
            logger.error("SyncPull compute_outgoing failed: %s", exc)
            return SyncPullResult(message="error: sync pull failed")

        has_more = any(
            len(entries) >= 500 for entries in outgoing.get("changes", {}).values()
        )

        try:
            cursors = outgoing.get("cursors", {})
            payload: dict[str, Any] = {
                "changes": outgoing["changes"],
                "cursors": cursors,
            }
            encrypted = self._transport.encrypt(payload)
            encoded = base64.b64encode(encrypted).decode("ascii")
        except SUBSYSTEM_ERRORS as exc:
            logger.error("SyncPull encryption failed: %s", exc)
            return SyncPullResult(message="error: encryption failed")
        except _InvalidToken as exc:
            logger.error(
                "SyncPull encryption failed (invalid token): %s", type(exc).__name__
            )
            return SyncPullResult(message="error: encryption failed")

        return SyncPullResult(
            encrypted_payload=encoded,
            new_cursors=json.dumps(cursors),
            has_more=has_more,
            message="ok",
        )


class SyncPushHandler:
    """Decrypt incoming payload and apply changes."""

    def __init__(
        self,
        root: Path,
        sync_engine: SyncEngine | None = None,
        transport: SyncTransport | None = None,
    ) -> None:
        self._root = root
        self._sync_engine = sync_engine
        self._transport = transport

    def handle(self, cmd: SyncPushCommand) -> SyncPushResult:
        if self._sync_engine is None:
            return SyncPushResult(message="Sync engine not available.")
        if self._transport is None:
            return SyncPushResult(message="Sync transport not available.")
        if not cmd.device_id:
            return SyncPushResult(message="device_id is required.")
        if not cmd.encrypted_payload:
            return SyncPushResult(message="encrypted_payload is required.")

        try:
            raw_token = base64.b64decode(cmd.encrypted_payload)
            payload = self._transport.decrypt(raw_token)
        except SUBSYSTEM_ERRORS as exc:
            logger.error("SyncPush decryption failed: %s", exc)
            return SyncPushResult(message="error: decryption failed")
        except _InvalidToken as exc:
            logger.error(
                "SyncPush decryption failed (invalid token): %s", type(exc).__name__
            )
            return SyncPushResult(message="error: decryption failed")

        try:
            result = self._sync_engine.apply_incoming(payload, cmd.device_id)
        except _SUBSYSTEM_ERRORS_DB as exc:
            logger.error("SyncPush apply_incoming failed: %s", exc)
            return SyncPushResult(message="error: apply failed")

        errors = result.get("errors", [])
        msg = "ok" if not errors else f"ok with errors: {'; '.join(errors)}"

        return SyncPushResult(
            applied=result.get("applied", 0),
            conflicts_resolved=result.get("conflicts_resolved", 0),
            message=msg,
        )


class SyncStatusHandler:
    """Return sync status."""

    def __init__(self, root: Path, sync_engine: SyncEngine | None = None) -> None:
        self._root = root
        self._sync_engine = sync_engine

    def handle(self, cmd: SyncStatusCommand) -> SyncStatusResult:
        if self._sync_engine is None:
            return SyncStatusResult(message="Sync engine not available.")

        try:
            status = self._sync_engine.sync_status()
        except _SUBSYSTEM_ERRORS_DB as exc:
            logger.error("SyncStatus failed: %s", exc)
            return SyncStatusResult(message="error: sync status failed")

        return SyncStatusResult(
            changelog_size=status.get("changelog_size", 0),
            cursors=json.dumps(status.get("cursors", [])),
            message="ok",
        )
