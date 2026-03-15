"""Security status, dashboard, and audit endpoints."""

from __future__ import annotations

import logging
from http import HTTPStatus

from jarvis_engine._constants import GATEWAY_AUDIT_LOG
from jarvis_engine._shared import runtime_dir
from jarvis_engine.mobile_routes._helpers import MobileRouteHandlerProtocol

logger = logging.getLogger(__name__)


class SecurityRoutesMixin:
    """Security status, dashboard, and gateway audit log endpoints."""

    def _handle_get_security_status(self: MobileRouteHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        _sec_orch = getattr(self.server, "security", None)
        if _sec_orch is None:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "error": "Security orchestrator not available.",
                },
            )
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "security": _sec_orch.status(),
            },
        )

    def _handle_get_security_dashboard(self: MobileRouteHandlerProtocol) -> None:
        if not self._validate_auth_flexible(b""):
            return
        server_obj = self.server
        sec = getattr(server_obj, "security", None)
        if sec is None:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "Security orchestrator not available."},
            )
            return
        dashboard = {
            "security_status": sec.status(),
            "recent_actions": sec.action_auditor.recent_actions(20)
            if sec.action_auditor
            else [],
            "scope_violations": sec.scope_enforcer.recent_violations(10)
            if sec.scope_enforcer
            else [],
            "resource_usage": sec.resource_monitor.status()
            if sec.resource_monitor
            else {},
            "heartbeat": getattr(sec, "heartbeat", None).status()
            if getattr(sec, "heartbeat", None)
            else {},
            "threat_intel": getattr(sec, "threat_intel", None).status()
            if getattr(sec, "threat_intel", None)
            else {},
        }
        self._write_json(HTTPStatus.OK, {"ok": True, "dashboard": dashboard})

    def _handle_get_audit(self: MobileRouteHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine._shared import load_jsonl_tail

        audit_path = runtime_dir(self._root) / GATEWAY_AUDIT_LOG
        records = load_jsonl_tail(audit_path, limit=50)
        self._write_json(
            HTTPStatus.OK, {"ok": True, "audit": records, "total": len(records)}
        )
