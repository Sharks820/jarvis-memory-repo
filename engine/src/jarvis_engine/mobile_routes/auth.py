from __future__ import annotations

import logging
import os
import socket
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from jarvis_engine._shared import now_iso, runtime_dir
from jarvis_engine.ops.gaming_mode import read_gaming_mode_state, write_gaming_mode_state
from jarvis_engine.mobile_routes._helpers import MobileRouteHandlerProtocol
from jarvis_engine.security.owner_guard import read_owner_guard, trust_mobile_device, verify_master_password
from jarvis_engine.ops.runtime_control import (
    read_control_state,
    reset_control_state,
    write_control_state,
)

logger = logging.getLogger(__name__)


class _AuthRoutesHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    def _require_owner_session(self) -> Any | None:
        ...

    def _gaming_state_path(self) -> Path:
        ...

    def _read_gaming_state(self) -> GamingState:
        ...

    def _write_gaming_state(
        self,
        *,
        enabled: bool | None = None,
        auto_detect: bool | None = None,
        reason: str = "",
    ) -> GamingState:
        ...

    def _settings_payload(self) -> SettingsPayload:
        ...


class GamingState(TypedDict, total=False):
    """Typed dict for gaming mode state."""

    enabled: bool
    auto_detect: bool
    reason: str
    updated_utc: str


class OwnerGuardSummary(TypedDict):
    """Shape of the ``owner_guard`` key in settings payload."""

    enabled: bool
    owner_user_id: str
    trusted_mobile_device_count: int


class SettingsPayload(TypedDict):
    """Return shape of ``_settings_payload``."""

    runtime_control: dict[str, Any]
    gaming_mode: GamingState
    owner_guard: OwnerGuardSummary


