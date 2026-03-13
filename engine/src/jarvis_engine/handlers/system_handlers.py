"""System handler classes -- adapter shims delegating to existing functions."""

from __future__ import annotations

import json
import logging
import os
import re
import webbrowser
from pathlib import Path
from typing import Any  # used for WeatherHandler.current dict (external API shape)
from urllib.parse import quote
from urllib.request import urlopen

logger = logging.getLogger(__name__)

from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DaemonRunResult,
    DesktopWidgetCommand,
    DesktopWidgetResult,
    GamingModeCommand,
    GamingModeResult,
    LogCommand,
    LogResult,
    MigrateMemoryCommand,
    MigrateMemoryResult,
    MobileDesktopSyncCommand,
    MobileDesktopSyncResult,
    OpenWebCommand,
    OpenWebResult,
    SelfHealCommand,
    SelfHealResult,
    ServeMobileCommand,
    ServeMobileResult,
    StatusCommand,
    StatusResult,
    WeatherCommand,
    WeatherResult,
)


class StatusHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: StatusCommand) -> StatusResult:
        from jarvis_engine.config import load_config
        from jarvis_engine.memory_store import MemoryStore

        config = load_config()
        store = MemoryStore(self._root)
        events = list(store.tail(5))
        return StatusResult(
            profile=config.profile,
            primary_runtime=config.primary_runtime,
            secondary_runtime=config.secondary_runtime,
            security_strictness=config.security_strictness,
            operation_mode=config.operation_mode,
            cloud_burst_enabled=config.cloud_burst_enabled,
            events=events,
        )


class LogHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: LogCommand) -> LogResult:
        from jarvis_engine.memory_store import MemoryStore

        store = MemoryStore(self._root)
        event = store.append(event_type=cmd.event_type, message=cmd.message)
        return LogResult(
            ts=event.ts, event_type=event.event_type, message=event.message
        )


class ServeMobileHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: ServeMobileCommand) -> ServeMobileResult:
        from jarvis_engine.mobile_api import run_mobile_server

        effective_token = cmd.token or os.getenv("JARVIS_MOBILE_TOKEN", "").strip()
        effective_signing_key = (
            cmd.signing_key or os.getenv("JARVIS_MOBILE_SIGNING_KEY", "").strip()
        )
        if not effective_token:
            return ServeMobileResult(return_code=2)
        if not effective_signing_key:
            return ServeMobileResult(return_code=2)
        try:
            run_mobile_server(
                host=cmd.host,
                port=cmd.port,
                auth_token=effective_token,
                signing_key=effective_signing_key,
                repo_root=self._root,
                tls=cmd.tls,
            )
        except KeyboardInterrupt:
            logger.debug("Mobile server stopped by KeyboardInterrupt")
        except RuntimeError as exc:
            logger.warning("Mobile server RuntimeError: %s", exc)
            return ServeMobileResult(return_code=3)
        except OSError as exc:
            logger.warning("Mobile server OSError: %s", exc)
            return ServeMobileResult(return_code=3)
        return ServeMobileResult(return_code=0)


