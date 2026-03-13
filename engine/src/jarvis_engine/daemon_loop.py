"""Daemon loop — extracted from main.py for better separation of concerns."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_engine._bus import get_bus
from jarvis_engine._compat import UTC
from jarvis_engine._constants import (
    DEFAULT_API_PORT,
    KG_METRICS_LOG,
    SELF_TEST_HISTORY,
    SUBSYSTEM_ERRORS,
    SUBSYSTEM_ERRORS_DB,
)
from jarvis_engine._shared import memory_db_path, now_iso, runtime_dir, set_process_title
from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.ops_commands import MissionRunCommand
from jarvis_engine.config import repo_root
from jarvis_engine.gaming_mode import (
    _windows_idle_seconds,
    detect_active_game_process,
    read_gaming_mode_state,
)
from jarvis_engine.harvest_discovery import discover_harvest_topics
from jarvis_engine.learning_missions import load_missions
from jarvis_engine.ops.runtime_control import (
    capture_runtime_resource_snapshot,
    read_control_state,
    recommend_daemon_sleep,
    write_resource_pressure_state,
)

logger = logging.getLogger(__name__)


def _emit(line: str) -> None:
    """Emit a structured key=value status line.

    All daemon structured output goes through this single function so the
    transport can be swapped (e.g. to a socket or log file) without hunting
    for scattered ``print()`` calls.
    """
    print(line)  # noqa: T201 — intentional structured output


# Parameter bundle dataclasses

@dataclass
class CycleState:
    """Typed state bundle returned by ``_gather_cycle_state``.

    Supports ``state["key"]`` dict-style access for backward compatibility
    with tests that substitute plain dicts via monkeypatch.
    """

    idle_seconds: float | None
    is_active: bool
    sleep_seconds: int
    resource_snapshot: dict
    pressure_level: str
    skip_heavy_tasks: bool
    gaming_state: dict
    control_state: dict
    auto_detect: bool
    detected_process: str
    gaming_mode_enabled: bool
    daemon_paused: bool
    safe_mode: bool

    # dict-style access for backward compat with tests returning plain dicts
    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        return getattr(self, key, default)

    def keys(self):
        return [f.name for f in self.__dataclass_fields__.values()]


@dataclass
class DaemonConfig:
    """Configuration bundle for cmd_daemon_run_impl."""

    interval_s: int = 180
    snapshot_path: Path = field(default_factory=lambda: Path("ops_snapshot.live.json"))
    actions_path: Path = field(default_factory=lambda: Path("actions.generated.json"))
    execute: bool = False
    approve_privileged: bool = False
    auto_open_connectors: bool = False
    max_cycles: int = 0
    idle_interval_s: int = 900
    idle_after_s: int = 300
    run_missions: bool = False
    sync_every_cycles: int = 5
    self_heal_every_cycles: int = 20
    self_test_every_cycles: int = 20
    watchdog_every_cycles: int = 5


# Daemon-scoped bus cache (avoids recreating MemoryEngine per periodic task)
_daemon_bus: CommandBus | None = None
_daemon_bus_lock = threading.Lock()

# Mission failure backoff: skip missions for N cycles after a failure
_MISSION_BACKOFF_CYCLES = 5
_mission_backoff_until_cycle: int = 0

# Maximum allowed duration for a single daemon cycle (seconds).
# If a cycle exceeds this, a WARNING is logged.
_CYCLE_TIMEOUT_S = 600

# Tracks the start time of the current cycle (set before each cycle).
_cycle_start: float = 0.0


def _emit_cycle_failure(event: str, exc: Exception, *, message: str) -> None:
    """Log and surface a subsystem failure using the daemon's standard contract."""
    logger.warning("%s: %s", message, exc)
    _emit(f"{event}_error={exc}")


def _watchdog_check() -> dict:
    """Check cycle health and return a status dict.

    Returns dict with keys:
    - ``healthy`` (bool): True if current cycle is within timeout
    - ``elapsed_s`` (float): seconds since cycle started (0 if not started)
    - ``timeout_s`` (int): the configured timeout
    """
    if _cycle_start <= 0:
        return {"healthy": True, "elapsed_s": 0.0, "timeout_s": _CYCLE_TIMEOUT_S}
    elapsed = time.monotonic() - _cycle_start
    return {
        "healthy": elapsed < _CYCLE_TIMEOUT_S,
        "elapsed_s": round(elapsed, 2),
        "timeout_s": _CYCLE_TIMEOUT_S,
    }


def _get_daemon_bus() -> CommandBus:
    """Return cached daemon bus, creating once on first call (thread-safe).

    Always acquires the lock to avoid double-checked locking, which relies
    on the GIL for correctness and breaks under free-threaded Python 3.13t+.
    The lock overhead is negligible since daemon cycles run every 30+ seconds.
    """
    global _daemon_bus  # lazy singleton: avoid recreating MemoryEngine per cycle
    with _daemon_bus_lock:
        if _daemon_bus is None:
            _daemon_bus = get_bus()
        return _daemon_bus


# Daemon cycle state for KG regression tracking
_daemon_kg_prev_metrics: dict | None = None
_daemon_kg_prev_metrics_lock = threading.Lock()


