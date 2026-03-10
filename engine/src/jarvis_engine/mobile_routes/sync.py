from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.sync.auto_sync import AutoSyncConfig

logger = logging.getLogger(__name__)


class SyncRoutesMixin:
    """Endpoint handlers for sync operations."""

    def _ensure_auto_sync(self) -> AutoSyncConfig:
        """Thread-safe lazy init of AutoSyncConfig."""
        auto_sync = self.server._auto_sync_config
        if auto_sync is not None:
            return auto_sync
        with self.server._sync_init_lock:
            if self.server._auto_sync_config is not None:
                return self.server._auto_sync_config
            from jarvis_engine.sync.auto_sync import AutoSyncConfig

            config_path = self._root / ".planning" / "sync" / "auto_sync_config.json"
            auto_sync = AutoSyncConfig(config_path)
            self.server._auto_sync_config = auto_sync
            return auto_sync

    def _handle_get_sync_status(self) -> None:
        if not self._validate_auth(b""):
            return
        sync_engine = self.server.ensure_sync_engine()
        if sync_engine is None:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "Sync not available."},
            )
            return
        try:
            status = sync_engine.sync_status()
            self._write_json(HTTPStatus.OK, {"ok": True, "sync_status": status})
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/status failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Sync status query failed."},
            )

    def _handle_get_sync_config(self) -> None:
        """Return auto-sync configuration for the requesting device."""
        if not self._validate_auth(b""):
            return
        try:
            auto_sync = self._ensure_auto_sync()
            device_id = self.headers.get("X-Jarvis-Device-Id", "unknown")
            config = auto_sync.get_sync_config_for_device(device_id)
            self._write_json(HTTPStatus.OK, {"ok": True, "config": config})
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/config GET failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Failed to get sync config."},
            )

    def _handle_get_sync_heartbeat(self) -> None:
        """Lightweight heartbeat — phone calls this to confirm connectivity."""
        if not self._validate_auth(b""):
            return
        try:
            device_id = self.headers.get("X-Jarvis-Device-Id", "unknown")
            auto_sync = self._ensure_auto_sync()
            auto_sync.record_heartbeat(device_id)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "server_time": int(time.time()),
                    "device_id": device_id,
                },
            )
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/heartbeat failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Heartbeat failed."},
            )

    def _handle_post_sync_deprecated(self) -> None:
        self._write_json(
            HTTPStatus.GONE,
            {
                "ok": False,
                "error": "Deprecated. Use /sync/pull or /sync/push",
                "endpoints": ["/sync/pull", "/sync/push", "/sync/status"],
            },
        )

    def _handle_post_sync_pull(self) -> None:
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id or len(device_id) > 128 or not device_id.isascii():
            self._write_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid device_id."}
            )
            return
        sync_engine = self.server.ensure_sync_engine()
        sync_transport = getattr(self.server, "_sync_transport", None)
        if sync_engine is None or sync_transport is None:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "Sync not available."},
            )
            return
        try:
            import base64 as _b64

            outgoing = sync_engine.compute_outgoing(device_id)
            encrypted = sync_transport.encrypt(outgoing)
            encoded = _b64.b64encode(encrypted).decode("ascii")
            has_more = any(len(v) >= 500 for v in outgoing.get("changes", {}).values())
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "encrypted_payload": encoded,
                    "new_cursors": outgoing.get("cursors", {}),
                    "has_more": has_more,
                },
            )
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/pull failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Sync pull failed."},
            )

    def _handle_post_sync_push(self) -> None:
        payload, _ = self._read_json_body(max_content_length=2_000_000)
        if payload is None:
            return
        device_id = str(payload.get("device_id", "")).strip()
        encrypted_payload = str(payload.get("encrypted_payload", "")).strip()
        if not device_id or len(device_id) > 128 or not device_id.isascii():
            self._write_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid device_id."}
            )
            return
        if not encrypted_payload:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "encrypted_payload is required."},
            )
            return
        sync_engine = self.server.ensure_sync_engine()
        sync_transport = getattr(self.server, "_sync_transport", None)
        if sync_engine is None or sync_transport is None:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "Sync not available."},
            )
            return
        try:
            import base64 as _b64

            try:
                raw_token = _b64.b64decode(encrypted_payload)
            except (ValueError, _b64.binascii.Error) as exc:
                logger.debug("Invalid base64 payload in sync/push: %s", exc)
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Invalid base64 payload."},
                )
                return
            changes = sync_transport.decrypt(raw_token)
            result = sync_engine.apply_incoming(changes, device_id)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "applied": result.get("applied", 0),
                    "conflicts_resolved": result.get("conflicts_resolved", 0),
                    "errors": result.get("errors", []),
                },
            )
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/push failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Sync push failed."},
            )

    def _handle_post_sync_config(self) -> None:
        """Update auto-sync configuration (relay URL, intervals, etc)."""
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        try:
            auto_sync = self._ensure_auto_sync()
            updates = payload.get("config", payload)
            from jarvis_engine.sync.auto_sync import DEFAULT_SYNC_CONFIG

            safe_updates = {
                k: v for k, v in updates.items() if k in DEFAULT_SYNC_CONFIG
            }
            if safe_updates:
                auto_sync.update(safe_updates)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "config": auto_sync.get_all(),
                },
            )
        except Exception as exc:  # boundary: catch-all justified
            logger.error("sync/config POST failed: %s", exc)
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Failed to update sync config."},
            )
