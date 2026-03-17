"""System, memory, and desktop CLI command handlers.

Extracted from main.py to improve file health and separation of concerns.
Contains: status, log, ingest, serve-mobile, desktop-widget, gaming-mode,
runtime-control, daemon-run, memory-snapshot, memory-maintenance,
migrate-memory, self-heal, memory-eval, persona-config, weather, open-web,
mobile-desktop-sync.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from jarvis_engine._bus import get_bus as _get_bus
from jarvis_engine._cli_helpers import cli_dispatch
from jarvis_engine._compat import UTC
from jarvis_engine._shared import make_task_id, memory_db_path, set_process_title
from jarvis_engine.memory.auto_ingest import auto_ingest_memory as _auto_ingest_memory
from jarvis_engine.config import repo_root
from jarvis_engine.ops.gaming_mode import gaming_processes_path

from jarvis_engine.commands.memory_commands import (
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
)
from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DesktopWidgetCommand,
    GamingModeCommand,
    LogCommand,
    MigrateMemoryCommand,
    MobileDesktopSyncCommand,
    OpenWebCommand,
    SelfHealCommand,
    StatusCommand,
    WeatherCommand,
)
from jarvis_engine.commands.security_commands import (
    PersonaConfigCommand,
    RuntimeControlCommand,
)

logger = logging.getLogger(__name__)


def cmd_gaming_mode(enable: bool | None, reason: str, auto_detect: str) -> int:
    result = _get_bus().dispatch(GamingModeCommand(enable=enable, reason=reason, auto_detect=auto_detect))
    state = result.state
    print("gaming_mode")
    print(f"enabled={bool(state.get('enabled', False))}")
    print(f"auto_detect={bool(state.get('auto_detect', False))}")
    print(f"auto_detect_active={result.detected}")
    if result.detected_process:
        print(f"detected_process={result.detected_process}")
    print(f"effective_enabled={result.effective_enabled}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_autopilot_paused_when_enabled")
    print(f"process_config={gaming_processes_path()}")
    return 0


def cmd_status() -> int:
    result = _get_bus().dispatch(StatusCommand())
    print("Jarvis Engine Status")
    print(f"profile={result.profile}")
    print(f"primary_runtime={result.primary_runtime}")
    print(f"secondary_runtime={result.secondary_runtime}")
    print(f"security_strictness={result.security_strictness}")
    print(f"operation_mode={result.operation_mode}")
    print(f"cloud_burst_enabled={result.cloud_burst_enabled}")
    print("recent_events:")
    if not result.events:
        print("- none")
    else:
        for event in result.events:
            print(f"- [{event.ts}] {event.event_type}: {event.message}")
    # Structured response for UI consumption (UI-05)
    print(f"response=Engine status: {result.profile} profile, {result.operation_mode} mode, "
          f"runtime={result.primary_runtime}")
    return 0


def cmd_log(event_type: str, message: str) -> int:
    result = _get_bus().dispatch(LogCommand(event_type=event_type, message=message))
    print(f"logged: [{result.ts}] {result.event_type}: {result.message}")
    return 0


def cmd_ingest(source: str, kind: str, task_id: str, content: str) -> int:
    result = _get_bus().dispatch(IngestCommand(source=source, kind=kind, task_id=task_id, content=content))
    print(f"ingested: id={result.record_id} source={result.source} kind={result.kind} task_id={result.task_id}")
    return 0


def cmd_serve_mobile(host: str, port: int, token: str | None, signing_key: str | None, allow_insecure_bind: bool = False, config_file: str | None = None, tls: bool | None = None) -> int:
    from jarvis_engine._shared import load_json_file
    from jarvis_engine.mobile_routes.lifecycle import run_mobile_server

    # Load credentials from config file if provided
    if config_file:
        config_path = Path(config_file)
        if not config_path.exists():
            print(f"error: config file not found: {config_file}")
            return 2

        config_data = load_json_file(config_path, None)
        if config_data is None:
            print(f"error: failed to read config file: {config_file}")
            return 2
        # CLI args override config file values
        if not token:
            token = config_data.get("token")
        if not signing_key:
            signing_key = config_data.get("signing_key")

    effective_token = token or os.getenv("JARVIS_MOBILE_TOKEN", "").strip()
    effective_signing_key = signing_key or os.getenv("JARVIS_MOBILE_SIGNING_KEY", "").strip()
    if not effective_token:
        print("error: missing mobile token. pass --token or set JARVIS_MOBILE_TOKEN")
        return 2
    if not effective_signing_key:
        print("error: missing signing key. pass --signing-key or set JARVIS_MOBILE_SIGNING_KEY")
        return 2

    if allow_insecure_bind:
        os.environ["JARVIS_ALLOW_INSECURE_MOBILE_BIND"] = "true"

    # Token rotation warning: check config file age if loaded from file
    if config_file:
        try:
            _cfg_text = Path(config_file).read_text(encoding="utf-8")
            _cfg_data = json.loads(_cfg_text)
            _created_utc = _cfg_data.get("created_utc", "")
            if _created_utc:
                _created_dt = datetime.fromisoformat(_created_utc.replace("Z", "+00:00"))
                _now_utc = datetime.now(tz=_created_dt.tzinfo) if _created_dt.tzinfo else datetime.now(UTC)
                _age_days = (_now_utc - _created_dt).days
                if _age_days > 90:
                    print(
                        f"warning: mobile API credential bundle is {_age_days} days old. "
                        f"Consider rotating via: delete {config_file} and restart"
                    )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            logger.debug("Non-fatal: could not parse mobile API config for age check: %s", exc)

    # Set descriptive process title for Task Manager visibility
    set_process_title("jarvis-mobile-api")

    root = repo_root()
    # Register PID file for duplicate detection and dashboard visibility
    from jarvis_engine.ops.process_manager import is_service_running, write_pid_file, remove_pid_file
    if is_service_running("mobile_api", root):
        print("error: mobile API is already running")
        return 4

    # NOTE: run_mobile_server is called directly here (not via bus) so that
    # tests can monkeypatch main_mod.run_mobile_server.
    try:
        write_pid_file("mobile_api", root)
        run_mobile_server(
            host=host,
            port=port,
            auth_token=effective_token,
            signing_key=effective_signing_key,
            repo_root=root,
            tls=tls,
        )
    except KeyboardInterrupt:
        print("\nmobile_api_stopped=true")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 3
    except OSError as exc:
        print(f"error: could not bind mobile API on {host}:{port}: {exc}")
        return 3
    finally:
        remove_pid_file("mobile_api", root)
    return 0


def cmd_desktop_widget() -> int:
    set_process_title("jarvis-widget")
    root = repo_root()
    from jarvis_engine.ops.process_manager import is_service_running, kill_service, write_pid_file, remove_pid_file
    if is_service_running("widget", root):
        print("error: widget is already running")
        return 4
    import atexit

    def _cleanup_on_exit() -> None:
        """Kill child services if widget exits unexpectedly."""
        try:
            for svc in ("mobile_api", "daemon"):
                try:
                    kill_service(svc, root)
                except (OSError, ValueError):
                    pass
        except Exception:
            pass

    try:
        write_pid_file("widget", root)
        atexit.register(_cleanup_on_exit)
        result = _get_bus().dispatch(DesktopWidgetCommand())
        if result.return_code != 0:
            print("error: desktop widget unavailable")
        return result.return_code
    finally:
        atexit.unregister(_cleanup_on_exit)
        remove_pid_file("widget", root)


def cmd_runtime_control(
    *,
    pause: bool,
    resume: bool,
    safe_on: bool,
    safe_off: bool,
    reset: bool,
    reason: str,
) -> int:

    _bus = _get_bus()

    result = _bus.dispatch(RuntimeControlCommand(
        pause=pause, resume=resume, safe_on=safe_on, safe_off=safe_off, reset=reset, reason=reason,
    ))

    state = result.state
    print("runtime_control")
    print(f"daemon_paused={bool(state.get('daemon_paused', False))}")
    print(f"safe_mode={bool(state.get('safe_mode', False))}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_paused_skips_autopilot,safe_mode_forces_non_executing_cycles")
    return 0


def cmd_persona_config(
    *,
    enable: bool,
    disable: bool,
    humor_level: int | None,
    mode: str,
    style: str,
) -> int:
    result = _get_bus().dispatch(PersonaConfigCommand(
        enable=enable, disable=disable, humor_level=humor_level, mode=mode, style=style,
    ))
    cfg = result.config

    # Handler returns a dict with "error" key on conflicting flags
    if isinstance(cfg, dict) and "error" in cfg:
        print(f"error={cfg['error']}")
        return 1

    print("persona_config")
    print(f"enabled={cfg.enabled}")
    print(f"mode={cfg.mode}")
    print(f"style={cfg.style}")
    print(f"humor_level={cfg.humor_level}")
    print(f"updated_utc={cfg.updated_utc}")
    return 0


def cmd_memory_snapshot(create: bool, verify_path: str | None, note: str) -> int:
    result = _get_bus().dispatch(MemorySnapshotCommand(create=create, verify_path=verify_path, note=note))
    if result.created:
        print("memory_snapshot_created=true")
        print(f"snapshot_path={result.snapshot_path}")
        print(f"metadata_path={result.metadata_path}")
        print(f"signature_path={result.signature_path}")
        print(f"sha256={result.sha256}")
        print(f"file_count={result.file_count}")
        return 0
    if result.verified:
        print("memory_snapshot_verification")
        print(f"ok={result.ok}")
        print(f"reason={result.reason}")
        print(f"expected_sha256={result.expected_sha256}")
        print(f"actual_sha256={result.actual_sha256}")
        return 0 if result.ok else 2
    print("error: choose --create or --verify-path")
    return 2


def cmd_memory_maintenance(keep_recent: int, snapshot_note: str) -> int:
    result = _get_bus().dispatch(MemoryMaintenanceCommand(keep_recent=keep_recent, snapshot_note=snapshot_note))
    report = result.report
    print("memory_maintenance")
    print(f"status={report.get('status', 'unknown')}")
    print(f"report_path={report.get('report_path', '')}")
    compact = report.get("compact", {})
    if isinstance(compact, dict):
        print(f"compacted={compact.get('compacted', False)}")
        print(f"total_records={compact.get('total_records', 0)}")
        print(f"kept_records={compact.get('kept_records', 0)}")
    regression = report.get("regression", {})
    if isinstance(regression, dict):
        print(f"regression_status={regression.get('status', '')}")
        print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
        print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    snapshot = report.get("snapshot", {})
    if isinstance(snapshot, dict):
        print(f"snapshot_path={snapshot.get('path', '')}")
    return 0


def cmd_migrate_memory() -> int:
    """Migrate JSONL/JSON memory data into SQLite (one-time command)."""
    result = _get_bus().dispatch(MigrateMemoryCommand())
    if result.return_code != 0:
        print("error: memory migration failed")
        return result.return_code
    summary = result.summary
    totals = summary.get("totals", {})
    print("memory_migration_complete")
    print(f"total_inserted={totals.get('inserted', 0)}")
    print(f"total_skipped={totals.get('skipped', 0)}")
    print(f"total_errors={totals.get('errors', 0)}")
    print(f"db_path={summary.get('db_path', '')}")
    return 0


def cmd_mobile_desktop_sync(*, auto_ingest: bool, as_json: bool) -> int:
    bus_result, _ = cli_dispatch(
        MobileDesktopSyncCommand(auto_ingest=auto_ingest, as_json=as_json),
        as_json=as_json, json_field="report",
    )
    report = bus_result.report
    if not as_json:
        print("mobile_desktop_sync")
        print(f"sync_ok={report.get('sync_ok', False)}")
        print(f"report_path={report.get('report_path', '')}")
        checks = report.get("checks", [])
        if isinstance(checks, list):
            for row in checks:
                if not isinstance(row, dict):
                    continue
                print(f"check_{row.get('name','')}={row.get('ok', False)}")
    if auto_ingest:
        _auto_ingest_memory(
            source="task_outcome",
            kind="episodic",
            task_id=make_task_id("sync"),
            content=(
                f"Mobile/Desktop sync executed. "
                f"sync_ok={report.get('sync_ok', False)}; "
                f"trusted_mobile_devices={report.get('owner_guard', {}).get('trusted_mobile_device_count', 0)}"
            ),
        )
    return bus_result.return_code


def cmd_self_heal(*, force_maintenance: bool, keep_recent: int, snapshot_note: str, as_json: bool) -> int:
    bus_result, _ = cli_dispatch(
        SelfHealCommand(
            force_maintenance=force_maintenance, keep_recent=keep_recent,
            snapshot_note=snapshot_note, as_json=as_json,
        ),
        as_json=as_json, json_field="report",
    )
    if not as_json:
        report = bus_result.report
        print("self_heal")
        print(f"status={report.get('status', 'unknown')}")
        print(f"report_path={report.get('report_path', '')}")
        actions = report.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                print(f"action={action}")
        regression = report.get("regression", {})
        if isinstance(regression, dict):
            print(f"regression_status={regression.get('status', '')}")
            print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
            print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    return bus_result.return_code


def cmd_memory_eval() -> int:
    from jarvis_engine.learning.growth_tracker import (
        DEFAULT_MEMORY_TASKS,
        run_memory_eval,
    )

    root = repo_root()
    db_path = memory_db_path(root)

    engine = None
    embed_service = None
    if db_path.exists():
        try:
            from jarvis_engine.memory.embeddings import EmbeddingService
            from jarvis_engine.memory.engine import MemoryEngine

            embed_service = EmbeddingService()
            engine = MemoryEngine(db_path, embed_service=embed_service)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"error=failed to init memory engine: {exc}")
            return 1

    if engine is None or embed_service is None:
        print(f"error=memory_db_missing path={db_path}")
        return 1

    try:
        results = run_memory_eval(DEFAULT_MEMORY_TASKS, engine, embed_service)
    except RuntimeError as exc:
        print(f"error={exc}")
        return 1

    for r in results:
        print(
            f"task={r.task_id} score={r.overall_score:.2f} "
            f"results={r.results_found} branch_cov={r.branch_coverage:.2f} "
            f"kw_cov={r.keyword_coverage:.2f}"
        )

    if results:
        avg = sum(r.overall_score for r in results) / len(results)
        print(f"average_score={avg:.4f}")
    else:
        print("average_score=0.0000")
    return 0


def cmd_weather(location: str) -> int:
    result = _get_bus().dispatch(WeatherCommand(location=location))
    if result.return_code != 0:
        print("error: weather lookup failed")
        return result.return_code

    c = result.current
    print("weather_report")
    print(f"location={result.location}")
    if result.description:
        print(f"conditions={result.description}")
    print(f"temperature_f={c.get('temp_F', '')}°F")
    print(f"temperature_c={c.get('temp_C', '')}°C")
    print(f"feels_like_f={c.get('FeelsLikeF', '')}°F")
    print(f"feels_like_c={c.get('FeelsLikeC', '')}°C")
    print(f"humidity={c.get('humidity', '')}%")
    print(f"wind_speed_mph={c.get('windspeedMiles', '')}")
    print(f"wind_direction={c.get('winddir16Point', '')}")
    print(f"visibility_miles={c.get('visibilityMiles', '')}")
    print(f"uv_index={c.get('uvIndex', '')}")
    print(f"pressure_mb={c.get('pressure', '')}")
    print(f"precipitation_mm={c.get('precipMM', '')}")
    print(f"cloud_cover={c.get('cloudcover', '')}%")
    return 0


def cmd_open_web(url: str) -> int:
    result, _ = cli_dispatch(OpenWebCommand(url=url))
    if result.return_code != 0:
        print("error=No URL provided or invalid URL.")
        return result.return_code
    print(f"opened_url={result.opened_url}")
    return 0


def cmd_daemon_run(
    interval_s: int,
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    max_cycles: int,
    idle_interval_s: int,
    idle_after_s: int,
    run_missions: bool,
    sync_every_cycles: int = 5,
    self_heal_every_cycles: int = 20,
    self_test_every_cycles: int = 20,
) -> int:
    result = _get_bus().dispatch(DaemonRunCommand(
        interval_s=interval_s, snapshot_path=snapshot_path, actions_path=actions_path,
        execute=execute, approve_privileged=approve_privileged,
        auto_open_connectors=auto_open_connectors, max_cycles=max_cycles,
        idle_interval_s=idle_interval_s, idle_after_s=idle_after_s,
        run_missions=run_missions, sync_every_cycles=sync_every_cycles,
        self_heal_every_cycles=self_heal_every_cycles,
        self_test_every_cycles=self_test_every_cycles,
    ))
    return result.return_code