class DaemonRunHandler:
    """Daemon-run is deeply nested loop logic -- delegates to original."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: DaemonRunCommand) -> DaemonRunResult:
        from jarvis_engine.daemon_loop import DaemonConfig, cmd_daemon_run_impl

        cfg = DaemonConfig(
            interval_s=cmd.interval_s,
            snapshot_path=cmd.snapshot_path,
            actions_path=cmd.actions_path,
            execute=cmd.execute,
            approve_privileged=cmd.approve_privileged,
            auto_open_connectors=cmd.auto_open_connectors,
            max_cycles=cmd.max_cycles,
            idle_interval_s=cmd.idle_interval_s,
            idle_after_s=cmd.idle_after_s,
            run_missions=cmd.run_missions,
            sync_every_cycles=cmd.sync_every_cycles,
            self_heal_every_cycles=cmd.self_heal_every_cycles,
            self_test_every_cycles=cmd.self_test_every_cycles,
        )
        rc = cmd_daemon_run_impl(cfg)
        return DaemonRunResult(return_code=rc)


class MobileDesktopSyncHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MobileDesktopSyncCommand) -> MobileDesktopSyncResult:
        from jarvis_engine.resilience import run_mobile_desktop_sync

        report = run_mobile_desktop_sync(self._root)
        return MobileDesktopSyncResult(
            report=report,
            return_code=0 if bool(report.get("sync_ok", False)) else 2,
        )


class SelfHealHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: SelfHealCommand) -> SelfHealResult:
        from jarvis_engine.resilience import run_self_heal

        report = run_self_heal(
            self._root,
            keep_recent=max(200, min(cmd.keep_recent, 50000)),
            snapshot_note=cmd.snapshot_note.strip()[:160] or "self-heal",
            force_maintenance=cmd.force_maintenance,
        )
        rc = 0 if str(report.get("status", "")) in {"ok", "attention"} else 2
        return SelfHealResult(report=report, return_code=rc)


class DesktopWidgetHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: DesktopWidgetCommand) -> DesktopWidgetResult:
        try:
            from jarvis_engine.desktop.widget import run_desktop_widget
        except ImportError as exc:
            logger.warning("desktop_widget module not available: %s", exc)
            return DesktopWidgetResult(return_code=2)
        run_desktop_widget()
        return DesktopWidgetResult(return_code=0)


class GamingModeHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GamingModeCommand) -> GamingModeResult:
        from jarvis_engine import daemon_loop as _daemon_loop_mod

        state = _daemon_loop_mod.read_gaming_mode_state()
        changed = False
        if cmd.enable is not None:
            state["enabled"] = cmd.enable
            changed = True
        if cmd.auto_detect in {"on", "off"}:
            state["auto_detect"] = cmd.auto_detect == "on"
            changed = True
        if cmd.reason.strip():
            state["reason"] = cmd.reason.strip()
        if changed:
            from jarvis_engine._shared import now_iso as _now_iso

            state["updated_utc"] = _now_iso()
            state = _daemon_loop_mod.write_gaming_mode_state(state)

        detected = False
        detected_process = ""
        if bool(state.get("auto_detect", False)):
            detected, detected_process = _daemon_loop_mod.detect_active_game_process()
        effective_enabled = bool(state.get("enabled", False)) or detected

        return GamingModeResult(
            state=dict(state),
            detected=detected,
            detected_process=detected_process,
            effective_enabled=effective_enabled,
        )


class OpenWebHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpenWebCommand) -> OpenWebResult:
        from urllib.parse import urlparse

        candidate = cmd.url.strip()
        if not candidate:
            return OpenWebResult(return_code=2)
        if len(candidate) > 500:
            return OpenWebResult(return_code=2)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", candidate):
            # Has a scheme -- only allow http/https
            if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
                return OpenWebResult(return_code=2)
        else:
            candidate = f"https://{candidate.lstrip('/')}"
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https"):
            return OpenWebResult(return_code=2)
        if not parsed.hostname:
            return OpenWebResult(return_code=2)
        if parsed.username or parsed.password:
            return OpenWebResult(return_code=2)
        webbrowser.open(candidate)
        return OpenWebResult(return_code=0, opened_url=candidate)


class WeatherHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: WeatherCommand) -> WeatherResult:
        target = (
            cmd.location.strip()
            or os.getenv("JARVIS_DEFAULT_LOCATION", "").strip()
            or "New York, NY"
        )[:200]
        encoded_location = quote(target, safe="")
        url = f"https://wttr.in/{encoded_location}?format=j1"
        try:
            with urlopen(url, timeout=12) as resp:  # nosec B310
                raw = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError, TimeoutError) as exc:
            logger.debug("Weather fetch failed for %s: %s", target, type(exc).__name__)
            return WeatherResult(return_code=2, location=target)

        current: dict[str, Any] = {}
        if isinstance(raw, dict):
            values = raw.get("current_condition", [])
            if isinstance(values, list) and values and isinstance(values[0], dict):
                current = values[0]
        if not current:
            return WeatherResult(return_code=2, location=target)

        desc = ""
        desc_raw = current.get("weatherDesc", [])
        if isinstance(desc_raw, list) and desc_raw and isinstance(desc_raw[0], dict):
            desc = str(desc_raw[0].get("value", "")).strip()

        return WeatherResult(
            return_code=0, location=target, current=current, description=desc
        )


class MigrateMemoryHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MigrateMemoryCommand) -> MigrateMemoryResult:
        from jarvis_engine.memory.embeddings import EmbeddingService
        from jarvis_engine.memory.migration import run_full_migration

        embed_service = EmbeddingService()
        from jarvis_engine._shared import memory_db_path as _memory_db_path

        db_path = _memory_db_path(self._root)
        summary = run_full_migration(self._root, db_path, embed_service)
        rc = 0 if summary.get("status") == "ok" else 2
        return MigrateMemoryResult(summary=summary, return_code=rc)
