"""Tests for sync_handlers -- SyncPullHandler, SyncPushHandler, SyncStatusHandler."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

from jarvis_engine.commands.sync_commands import (
    SyncPullCommand,
    SyncPushCommand,
    SyncStatusCommand,
)
from jarvis_engine.handlers.sync_handlers import (
    SyncPullHandler,
    SyncPushHandler,
    SyncStatusHandler,
)

ROOT = Path(".")


# ---------------------------------------------------------------------------
# SyncPullHandler
# ---------------------------------------------------------------------------


def test_pull_no_sync_engine() -> None:
    """Returns unavailable message when sync_engine is None."""
    handler = SyncPullHandler(ROOT, sync_engine=None, transport=MagicMock())
    result = handler.handle(SyncPullCommand(device_id="phone"))
    assert "not available" in result.message


def test_pull_no_transport() -> None:
    """Returns unavailable message when transport is None."""
    handler = SyncPullHandler(ROOT, sync_engine=MagicMock(), transport=None)
    result = handler.handle(SyncPullCommand(device_id="phone"))
    assert "not available" in result.message


def test_pull_missing_device_id() -> None:
    """Returns error when device_id is empty."""
    handler = SyncPullHandler(ROOT, sync_engine=MagicMock(), transport=MagicMock())
    result = handler.handle(SyncPullCommand(device_id=""))
    assert "device_id is required" in result.message


def test_pull_compute_outgoing_failure() -> None:
    """Returns error when compute_outgoing raises."""
    engine = MagicMock()
    engine.compute_outgoing.side_effect = RuntimeError("db locked")
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=MagicMock())
    result = handler.handle(SyncPullCommand(device_id="phone"))
    assert "error" in result.message
    assert "sync pull failed" in result.message


def test_pull_encryption_failure() -> None:
    """Returns error when transport.encrypt raises."""
    engine = MagicMock()
    engine.compute_outgoing.return_value = {"changes": {}, "cursors": {}}
    transport = MagicMock()
    transport.encrypt.side_effect = ValueError("bad key")
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=transport)
    result = handler.handle(SyncPullCommand(device_id="phone"))
    assert "error" in result.message
    assert "encryption failed" in result.message


def test_pull_success_no_more() -> None:
    """Successful pull with fewer than 500 entries -- has_more is False."""
    engine = MagicMock()
    engine.compute_outgoing.return_value = {
        "changes": {"memories": [{"id": 1}]},
        "cursors": {"memories": 42},
    }
    transport = MagicMock()
    transport.encrypt.return_value = b"encrypted_bytes"
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=transport)
    result = handler.handle(SyncPullCommand(device_id="phone"))

    assert result.message == "ok"
    assert result.has_more is False
    decoded = base64.b64decode(result.encrypted_payload)
    assert decoded == b"encrypted_bytes"
    assert json.loads(result.new_cursors) == {"memories": 42}


def test_pull_success_has_more() -> None:
    """has_more is True when a changes category has >= 500 entries."""
    engine = MagicMock()
    engine.compute_outgoing.return_value = {
        "changes": {"memories": list(range(500))},
        "cursors": {},
    }
    transport = MagicMock()
    transport.encrypt.return_value = b"x"
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=transport)
    result = handler.handle(SyncPullCommand(device_id="phone"))

    assert result.message == "ok"
    assert result.has_more is True


def test_pull_empty_changes() -> None:
    """Successful pull with empty changes dict."""
    engine = MagicMock()
    engine.compute_outgoing.return_value = {"changes": {}, "cursors": {}}
    transport = MagicMock()
    transport.encrypt.return_value = b""
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=transport)
    result = handler.handle(SyncPullCommand(device_id="d"))

    assert result.message == "ok"
    assert result.has_more is False


def test_pull_missing_cursors_key() -> None:
    """When outgoing has no 'cursors' key, defaults to empty dict."""
    engine = MagicMock()
    engine.compute_outgoing.return_value = {"changes": {}}
    transport = MagicMock()
    transport.encrypt.return_value = b"data"
    handler = SyncPullHandler(ROOT, sync_engine=engine, transport=transport)
    result = handler.handle(SyncPullCommand(device_id="d"))

    assert json.loads(result.new_cursors) == {}


# ---------------------------------------------------------------------------
# SyncPushHandler
# ---------------------------------------------------------------------------


def test_push_no_sync_engine() -> None:
    handler = SyncPushHandler(ROOT, sync_engine=None, transport=MagicMock())
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload="x"))
    assert "not available" in result.message


def test_push_no_transport() -> None:
    handler = SyncPushHandler(ROOT, sync_engine=MagicMock(), transport=None)
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload="x"))
    assert "not available" in result.message


def test_push_missing_device_id() -> None:
    handler = SyncPushHandler(ROOT, sync_engine=MagicMock(), transport=MagicMock())
    result = handler.handle(SyncPushCommand(device_id="", encrypted_payload="x"))
    assert "device_id is required" in result.message


def test_push_missing_payload() -> None:
    handler = SyncPushHandler(ROOT, sync_engine=MagicMock(), transport=MagicMock())
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload=""))
    assert "encrypted_payload is required" in result.message


def test_push_decryption_failure() -> None:
    """Returns error when base64 decode or transport.decrypt fails."""
    transport = MagicMock()
    transport.decrypt.side_effect = ValueError("bad token")
    handler = SyncPushHandler(ROOT, sync_engine=MagicMock(), transport=transport)
    payload = base64.b64encode(b"garbage").decode("ascii")
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload=payload))
    assert "decryption failed" in result.message


def test_push_apply_incoming_failure() -> None:
    """Returns error when apply_incoming raises."""
    transport = MagicMock()
    transport.decrypt.return_value = {"changes": []}
    engine = MagicMock()
    engine.apply_incoming.side_effect = RuntimeError("conflict")
    handler = SyncPushHandler(ROOT, sync_engine=engine, transport=transport)
    payload = base64.b64encode(b"data").decode("ascii")
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload=payload))
    assert "apply failed" in result.message


def test_push_success_no_errors() -> None:
    """Successful push with no errors."""
    transport = MagicMock()
    transport.decrypt.return_value = {"changes": []}
    engine = MagicMock()
    engine.apply_incoming.return_value = {
        "applied": 5,
        "conflicts_resolved": 1,
        "errors": [],
    }
    handler = SyncPushHandler(ROOT, sync_engine=engine, transport=transport)
    payload = base64.b64encode(b"data").decode("ascii")
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload=payload))

    assert result.message == "ok"
    assert result.applied == 5
    assert result.conflicts_resolved == 1


def test_push_success_with_errors() -> None:
    """Successful push that reports partial errors."""
    transport = MagicMock()
    transport.decrypt.return_value = {}
    engine = MagicMock()
    engine.apply_incoming.return_value = {
        "applied": 3,
        "conflicts_resolved": 0,
        "errors": ["bad row 7", "missing FK"],
    }
    handler = SyncPushHandler(ROOT, sync_engine=engine, transport=transport)
    payload = base64.b64encode(b"x").decode("ascii")
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload=payload))

    assert result.message.startswith("ok with errors")
    assert "bad row 7" in result.message
    assert result.applied == 3


def test_push_invalid_base64() -> None:
    """Non-base64 payload triggers decryption error."""
    transport = MagicMock()
    # base64.b64decode on invalid chars will raise
    handler = SyncPushHandler(ROOT, sync_engine=MagicMock(), transport=transport)
    result = handler.handle(SyncPushCommand(device_id="phone", encrypted_payload="!!!invalid!!!"))
    assert "decryption failed" in result.message


# ---------------------------------------------------------------------------
# SyncStatusHandler
# ---------------------------------------------------------------------------


def test_status_no_sync_engine() -> None:
    handler = SyncStatusHandler(ROOT, sync_engine=None)
    result = handler.handle(SyncStatusCommand())
    assert "not available" in result.message


def test_status_exception() -> None:
    engine = MagicMock()
    engine.sync_status.side_effect = RuntimeError("oops")
    handler = SyncStatusHandler(ROOT, sync_engine=engine)
    result = handler.handle(SyncStatusCommand())
    assert "error" in result.message


def test_status_success() -> None:
    engine = MagicMock()
    engine.sync_status.return_value = {
        "changelog_size": 42,
        "cursors": [{"device": "phone", "pos": 100}],
    }
    handler = SyncStatusHandler(ROOT, sync_engine=engine)
    result = handler.handle(SyncStatusCommand())

    assert result.message == "ok"
    assert result.changelog_size == 42
    parsed = json.loads(result.cursors)
    assert parsed == [{"device": "phone", "pos": 100}]


def test_status_missing_keys() -> None:
    """When sync_status returns empty dict, defaults are used."""
    engine = MagicMock()
    engine.sync_status.return_value = {}
    handler = SyncStatusHandler(ROOT, sync_engine=engine)
    result = handler.handle(SyncStatusCommand())

    assert result.changelog_size == 0
    assert json.loads(result.cursors) == []
