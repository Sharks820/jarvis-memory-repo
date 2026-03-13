"""Security status, dashboard, and audit endpoints."""

from __future__ import annotations

import logging
from http import HTTPStatus

from jarvis_engine._constants import GATEWAY_AUDIT_LOG as _GATEWAY_AUDIT_LOG
from jarvis_engine._shared import runtime_dir as _runtime_dir
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
                {"ok": False, "error": "Security orchestrator not available"},
            )
            return
        dashboard = {
            "security_status": sec.status(),
            "recent_actions": sec.action_auditor.recent_actions(20)
            if hasattr(sec, "action_auditor") and sec.action_auditor
            else [],
            "scope_violations": sec.scope_enforcer.recent_violations(10)
            if hasattr(sec, "scope_enforcer") and sec.scope_enforcer
            else [],
            "resource_usage": sec.resource_monitor.status()
            if hasattr(sec, "resource_monitor") and sec.resource_monitor
            else {},
            "heartbeat": sec.heartbeat.status()
            if hasattr(sec, "heartbeat") and sec.heartbeat
            else {},
            "threat_intel": sec.threat_intel.status()
            if hasattr(sec, "threat_intel") and sec.threat_intel
            else {},
        }
        self._write_json(HTTPStatus.OK, {"ok": True, "dashboard": dashboard})

    def _handle_get_audit(self: MobileRouteHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine._shared import load_jsonl_tail

        audit_path = _runtime_dir(self._root) / _GATEWAY_AUDIT_LOG
        records = load_jsonl_tail(audit_path, limit=50)
        self._write_json(
            HTTPStatus.OK, {"ok": True, "audit": records, "total": len(records)}
        )
