from __future__ import annotations

import json
import logging
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

from jarvis_engine._constants import SUBSYSTEM_ERRORS
from jarvis_engine.mobile_routes._helpers import MobileRouteHandlerProtocol, MobileRouteServerProtocol

logger = logging.getLogger(__name__)

# Alias — health probes use the shared subsystem error tuple.
_HEALTH_PROBE_ERRORS = SUBSYSTEM_ERRORS


class _HealthRouteServerProtocol(MobileRouteServerProtocol, Protocol):
    repo_root: Path


class _HealthRoutesHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    server: _HealthRouteServerProtocol

    def send_response(self, code: int) -> None:
        ...

    def end_headers(self) -> None:
        ...

    def _write_text(self, status: int, content_type: str, text: str) -> None:
        ...

    def _quick_panel_path(self) -> Path:
        ...

    def _quick_panel_html(self) -> str:
        ...

    def _build_reliability_panel(
        self,
        root: Path,
        *,
        reliability_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def _gather_intelligence_growth(
        self,
        *,
        reliability_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class HealthRoutesMixin:
    """Endpoint handlers for health, dashboard, processes, and widget status."""

    def _quick_panel_path(self: _HealthRoutesHandlerProtocol) -> Path:
        return self._root / "mobile" / "quick_access.html"

    def _quick_panel_html(self: _HealthRoutesHandlerProtocol) -> str:
        path = self._quick_panel_path()
        if not path.exists():
            return "<h1>Jarvis Quick Panel not found.</h1>"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Quick panel HTML read failed: %s", exc)
            return "<h1>Jarvis Quick Panel unavailable.</h1>"

    def _build_reliability_panel(
        self: _HealthRoutesHandlerProtocol,
        root: Path,
        *,
        reliability_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from jarvis_engine.mobile_routes._helpers import _compute_command_reliability
        from jarvis_engine.ops.runtime_control import read_resource_pressure_state

        panel: dict[str, Any] = dict(reliability_cache) if reliability_cache is not None else dict(_compute_command_reliability())
        panel.setdefault("resource_snapshot", {})

        try:
            pressure_state = read_resource_pressure_state(root)
            if isinstance(pressure_state, dict):
                metrics = pressure_state.get("metrics", {})
                if isinstance(metrics, dict):
                    panel["last_pressure_level"] = str(
                        pressure_state.get("pressure_level", panel.get("last_pressure_level", "none"))
                    )
                    panel["resource_snapshot"] = {
                        "captured_utc": pressure_state.get("captured_utc", ""),
                        "process_memory_mb": (metrics.get("process_memory_mb", {}) or {}).get("current", 0.0),
                        "process_cpu_pct": (metrics.get("process_cpu_pct", {}) or {}).get("current", 0.0),
                        "embedding_cache_mb": (metrics.get("embedding_cache_mb", {}) or {}).get("current", 0.0),
                    }
        except _HEALTH_PROBE_ERRORS as exc:
            logger.debug("Reliability panel runtime snapshot unavailable: %s", exc)
        return panel

    def _handle_get_health(self: Any) -> None:
        from jarvis_engine._constants import SELF_TEST_HISTORY
        from jarvis_engine._shared import load_jsonl_tail
        from jarvis_engine._shared import runtime_dir

        self_test_history_path = runtime_dir(self._root) / SELF_TEST_HISTORY
        intelligence_status: dict[str, Any] = {"score": 0.0, "regression": False, "last_test": ""}
        try:
            tail = load_jsonl_tail(self_test_history_path, limit=1)
            if tail:
                latest = tail[0]
                intelligence_status["score"] = latest.get("average_score", 0.0)
                intelligence_status["last_test"] = latest.get("timestamp", "")
                intelligence_status["regression"] = latest.get("below_threshold", False)
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            logger.debug("self-test history parse failed: %s", exc)
        self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy", "intelligence": intelligence_status})

    def _handle_get_cert_fingerprint(self: Any) -> None:
        from jarvis_engine.mobile_routes._helpers import _get_cert_fingerprint

        server_obj = self.server
        security_dir = server_obj.repo_root / ".planning" / "security"
        cert_path = security_dir / "tls_cert.pem"
        cert_path_str = str(cert_path)
        if not cert_path.exists():
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "No TLS certificate found."})
            return
        fingerprint = _get_cert_fingerprint(cert_path_str)
        if fingerprint is None:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Failed to compute fingerprint."})
            return
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "fingerprint": fingerprint,
            "algorithm": "sha256",
        })

    def _handle_get_quick_panel(self: _HealthRoutesHandlerProtocol) -> None:
        self._write_text(HTTPStatus.OK, "text/html; charset=utf-8", self._quick_panel_html())

    def _handle_get_favicon(self: _HealthRoutesHandlerProtocol) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _handle_get_dashboard(self: _HealthRoutesHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        dashboard_payload = dict(build_intelligence_dashboard(self._root))
        dashboard_payload["reliability_panel"] = self._build_reliability_panel(self._root)
        self._write_json(HTTPStatus.OK, {"ok": True, "dashboard": dashboard_payload})

    def _handle_get_processes(self: _HealthRoutesHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine._shared import load_json_file
        from jarvis_engine._shared import runtime_dir
        from jarvis_engine.ops.process_manager import list_services

        services = list_services(self._root)
        ctrl_path = runtime_dir(self._root) / "control.json"
        control = load_json_file(ctrl_path, {})
        self._write_json(HTTPStatus.OK, {"ok": True, "services": services, "control": control})

    def _handle_post_processes_kill(self: _HealthRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        service_name = str(payload.get("service", "")).strip()
        from jarvis_engine.ops.process_manager import SERVICES, kill_service

        if service_name not in SERVICES:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Unknown service: {service_name}"})
            return
        killed = kill_service(service_name, self._root)
        self._write_json(HTTPStatus.OK, {"ok": True, "service": service_name, "killed": killed})

    def _handle_get_widget_status(self: _HealthRoutesHandlerProtocol) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard
        from jarvis_engine.mobile_routes._helpers import _compute_command_reliability, _serialize_activity_event

        reliability_cache = dict(_compute_command_reliability())
        combined: dict[str, Any] = {"ok": True}
        try:
            combined["growth"] = self._gather_intelligence_growth(reliability_cache=reliability_cache)
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("Intelligence growth gather failed: %s", exc)
            combined["growth"] = {}
        try:
            dash = build_intelligence_dashboard(self._root)
            combined["alerts"] = dash.get("proactive_alerts", [])
        except _HEALTH_PROBE_ERRORS as exc:
            logger.debug("Proactive alerts gather failed: %s", exc)
            combined["alerts"] = []
        try:
            combined["reliability"] = self._build_reliability_panel(self._root, reliability_cache=reliability_cache)
        except _HEALTH_PROBE_ERRORS as exc:
            logger.debug("Reliability panel build failed: %s", exc)
            combined["reliability"] = {}
        try:
            from jarvis_engine.activity_feed import ActivityCategory, get_activity_feed

            feed = get_activity_feed()
            events = feed.query(limit=10)
            combined["recent_events"] = [
                _serialize_activity_event(e)
                for e in events
                if e.category != ActivityCategory.DAEMON_CYCLE
            ][:10]
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("Recent activity events gather failed: %s", exc)
            combined["recent_events"] = []
        try:
            from jarvis_engine.learning.missions import get_now_working_on

            combined["now_working_on"] = get_now_working_on(self._root)
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("now_working_on gather failed: %s", exc)
            combined["now_working_on"] = None
        self._write_json(HTTPStatus.OK, combined)

    def _handle_get_gateway_health(self: _HealthRoutesHandlerProtocol) -> None:
        """GET /gateway/health — per-provider health and circuit breaker status."""
        if not self._validate_auth(b""):
            return
        gateway = getattr(self.server, "gateway", None)
        if gateway is None:
            self._write_json(HTTPStatus.OK, {"ok": True, "providers": {}})
            return
        health_tracker = getattr(gateway, "_health", None)
        if health_tracker is None:
            self._write_json(HTTPStatus.OK, {"ok": True, "providers": {}})
            return
        try:
            provider_health = health_tracker.all_health()
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("Gateway health query failed: %s", exc)
            provider_health = {}
        self._write_json(HTTPStatus.OK, {"ok": True, "providers": provider_health})

    def _handle_get_gateway_budget(self: _HealthRoutesHandlerProtocol) -> None:
        """GET /gateway/budget — current budget utilisation snapshot."""
        if not self._validate_auth(b""):
            return
        gateway = getattr(self.server, "gateway", None)
        if gateway is None:
            self._write_json(HTTPStatus.OK, {"ok": True, "budget": {}})
            return
        budget = getattr(gateway, "_budget", None)
        if budget is None:
            self._write_json(HTTPStatus.OK, {"ok": True, "budget": {}})
            return
        try:
            from dataclasses import asdict

            budget_dict = asdict(budget.status())
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("Gateway budget query failed: %s", exc)
            budget_dict = {}
        self._write_json(HTTPStatus.OK, {"ok": True, "budget": budget_dict})

    def _handle_get_memory_hygiene(self: _HealthRoutesHandlerProtocol) -> None:
        """GET /memory/hygiene — memory quality distribution and cleanup status."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.memory.hygiene import hygiene_dashboard_metrics

            metrics = hygiene_dashboard_metrics(self._root)
        except _HEALTH_PROBE_ERRORS as exc:
            logger.debug("Memory hygiene metrics failed: %s", exc)
            metrics = {}
        self._write_json(HTTPStatus.OK, {"ok": True, "hygiene": metrics})

    def _handle_get_diagnostics(self: _HealthRoutesHandlerProtocol) -> None:
        """GET /diagnostics/status — run quick scan, return health score + issues."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.self_diagnosis import DiagnosticEngine

            diag = DiagnosticEngine(self._root)
            issues = diag.run_quick_scan()
            score = diag.health_score(issues)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "healthy": score >= 70,
                "score": score,
                "issues": [i.to_dict() for i in issues],
            })
        except _HEALTH_PROBE_ERRORS as exc:
            logger.debug("Diagnostics scan failed: %s", exc)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "healthy": False,
                "score": 0,
                "issues": [],
                "error": f"diagnostics engine failed: {exc}",
            })