# Gaming mode helpers — now live in gaming_mode.py, imported above.


# Mission run helpers


def cmd_mission_run(mission_id: str, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    result = _get_daemon_bus().dispatch(MissionRunCommand(
        mission_id=mission_id, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: mission run failed")
        return result.return_code

    report = result.report
    _emit("learning_mission_completed=true")
    _emit(f"mission_id={report.get('mission_id', '')}")
    _emit(f"candidate_count={report.get('candidate_count', 0)}")
    _emit(f"verified_count={report.get('verified_count', 0)}")
    verified = report.get("verified_findings", [])
    if isinstance(verified, list):
        for idx, finding in enumerate(verified[:10], start=1):
            statement = str(finding.get("statement", "")) if isinstance(finding, dict) else ""
            sources = ",".join(finding.get("source_domains", [])) if isinstance(finding, dict) else ""
            _emit(f"verified_{idx}={statement}")
            _emit(f"verified_{idx}_sources={sources}")

    if result.ingested_record_id:
        _emit(f"mission_ingested_record_id={result.ingested_record_id}")
    return 0


def _run_next_pending_mission(*, max_results: int = 6, max_pages: int = 10) -> int:
    missions = load_missions(repo_root())
    for mission in missions:
        if str(mission.get("status", "")).lower() != "pending":
            continue
        mission_id = str(mission.get("mission_id", "")).strip()
        if not mission_id:
            continue
        _emit(f"mission_autorun_id={mission_id}")
        return cmd_mission_run(
            mission_id=mission_id,
            max_results=max_results,
            max_pages=max_pages,
            auto_ingest=True,
        )
    return 0


# Mobile API watchdog restart


def _restart_mobile_api(service_name: str) -> None:
    """Watchdog callback: restart mobile_api if it crashed.

    Only handles ``mobile_api`` — daemon restart is circular and widget is
    optional, so those are intentionally ignored.
    """
    import sys

    if service_name != "mobile_api":
        return
    root = repo_root()
    config_path = root / ".planning" / "security" / "mobile_api.json"
    if not config_path.exists():
        logger.warning("Watchdog: cannot restart mobile_api — config file missing: %s", config_path)
        return
    python = sys.executable
    engine_src = str(root / "engine" / "src")
    cmd = [
        python, "-m", "jarvis_engine.main", "serve-mobile",
        "--host", "127.0.0.1", "--port", str(DEFAULT_API_PORT),
        "--config-file", str(config_path),
    ]
    env = os.environ.copy()
    # Ensure engine source is on PYTHONPATH
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = engine_src + (os.pathsep + existing_pp if existing_pp else "")
    try:
        if sys.platform == "win32":
            # Detach from parent console so it survives daemon restarts
            subprocess.Popen(
                cmd,
                env=env,
                cwd=str(root / "engine"),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                cmd,
                env=env,
                cwd=str(root / "engine"),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        logger.info("Watchdog: restarted mobile_api via subprocess.")
        _emit("watchdog_restart_mobile_api=ok")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Watchdog: failed to restart mobile_api: %s", exc)
        _emit(f"watchdog_restart_mobile_api_error={exc}")


# Extracted subsystem helpers — each encapsulates one daemon responsibility


def _register_daemon_pid(root: Path) -> bool:
    """Register daemon PID file.  Returns True if registration succeeded."""
    from jarvis_engine.ops.process_manager import is_service_running, write_pid_file

    if is_service_running("daemon", root):
        print("error: daemon is already running")
        return False
    write_pid_file("daemon", root)
    return True


def _safe_log_activity(category: str, message: str, metadata: dict) -> None:
    """Log to activity feed, suppressing all errors (lazy import)."""
    try:
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        cat = getattr(ActivityCategory, category, ActivityCategory.DAEMON_CYCLE)
        log_activity(cat, message, metadata)
    except SUBSYSTEM_ERRORS_DB as exc:
        logger.debug("Activity feed log failed: %s", exc)


def _log_cycle_start(cycles: int, cycle_start_ts: str) -> None:
    """Log daemon cycle start to activity feed (never raises)."""
    _safe_log_activity(
        "DAEMON_CYCLE",
        f"Daemon cycle {cycles} started",
        {"cycle": cycles, "ts": cycle_start_ts, "phase": "start"},
    )


def _log_cycle_end(cycles: int, rc: int) -> None:
    """Log daemon cycle end to activity feed (never raises)."""
    _safe_log_activity(
        "DAEMON_CYCLE",
        f"Daemon cycle {cycles} ended (rc={rc})",
        {"cycle": cycles, "rc": rc, "phase": "end"},
    )


def _print_cycle_status(
    cycles: int,
    cycle_start_ts: str,
    state: CycleState | dict,
) -> None:
    """Print all per-cycle status lines to stdout."""
    _emit(f"cycle={cycles} ts={cycle_start_ts}")
    _emit(f"daemon_paused={state['daemon_paused']}")
    _emit(f"safe_mode={state['safe_mode']}")
    _emit(f"gaming_mode={state['gaming_mode_enabled']}")
    _emit(f"gaming_mode_auto_detect={state['auto_detect']}")
    if state["detected_process"]:
        _emit(f"gaming_mode_detected_process={state['detected_process']}")
    if state["gaming_state"].get("reason", ""):
        _emit(f"gaming_mode_reason={state['gaming_state'].get('reason', '')}")
    if state["control_state"].get("reason", ""):
        _emit(f"runtime_control_reason={state['control_state'].get('reason', '')}")
    _emit(f"device_active={state['is_active']}")
    _emit(f"resource_pressure_level={state['pressure_level']}")
    try:
        _m = state["resource_snapshot"].get("metrics", {})
        _rss = _m.get("process_memory_mb", {}).get("current", 0.0)
        _cpu = _m.get("process_cpu_pct", {}).get("current", 0.0)
        _emb = _m.get("embedding_cache_mb", {}).get("current", 0.0)
        _emit(f"resource_process_memory_mb={_rss}")
        _emit(f"resource_process_cpu_pct={_cpu}")
        _emit(f"resource_embedding_cache_mb={_emb}")
    except (AttributeError, KeyError, TypeError) as exc:
        logger.debug("Resource metric print failed: %s", exc)
    if state["pressure_level"] in {"mild", "severe"}:
        _emit(f"resource_throttle_sleep_s={state['sleep_seconds']}")
        if state["skip_heavy_tasks"]:
            _emit("resource_skip_heavy_tasks=true")
    if state["idle_seconds"] is not None:
        _emit(f"idle_seconds={round(state['idle_seconds'], 1)}")


def _log_resource_pressure(
    cycles: int,
    pressure_level: str,
    last_pressure_level: str,
    resource_snapshot: dict,
    sleep_seconds: int,
    skip_heavy_tasks: bool,
) -> None:
    """Log resource pressure changes to activity feed (never raises)."""
    if pressure_level != "none" and (pressure_level != last_pressure_level or cycles % 5 == 0):
        _safe_log_activity(
            "RESOURCE_PRESSURE",
            f"Resource pressure {pressure_level}",
            {
                "pressure_level": pressure_level,
                "cycle": cycles,
                "correlation_id": f"daemon-cycle-{cycles}",
                "metrics": resource_snapshot.get("metrics", {}),
                "sleep_s": sleep_seconds,
                "skip_heavy_tasks": skip_heavy_tasks,
            },
        )
    elif pressure_level == "none" and last_pressure_level != "none":
        _safe_log_activity(
            "RESOURCE_PRESSURE",
            "Resource pressure recovered",
            {
                "pressure_level": "none",
                "cycle": cycles,
                "correlation_id": f"daemon-cycle-{cycles}",
                "sleep_s": sleep_seconds,
                "skip_heavy_tasks": skip_heavy_tasks,
            },
        )


def _run_missions_cycle(root: Path, cycles: int, skip_heavy_tasks: bool) -> None:
    """Run pending missions and auto-generate new ones (never raises)."""
    global _mission_backoff_until_cycle  # mutable counter: tracks backoff across cycles
    # Skip if in failure backoff cooldown
    if cycles < _mission_backoff_until_cycle:
        _emit(f"mission_cycle_skipped=backoff_until_cycle_{_mission_backoff_until_cycle}")
        return
    try:
        mission_rc = _run_next_pending_mission()
    except SUBSYSTEM_ERRORS_DB as exc:
        mission_rc = 2
        _emit_cycle_failure("mission_cycle", exc, message="Daemon mission cycle failed")
        _mission_backoff_until_cycle = cycles + _MISSION_BACKOFF_CYCLES
        _emit(f"mission_backoff_set=until_cycle_{_mission_backoff_until_cycle}")
    else:
        _emit(f"mission_cycle_rc={mission_rc}")
    # Auto-generate new missions when queue is empty (every 50 cycles)
    if cycles % 50 == 0:
        if skip_heavy_tasks:
            _emit("mission_autogen_skipped=resource_pressure")
        else:
            try:
                from jarvis_engine.learning_missions import (
                    auto_generate_missions,
                    retry_failed_missions,
                )

                # First, retry any failed missions
                retried = retry_failed_missions(root)
                if retried:
                    _emit(f"mission_retried={retried}")
                # Then auto-generate if still no pending
                generated = auto_generate_missions(root, max_new=3)
                if generated:
                    topics = ", ".join(m.get("topic", "") for m in generated)
                    _emit(f"mission_auto_generated={len(generated)} topics=[{topics}]")
            except SUBSYSTEM_ERRORS_DB as exc:
                _emit_cycle_failure("mission_autogen", exc, message="Daemon mission auto-generation failed")


def _run_sync_cycle(cmd_mobile_desktop_sync) -> None:
    """Run mobile-desktop sync (never raises)."""
    try:
        sync_rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
    except SUBSYSTEM_ERRORS_DB as exc:
        sync_rc = 2
        _emit_cycle_failure("sync_cycle", exc, message="Daemon sync cycle failed")
    else:
        _emit(f"sync_cycle_rc={sync_rc}")


def _run_watchdog_cycle(root: Path) -> None:
    """Check if mobile_api crashed and restart it (never raises)."""
    try:
        from jarvis_engine.ops.process_manager import check_and_restart_services

        dead = check_and_restart_services(root, restart_callback=_restart_mobile_api)
        if dead:
            _emit(f"watchdog_dead_services={','.join(dead)}")
    except (ImportError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        _emit_cycle_failure("watchdog", exc, message="Daemon watchdog check failed")


def _run_self_heal_cycle(root: Path, cmd_self_heal) -> None:
    """Run self-heal and collect KG metrics (never raises)."""
    try:
        heal_rc = cmd_self_heal(
            force_maintenance=False,
            keep_recent=1800,
            snapshot_note="daemon-self-heal",
            as_json=False,
        )
    except SUBSYSTEM_ERRORS_DB as exc:
        heal_rc = 2
        _emit_cycle_failure("self_heal_cycle", exc, message="Daemon self-heal cycle failed")
    else:
        _emit(f"self_heal_cycle_rc={heal_rc}")
    # Collect KG growth metrics alongside self-heal
    _collect_kg_metrics(root)


def _collect_kg_metrics(root: Path) -> None:
    """Collect and append KG growth metrics (never raises).

    Prefers the daemon bus's existing KG connection (``bus.ctx.kg``) to
    avoid opening a redundant raw ``sqlite3.connect()`` each cycle.
    Falls back to a temporary connection only when the bus KG is
    unavailable.
    """
    try:
        from jarvis_engine.proactive.kg_metrics import collect_kg_metrics, append_kg_metrics

        kg = None
        try:
            bus = _get_daemon_bus()
            kg = getattr(bus.ctx, "kg", None)
        except (RuntimeError, AttributeError, ValueError, OSError) as exc:
            logger.debug("KG metrics: bus not available yet: %s", exc)
            kg = None

        if kg is not None:
            metrics = collect_kg_metrics(kg)
        else:
            # Fallback: open a temporary connection when bus KG is unavailable
            db_path = memory_db_path(root)
            if db_path.exists():
                from jarvis_engine._db_pragmas import connect_db

                _kg_conn = connect_db(db_path)
                try:
                    class _KGShim:
                        def __init__(self, conn: sqlite3.Connection) -> None:
                            self.db = conn

                    metrics = collect_kg_metrics(_KGShim(_kg_conn))
                finally:
                    _kg_conn.close()
            else:
                metrics = {"node_count": 0, "edge_count": 0}
        history_path = runtime_dir(root) / KG_METRICS_LOG
        append_kg_metrics(metrics, history_path)
        _emit(f"kg_metrics_nodes={metrics.get('node_count', 0)} edges={metrics.get('edge_count', 0)}")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("kg_metrics", exc, message="Daemon KG metrics collection failed")


def _run_self_test_cycle(root: Path) -> None:
    """Run adversarial self-test: memory quiz + regression detection (never raises)."""
    try:
        from jarvis_engine.proactive.self_test import AdversarialSelfTest

        bus = _get_daemon_bus()
        engine = bus.ctx.engine
        embed_svc = bus.ctx.embed_service
        if engine is not None and embed_svc is not None:
            tester = AdversarialSelfTest(engine, embed_svc, score_threshold=0.5)
            quiz_result = tester.run_memory_quiz()
            quiz_history = runtime_dir(root) / SELF_TEST_HISTORY
            tester.save_quiz_result(quiz_result, quiz_history)
            regression = tester.check_regression(quiz_history)
            _emit(f"self_test_score={quiz_result.get('average_score', 0.0):.4f}")
            _emit(f"self_test_tasks={quiz_result.get('tasks_run', 0)}")
            if regression.get("regression_detected"):
                _emit(f"self_test_regression=true drop_pct={regression.get('drop_pct', 0.0)}")
        else:
            _emit("self_test_skipped=engine_not_initialized")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("self_test", exc, message="Daemon self-test failed")


def _run_db_optimize_cycle(cycles: int) -> None:
    """Run SQLite ANALYZE (every 100 cycles) and VACUUM (every 500) (never raises)."""
    try:
        bus = _get_daemon_bus()
        engine = bus.ctx.engine
        if engine is not None:
            do_vacuum = (cycles % 500 == 0)
            opt_result = engine.optimize(vacuum=do_vacuum)
            _emit(f"db_optimize_analyzed={opt_result.get('analyzed', False)}")
            if do_vacuum:
                _emit(f"db_optimize_vacuumed={opt_result.get('vacuumed', False)}")
            if opt_result.get("errors"):
                _emit(f"db_optimize_errors={len(opt_result['errors'])}")
        else:
            _emit("db_optimize_skipped=engine_not_initialized")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("db_optimize", exc, message="Daemon DB optimize failed")


def _run_kg_regression_cycle(root: Path) -> None:
    """Run KG regression check with auto-restore on failure (never raises)."""
    try:
        from jarvis_engine.knowledge.regression import RegressionChecker

        bus = _get_daemon_bus()
        kg = bus.ctx.kg
        if kg is not None:
            rc_checker = RegressionChecker(kg)
            current_metrics = rc_checker.capture_metrics()
            global _daemon_kg_prev_metrics  # mutable state: previous metrics for delta comparison
            with _daemon_kg_prev_metrics_lock:
                prev_metrics = _daemon_kg_prev_metrics
            comparison = rc_checker.compare(prev_metrics, current_metrics)
            with _daemon_kg_prev_metrics_lock:
                _daemon_kg_prev_metrics = current_metrics
            _emit(f"kg_regression_status={comparison.get('status', 'unknown')}")
            if comparison.get("status") in ("fail", "warn"):
                discrepancies = comparison.get("discrepancies", [])
                _emit(f"kg_regression_discrepancies={len(discrepancies)}")
                _safe_log_activity(
                    "REGRESSION_CHECK",
                    f"KG regression detected: {comparison['status']}",
                    {"status": comparison["status"], "discrepancies": discrepancies},
                )
                # Auto-restore from backup on failure
                if comparison["status"] == "fail":
                    backup_dir = runtime_dir(root) / "kg_backups"
                    if backup_dir.exists():
                        backups = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime)
                        if backups:
                            restored = rc_checker.restore_graph(backups[-1])
                            _emit(f"kg_regression_auto_restore={'ok' if restored else 'failed'}")
                            _safe_log_activity(
                                "REGRESSION_CHECK",
                                f"KG auto-restore {'succeeded' if restored else 'failed'}",
                                {"backup": str(backups[-1]), "restored": restored},
                            )
        else:
            _emit("kg_regression_skipped=kg_not_initialized")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("kg_regression", exc, message="Daemon KG regression check failed")


def _run_usage_prediction_cycle() -> None:
    """Run usage pattern prediction (never raises)."""
    try:
        bus = _get_daemon_bus()
        usage_tracker = bus.ctx.usage_tracker
        if usage_tracker is not None:
            from datetime import datetime

            _now = datetime.now(UTC)
            prediction = usage_tracker.predict_context(_now.hour, _now.weekday())
            if prediction["interaction_count"] > 0:
                _emit(f"usage_predicted_route={prediction['likely_route']}")
                if prediction["common_topics"]:
                    _emit(f"usage_predicted_topics={','.join(prediction['common_topics'][:3])}")
                _emit(f"usage_interaction_count={prediction['interaction_count']}")
    except SUBSYSTEM_ERRORS as exc:
        _emit_cycle_failure("usage_prediction", exc, message="Daemon usage prediction failed")


def _run_memory_consolidation_cycle() -> None:
    """Run memory consolidation (never raises)."""
    try:
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        bus = _get_daemon_bus()
        result = bus.dispatch(ConsolidateMemoryCommand())
        _emit(f"consolidation_groups={result.groups_found}")
        _emit(f"consolidation_new_facts={result.new_facts_created}")
        if result.errors:
            _emit(f"consolidation_errors={len(result.errors)}")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("consolidation", exc, message="Daemon memory consolidation failed")


def _run_entity_resolution_cycle() -> None:
    """Run entity resolution with KG backup (never raises)."""
    try:
        from jarvis_engine.knowledge.entity_resolver import EntityResolver
        from jarvis_engine.knowledge.regression import RegressionChecker

        bus = _get_daemon_bus()
        kg = bus.ctx.kg
        embed_svc = bus.ctx.embed_service
        if kg is not None:
            try:
                rc_checker = RegressionChecker(kg)
                rc_checker.backup_graph(tag="pre-entity-resolve")
                _emit("entity_resolve_kg_backup=ok")
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                _emit_cycle_failure(
                    "entity_resolve_kg_backup",
                    exc,
                    message="Daemon entity resolve KG backup failed",
                )
            resolver = EntityResolver(kg, embed_service=embed_svc)
            resolve_result = resolver.auto_resolve()
            _emit(f"entity_resolve_candidates={resolve_result.candidates_found}")
            _emit(f"entity_resolve_merges={resolve_result.merges_applied}")
            if resolve_result.errors:
                _emit(f"entity_resolve_errors={len(resolve_result.errors)}")
            _safe_log_activity(
                "CONSOLIDATION",
                f"Entity resolution: {resolve_result.merges_applied} merges from {resolve_result.candidates_found} candidates",
                {
                    "candidates_found": resolve_result.candidates_found,
                    "merges_applied": resolve_result.merges_applied,
                    "errors": resolve_result.errors,
                },
            )
        else:
            _emit("entity_resolve_skipped=kg_not_initialized")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("entity_resolve", exc, message="Daemon entity resolution failed")


def _run_auto_harvest_cycle(root: Path) -> None:
    """Run autonomous knowledge harvesting (never raises)."""
    try:
        from jarvis_engine.harvesting.harvester import KnowledgeHarvester, HarvestCommand
        from jarvis_engine.harvesting.providers import (
            GeminiProvider,
            KimiNvidiaProvider,
            KimiProvider,
            MiniMaxProvider,
        )
        from jarvis_engine.harvesting.budget import BudgetManager

        harvest_topics = discover_harvest_topics(root)
        if harvest_topics:
            # Build harvester with ingest pipeline so results are stored
            harvest_db_path = memory_db_path(root)
            h_budget = None
            if harvest_db_path.exists():
                h_budget = BudgetManager(harvest_db_path)
            try:
                h_providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]
                h_available = [p for p in h_providers if p.is_available]
                # Get pipeline components from daemon bus
                h_bus = _get_daemon_bus()
                h_engine = h_bus.ctx.engine
                h_embed = h_bus.ctx.embed_service
                h_kg = h_bus.ctx.kg
                h_pipeline = None
                if h_engine is not None and h_embed is not None:
                    try:
                        from jarvis_engine.memory.classify import BranchClassifier
                        from jarvis_engine.memory.ingest import EnrichedIngestPipeline

                        h_classifier = BranchClassifier(h_embed)
                        h_pipeline = EnrichedIngestPipeline(
                            h_engine, h_embed, h_classifier, knowledge_graph=h_kg,
                        )
                    except (ImportError, OSError, sqlite3.Error) as exc_pipe:
                        logger.debug("Auto-harvest pipeline init failed: %s", exc_pipe)
                if h_available and h_pipeline is not None:
                    harvester = KnowledgeHarvester(
                        providers=h_available,
                        pipeline=h_pipeline,
                        cost_tracker=None,
                        budget_manager=h_budget,
                    )
                    total_records = 0
                    for topic in harvest_topics:
                        topic_records = 0
                        h_result = harvester.harvest(HarvestCommand(topic=topic, max_tokens=1024))
                        for entry in h_result.get("results", []):
                            topic_records += entry.get("records_created", 0)
                        total_records += topic_records
                        _emit(f"auto_harvest_topic={topic} records={topic_records}")
                    _safe_log_activity(
                        "HARVEST",
                        f"Auto-harvest: {len(harvest_topics)} topics, {total_records} records",
                        {"topics": harvest_topics, "total_records": total_records},
                    )
                elif not h_available:
                    _emit("auto_harvest_skipped=no_providers_available")
                else:
                    _emit("auto_harvest_skipped=no_ingest_pipeline")
            finally:
                if h_budget is not None:
                    h_budget.close()
        else:
            _emit("auto_harvest_skipped=no_topics_discovered")
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("auto_harvest", exc, message="Daemon auto-harvest failed")


# Main daemon loop implementation


def _gather_cycle_state(
    root: Path,
    active_interval: int,
    idle_interval: int,
    idle_after: int,
) -> CycleState:
    """Gather all per-cycle state: resource pressure, gaming mode, control state.

    Returns a :class:`CycleState` used by the main loop to decide whether to
    skip the cycle and how long to sleep.
    """
    idle_seconds = _windows_idle_seconds()
    is_active = True if idle_seconds is None else idle_seconds < idle_after
    sleep_seconds = active_interval if is_active else idle_interval

    resource_snapshot = capture_runtime_resource_snapshot(root)
    write_resource_pressure_state(root, resource_snapshot)
    throttle = recommend_daemon_sleep(sleep_seconds, resource_snapshot)
    sleep_seconds = int(throttle.get("sleep_s", sleep_seconds))
    pressure_level = str(throttle.get("pressure_level", "none"))
    skip_heavy_tasks = bool(throttle.get("skip_heavy_tasks", False))

    gaming_state = read_gaming_mode_state()
    control_state = read_control_state(repo_root())
    auto_detect = bool(gaming_state.get("auto_detect", False))
    auto_detect_hit = False
    detected_process = ""
    if auto_detect:
        auto_detect_hit, detected_process = detect_active_game_process()

    return CycleState(
        idle_seconds=idle_seconds,
        is_active=is_active,
        sleep_seconds=sleep_seconds,
        resource_snapshot=resource_snapshot,
        pressure_level=pressure_level,
        skip_heavy_tasks=skip_heavy_tasks,
        gaming_state=gaming_state,
        control_state=control_state,
        auto_detect=auto_detect,
        detected_process=detected_process,
        gaming_mode_enabled=bool(gaming_state.get("enabled", False)) or auto_detect_hit,
        daemon_paused=bool(control_state.get("daemon_paused", False)),
        safe_mode=bool(control_state.get("safe_mode", False)),
    )


@dataclass(frozen=True)
class _SubsystemEntry:
    """Descriptor for a periodic daemon subsystem."""

    name: str
    run: Any  # Callable[[], None] — bound at schedule-build time
    is_due: Any  # Callable[[int], bool] — True when cycle should fire
    heavy: bool = False  # If True, skipped under resource pressure


def _run_periodic_subsystems(
    root: Path,
    cycles: int,
    skip_heavy_tasks: bool,
    cfg: DaemonConfig,
    cmd_mobile_desktop_sync,
    cmd_self_heal,
) -> None:
    """Run all non-core periodic subsystems for the current cycle.

    Subsystems are declared as ``_SubsystemEntry`` descriptors so adding or
    reordering them is a one-line change.  Failures are logged but never
    affect the circuit breaker.
    """
    schedule: list[_SubsystemEntry] = [
        _SubsystemEntry(
            "missions", lambda: _run_missions_cycle(root, cycles, skip_heavy_tasks),
            lambda c: cfg.run_missions,
        ),
        _SubsystemEntry(
            "sync", lambda: _run_sync_cycle(cmd_mobile_desktop_sync),
            lambda c: cfg.sync_every_cycles > 0 and (c == 1 or c % cfg.sync_every_cycles == 0),
        ),
        _SubsystemEntry(
            "watchdog", lambda: _run_watchdog_cycle(root),
            lambda c: cfg.watchdog_every_cycles > 0 and c % cfg.watchdog_every_cycles == 0,
        ),
        _SubsystemEntry(
            "self_heal_cycle", lambda: _run_self_heal_cycle(root, cmd_self_heal),
            lambda c: cfg.self_heal_every_cycles > 0 and (c == 2 or c % cfg.self_heal_every_cycles == 0),
            heavy=True,
        ),
        _SubsystemEntry(
            "self_test", lambda: _run_self_test_cycle(root),
            lambda c: cfg.self_test_every_cycles > 0 and c % cfg.self_test_every_cycles == 0,
            heavy=True,
        ),
        _SubsystemEntry(
            "db_optimize", lambda: _run_db_optimize_cycle(cycles),
            lambda c: c % 100 == 0,
            heavy=True,
        ),
        _SubsystemEntry(
            "kg_regression", lambda: _run_kg_regression_cycle(root),
            lambda c: c % 10 == 0,
        ),
        _SubsystemEntry(
            "usage_prediction", lambda: _run_usage_prediction_cycle(),
            lambda c: c % 10 == 0,
        ),
        _SubsystemEntry(
            "consolidation", lambda: _run_memory_consolidation_cycle(),
            lambda c: c % 50 == 0,
            heavy=True,
        ),
        _SubsystemEntry(
            "entity_resolve", lambda: _run_entity_resolution_cycle(),
            lambda c: c % 100 == 0,
            heavy=True,
        ),
        _SubsystemEntry(
            "auto_harvest", lambda: _run_auto_harvest_cycle(root),
            lambda c: c % 200 == 0,
            heavy=True,
        ),
        _SubsystemEntry(
            "diagnostic_scan", lambda: _run_diagnostic_scan_cycle(root),
            lambda c: c % 50 == 0,
        ),
    ]

    for entry in schedule:
        if not entry.is_due(cycles):
            continue
        if entry.heavy and skip_heavy_tasks:
            logger.debug("Skipping heavy subsystem %s due to resource pressure", entry.name)
            _emit(f"{entry.name}_skipped=resource_pressure")
            continue
        entry.run()


def _run_diagnostic_scan_cycle(root: Path) -> None:
    """Run a quick diagnostic scan and persist results to JSONL history."""
    try:
        from jarvis_engine.self_diagnosis import DiagnosticEngine

        diag = DiagnosticEngine(root)
        issues = diag.run_quick_scan()
        score = diag.health_score(issues)
        _emit(f"diagnostic_scan_score={score} issues={len(issues)}")

        # Persist to diagnostics_history.jsonl
        history_path = runtime_dir(root) / "diagnostics_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": now_iso(),
            "score": score,
            "issue_count": len(issues),
            "issues": [i.to_dict() for i in issues],
        }
        try:
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.debug("Failed to write diagnostics history: %s", exc)
    except SUBSYSTEM_ERRORS as exc:
        logger.debug("Diagnostic scan cycle failed: %s", exc)


def _run_core_autopilot(
    snapshot_path: Path,
    actions_path: Path,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    safe_mode: bool,
    cmd_ops_autopilot,
) -> int:
    """Run the core ops-autopilot cycle. Returns the autopilot return code."""
    exec_cycle = execute and not safe_mode
    approve_cycle = approve_privileged and not safe_mode
    if safe_mode and (execute or approve_privileged):
        _emit("safe_mode_override=execute_and_privileged_flags_forced_false")
    try:
        return cmd_ops_autopilot(
            snapshot_path=snapshot_path,
            actions_path=actions_path,
            execute=exec_cycle,
            approve_privileged=approve_cycle,
            auto_open_connectors=auto_open_connectors,
        )
    except SUBSYSTEM_ERRORS_DB as exc:
        _emit_cycle_failure("cycle", exc, message="Daemon autopilot cycle failed")
        return 2


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 1-second chunks so KeyboardInterrupt is handled promptly."""
    remaining = float(seconds)
    while remaining > 0:
        chunk = min(1.0, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _handle_circuit_breaker(rc: int, consecutive_failures: int) -> int:
    """Update and act on the circuit breaker. Returns updated failure count."""
    max_consecutive_failures = 10
    if rc == 0:
        return 0
    consecutive_failures += 1
    _emit(f"consecutive_failures={consecutive_failures}")
    if consecutive_failures >= max_consecutive_failures:
        _emit("daemon_circuit_breaker_open=true cooldown=300s")
        _interruptible_sleep(300)  # 5-minute cooldown instead of exit
        return 0  # Reset counter after cooldown
    return consecutive_failures


def _emit_cycle_status(
    cycles: int,
    state: CycleState | dict,
    last_pressure_level: str,
) -> None:
    """Log and print all per-cycle status and resource pressure info."""
    cycle_start_ts = now_iso()
    _log_cycle_start(cycles, cycle_start_ts)
    _print_cycle_status(cycles, cycle_start_ts, state)
    _log_resource_pressure(
        cycles, state["pressure_level"], last_pressure_level,
        state["resource_snapshot"], state["sleep_seconds"],
        state["skip_heavy_tasks"],
    )


def _should_skip_cycle(state: CycleState | dict, idle_interval: int) -> str | None:
    """Check if the cycle should be skipped.

    Returns a skip-reason string to print, or ``None`` when the cycle
    should proceed normally.  When skipped the caller should sleep for
    ``max(idle_interval, 600)`` seconds.
    """
    if state["daemon_paused"]:
        return "cycle_skipped=runtime_control_daemon_paused"
    if state["gaming_mode_enabled"]:
        return "cycle_skipped=gaming_mode_enabled"
    return None


def cmd_daemon_run_impl(cfg: DaemonConfig) -> int:
    """Implementation body for daemon-run (called by handler via callback)."""
    from jarvis_engine.cli_system import cmd_mobile_desktop_sync, cmd_self_heal
    from jarvis_engine.cli_ops import cmd_ops_autopilot

    set_process_title("jarvis-daemon")
    root = repo_root()
    from jarvis_engine.ops.process_manager import remove_pid_file

    if not _register_daemon_pid(root):
        return 4

    active_interval = max(30, cfg.interval_s)
    idle_interval = max(30, cfg.idle_interval_s)
    idle_after = max(60, cfg.idle_after_s)
    consecutive_failures = 0
    cycles = 0
    last_pressure_level = "none"
    # Initialize conversation state singleton for cross-LLM continuity.
    # get_conversation_state() creates the manager which auto-loads from disk.
    try:
        from jarvis_engine.conversation_state import get_conversation_state

        get_conversation_state()
        logger.debug("Conversation state initialized on daemon startup")
    except SUBSYSTEM_ERRORS as exc:
        logger.debug("Conversation state init on daemon startup failed: %s", exc)

    # Warm-start STT backends in a background thread to eliminate cold-start
    # latency when the user first issues a voice command.
    try:
        from jarvis_engine.stt import warmup_stt_backends

        threading.Thread(target=warmup_stt_backends, daemon=True).start()
        logger.debug("STT backend warmup started in background thread")
    except (ImportError, OSError) as exc:
        logger.debug("STT backend warmup launch failed: %s", exc)

    _emit("jarvis_daemon_started=true")
    _emit(f"active_interval_s={active_interval}")
    _emit(f"idle_interval_s={idle_interval}")
    _emit(f"idle_after_s={idle_after}")
    try:
        global _cycle_start  # noqa: PLW0603  -- mutable timestamp read by watchdog from another thread
        while True:
            cycles += 1
            _cycle_start = time.monotonic()
            state = _gather_cycle_state(root, active_interval, idle_interval, idle_after)
            _emit_cycle_status(cycles, state, last_pressure_level)
            last_pressure_level = state["pressure_level"]

            skip_reason = _should_skip_cycle(state, idle_interval)
            if skip_reason:
                _emit(skip_reason)
                if cfg.max_cycles > 0 and cycles >= cfg.max_cycles:
                    break
                _interruptible_sleep(max(idle_interval, 600))
                continue

            _run_periodic_subsystems(
                root, cycles, state["skip_heavy_tasks"], cfg,
                cmd_mobile_desktop_sync, cmd_self_heal,
            )

            rc = _run_core_autopilot(
                cfg.snapshot_path, cfg.actions_path, cfg.execute,
                cfg.approve_privileged, cfg.auto_open_connectors,
                state["safe_mode"], cmd_ops_autopilot,
            )
            _emit(f"cycle_rc={rc}")
            _log_cycle_end(cycles, rc)
            consecutive_failures = _handle_circuit_breaker(rc, consecutive_failures)

            # Watchdog: warn if cycle exceeded timeout
            cycle_elapsed = time.monotonic() - _cycle_start
            if cycle_elapsed > _CYCLE_TIMEOUT_S:
                logger.warning(
                    "Daemon cycle %d exceeded timeout: %.1fs > %ds",
                    cycles, cycle_elapsed, _CYCLE_TIMEOUT_S,
                )
                _emit(f"cycle_timeout_warning={cycle_elapsed:.1f}s")

            if cfg.max_cycles > 0 and cycles >= cfg.max_cycles:
                break
            _emit(f"sleep_s={state['sleep_seconds']}")
            _interruptible_sleep(state["sleep_seconds"])
    except KeyboardInterrupt:
        _emit("jarvis_daemon_stopped=true")
    finally:
        # Persist conversation state before shutdown
        try:
            from jarvis_engine.conversation_state import get_conversation_state

            _csm_shutdown = get_conversation_state()
            _csm_shutdown.save()
            logger.debug("Conversation state saved on daemon shutdown")
        except SUBSYSTEM_ERRORS as exc:
            logger.debug("Conversation state save on shutdown failed: %s", exc)
        remove_pid_file("daemon", root)
    return 0