class AuthRoutesMixin:
    """Endpoint handlers for authentication, bootstrap, and settings."""

    def _require_owner_session(self: _AuthRoutesHandlerProtocol) -> Any | None:
        """Return the server's OwnerSessionManager, or write 503 and return None."""
        session = getattr(self.server, "owner_session", None)
        if session is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Session auth not available.",
            })
        return session

    def _gaming_state_path(self: _AuthRoutesHandlerProtocol) -> Path:
        return runtime_dir(self._root) / "gaming_mode.json"

    def _read_gaming_state(self: _AuthRoutesHandlerProtocol) -> GamingState:
        """Read gaming mode state from the shared daemon_loop implementation."""
        return cast(GamingState, read_gaming_mode_state(state_path=self._gaming_state_path()))

    def _write_gaming_state(
        self: _AuthRoutesHandlerProtocol,
        *,
        enabled: bool | None = None,
        auto_detect: bool | None = None,
        reason: str = "",
    ) -> GamingState:
        state = self._read_gaming_state()
        if enabled is not None:
            state["enabled"] = enabled
        if auto_detect is not None:
            state["auto_detect"] = auto_detect
        if reason.strip():
            state["reason"] = reason.strip()[:200]
        state["updated_utc"] = now_iso()
        state_payload: dict[str, object] = dict(state)
        return cast(
            GamingState,
            write_gaming_mode_state(state_payload, state_path=self._gaming_state_path()),
        )

    def _settings_payload(self: _AuthRoutesHandlerProtocol) -> SettingsPayload:
        control = dict(read_control_state(self._root))
        gaming = self._read_gaming_state()
        owner_guard = read_owner_guard(self._root)
        return {
            "runtime_control": control,
            "gaming_mode": gaming,
            "owner_guard": {
                "enabled": bool(owner_guard.get("enabled", False)),
                "owner_user_id": str(owner_guard.get("owner_user_id", "")),
                "trusted_mobile_device_count": len(owner_guard.get("trusted_mobile_devices", [])),
            },
        }

    def _handle_get_auth_status(self: _AuthRoutesHandlerProtocol) -> None:
        owner_session = self._require_owner_session()
        if owner_session is None:
            return
        status = owner_session.session_status()
        status["ok"] = True
        self._write_json(HTTPStatus.OK, status)

    def _handle_get_settings(self: _AuthRoutesHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "settings": self._settings_payload()})

    def _handle_post_bootstrap(self: _AuthRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(auth=False, max_content_length=6_000)
        if payload is None:
            return
        client_ip = str(self.client_address[0]).strip()
        allow_remote_bootstrap = os.getenv("JARVIS_ALLOW_REMOTE_BOOTSTRAP", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if client_ip not in ("127.0.0.1", "::1") and not allow_remote_bootstrap:
            self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Bootstrap only allowed from localhost."})
            return
        server = self.server
        if server.check_bootstrap_rate(client_ip):
            self._write_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Too many bootstrap attempts. Try again later."},
            )
            return
        master_password = str(payload.get("master_password", "")).strip()
        if not master_password:
            master_password = str(self.headers.get("X-Jarvis-Master-Password", "") or "").strip()
        if not master_password:
            self._unauthorized("Master password is required.")
            return
        root = self._root
        if not verify_master_password(root, master_password):
            server.record_bootstrap_attempt(client_ip)
            self._unauthorized("Invalid master password.")
            return
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            device_id = str(self.headers.get("X-Jarvis-Device-Id", "") or "").strip()
        trusted = False
        if device_id and len(device_id) <= 128 and device_id.isascii():
            trust_mobile_device(root, device_id)
            trusted = True
        bind_addr = self.server.server_address[0]
        port = self.server.server_address[1]
        if bind_addr in ("0.0.0.0", "", "::"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    bind_addr = s.getsockname()[0]
            except OSError as exc:
                logger.debug("Could not determine bind address: %s", exc)
                bind_addr = "127.0.0.1"
        _scheme = "https" if getattr(self.server, "tls_active", False) else "http"
        base_url = f"{_scheme}://{bind_addr}:{port}"
        logger.warning("Bootstrap credentials sent — ensure connection is from localhost only")
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "session": {
                    "base_url": base_url,
                    "token": self.server.auth_token,
                    "signing_key": self.server.signing_key,
                    "device_id": device_id,
                    "trusted_device": trusted,
                },
                "owner_guard": {
                    k: v for k, v in read_owner_guard(root).items()
                    if k not in ("master_password_hash", "master_password_salt_b64", "master_password_iterations")
                },
            },
        )

    def _handle_post_auth_login(self: _AuthRoutesHandlerProtocol) -> None:
        owner_session = self._require_owner_session()
        if owner_session is None:
            return
        payload, _ = self._read_json_body(auth=False, max_content_length=2_000)
        if payload is None:
            return
        password = str(payload.get("password", "")).strip()
        if not password:
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": "Missing required field: password.",
            })
            return
        token = owner_session.authenticate(password)
        if token is None:
            if verify_master_password(self._root, password):
                token = owner_session.create_external_session()
                if token is not None:
                    logger.info("Owner authenticated via master password, session ...%s created", token[-4:])
        if token is None:
            self._write_json(HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "Invalid password.",
            })
            return
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "session_token": token,
        })

    def _handle_post_auth_logout(self: _AuthRoutesHandlerProtocol) -> None:
        owner_session = self._require_owner_session()
        if owner_session is None:
            return
        session_token = str(self.headers.get("X-Jarvis-Session", "") or "").strip()
        if not session_token:
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": "Missing X-Jarvis-Session header.",
            })
            return
        if not owner_session.validate_session(session_token):
            self._write_json(HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "Invalid or expired session.",
            })
            return
        owner_session.logout(session_token)
        self._write_json(HTTPStatus.OK, {"ok": True})

    def _handle_post_auth_lock(self: _AuthRoutesHandlerProtocol) -> None:
        owner_session = self._require_owner_session()
        if owner_session is None:
            return
        session_token = str(self.headers.get("X-Jarvis-Session", "") or "").strip()
        if not session_token or not owner_session.validate_session(session_token):
            self._write_json(HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "Valid session required for lock.",
            })
            return
        owner_session.logout_all()
        self._write_json(HTTPStatus.OK, {"ok": True})

    def _handle_post_settings(self: _AuthRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return

        reason = str(payload.get("reason", "")).strip()[:200]
        reset_raw = payload.get("reset", False)
        if not isinstance(reset_raw, bool):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid reset."})
            return
        reset = reset_raw
        daemon_paused = payload.get("daemon_paused")
        safe_mode = payload.get("safe_mode")
        muted = payload.get("muted")
        mute_until_utc = payload.get("mute_until_utc")
        gaming_enabled = payload.get("gaming_enabled")
        gaming_auto_detect = payload.get("gaming_auto_detect")

        for key, value in (
            ("daemon_paused", daemon_paused),
            ("safe_mode", safe_mode),
            ("muted", muted),
            ("gaming_enabled", gaming_enabled),
            ("gaming_auto_detect", gaming_auto_detect),
        ):
            if value is not None and not isinstance(value, bool):
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid {key}."})
                return
        if mute_until_utc is not None and not isinstance(mute_until_utc, str):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid mute_until_utc."})
            return

        if reset:
            reset_control_state(self._root)
            try:
                self._write_gaming_state(enabled=False, auto_detect=False, reason=reason)
            except PermissionError as exc:
                logger.warning("Gaming state write blocked by permissions: %s", exc)
                self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Unsafe gaming state path."})
                return
        else:
            if any(v is not None for v in (daemon_paused, safe_mode, muted, mute_until_utc)) or reason:
                write_control_state(
                    self._root,
                    daemon_paused=daemon_paused if isinstance(daemon_paused, bool) else None,
                    safe_mode=safe_mode if isinstance(safe_mode, bool) else None,
                    muted=muted if isinstance(muted, bool) else None,
                    mute_until_utc=mute_until_utc if isinstance(mute_until_utc, str) else None,
                    reason=reason,
                )
            if gaming_enabled is not None or gaming_auto_detect is not None or reason:
                try:
                    self._write_gaming_state(
                        enabled=gaming_enabled if isinstance(gaming_enabled, bool) else None,
                        auto_detect=gaming_auto_detect if isinstance(gaming_auto_detect, bool) else None,
                        reason=reason,
                    )
                except PermissionError as exc:
                    logger.warning("Gaming state write blocked by permissions: %s", exc)
                    self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Unsafe gaming state path."})
                    return

        self._write_json(HTTPStatus.OK, {"ok": True, "settings": self._settings_payload()})
