from __future__ import annotations

import json
import logging
from http import HTTPStatus
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HealthRoutesMixin:
    """Endpoint handlers for health, dashboard, processes, and widget status."""

    def _quick_panel_path(self) -> Path:
        return self._root / "mobile" / "quick_access.html"

    def _quick_panel_html(self) -> str:
        path = self._quick_panel_path()
        if not path.exists():
            return "<h1>Jarvis Quick Panel not found.</h1>"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "<h1>Jarvis Quick Panel unavailable.</h1>"

    def _build_reliability_panel(self, root: Path, *, reliability_cache: dict[str, Any] | None = None) -> dict[str, Any]:
        from jarvis_engine.mobile_routes._helpers import _compute_command_reliability
        from jarvis_engine.runtime_control import read_resource_pressure_state

        panel = reliability_cache if reliability_cache is not None else _compute_command_reliability()
        panel.setdefault("resource_snapshot", {})

        try:
            pressure_state = read_resource_pressure_state(root)
            if isinstance(pressure_state, dict):
                metrics = pressure_state.get("metrics", {})
                panel["last_pressure_level"] = str(pressure_state.get("pressure_level", panel["last_pressure_level"]))
                panel["resource_snapshot"] = {
                    "captured_utc": pressure_state.get("captured_utc", ""),
                    "process_memory_mb": (metrics.get("process_memory_mb", {}) or {}).get("current", 0.0),
                    "process_cpu_pct": (metrics.get("process_cpu_pct", {}) or {}).get("current", 0.0),
                    "embedding_cache_mb": (metrics.get("embedding_cache_mb", {}) or {}).get("current", 0.0),
                }
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("Reliability panel runtime snapshot unavailable: %s", exc)
        return panel

    def _handle_get_health(self) -> None:
        from jarvis_engine._constants import SELF_TEST_HISTORY as _SELF_TEST_HISTORY
        from jarvis_engine._constants import runtime_dir as _runtime_dir

        from jarvis_engine._shared import load_jsonl_tail

        self_test_history_path = _runtime_dir(self._root) / _SELF_TEST_HISTORY
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

    def _handle_get_cert_fingerprint(self) -> None:
        from jarvis_engine.mobile_routes._helpers import _get_cert_fingerprint

        server_obj = self.server
        security_dir = server_obj.repo_root / ".planning" / "security"
        cert_path_str = str(security_dir / "tls_cert.pem")
        if not (security_dir / "tls_cert.pem").exists():
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

    def _handle_get_quick_panel(self) -> None:
        self._write_text(HTTPStatus.OK, "text/html; charset=utf-8", self._quick_panel_html())

    def _handle_get_favicon(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _handle_get_dashboard(self) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        dashboard = build_intelligence_dashboard(self._root)
        dashboard["reliability_panel"] = self._build_reliability_panel(self._root)
        self._write_json(
            HTTPStatus.OK,
            {"ok": True, "dashboard": dashboard},
        )

    def _handle_get_processes(self) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine._constants import runtime_dir as _runtime_dir
        from jarvis_engine.process_manager import list_services

        services = list_services(self._root)
        from jarvis_engine._shared import load_json_file

        ctrl_path = _runtime_dir(self._root) / "control.json"
        control = load_json_file(ctrl_path, {})
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "services": services,
            "control": control,
        })

    def _handle_post_processes_kill(self) -> None:
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        service_name = str(payload.get("service", "")).strip()
        from jarvis_engine.process_manager import SERVICES, kill_service

        if service_name not in SERVICES:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Unknown service: {service_name}"})
            return
        killed = kill_service(service_name, self._root)
        self._write_json(HTTPStatus.OK, {"ok": True, "service": service_name, "killed": killed})

    def _handle_get_widget_status(self) -> None:
        if not self._validate_auth(b""):
            return
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard
        from jarvis_engine.mobile_routes._helpers import _compute_command_reliability

        _reliability = _compute_command_reliability()
        combined: dict[str, Any] = {"ok": True}
        try:
            combined["growth"] = self._gather_intelligence_growth(reliability_cache=_reliability)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError) as exc:
            logger.debug("Intelligence growth gather failed: %s", exc)
            combined["growth"] = {}
        try:
            dash = build_intelligence_dashboard(self._root)
            combined["alerts"] = dash.get("proactive_alerts", [])
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("Proactive alerts gather failed: %s", exc)
            combined["alerts"] = []
        try:
            combined["reliability"] = self._build_reliability_panel(self._root, reliability_cache=_reliability)
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("Reliability panel build failed: %s", exc)
            combined["reliability"] = {}
        try:
            from jarvis_engine.activity_feed import ActivityCategory, get_activity_feed
            from jarvis_engine.mobile_routes._helpers import _serialize_activity_event

            feed = get_activity_feed()
            events = feed.query(limit=10)
            combined["recent_events"] = [
                _serialize_activity_event(e)
                for e in events
                if e.category != ActivityCategory.DAEMON_CYCLE
            ][:10]
        except (ImportError, RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Recent activity events gather failed: %s", exc)
            combined["recent_events"] = []
        self._write_json(HTTPStatus.OK, combined)
