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
from datetime import datetime
from pathlib import Path

from jarvis_engine._bus import get_bus
from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._constants import (
    DEFAULT_API_PORT as _DEFAULT_API_PORT,
    KG_METRICS_LOG as _KG_METRICS_LOG,
    SELF_TEST_HISTORY as _SELF_TEST_HISTORY,
)
from jarvis_engine._shared import (
    memory_db_path as _memory_db_path,
    runtime_dir as _runtime_dir,
)
from jarvis_engine._shared import set_process_title as _set_process_title
from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.ops_commands import MissionRunCommand
from jarvis_engine.config import repo_root
from jarvis_engine.learning_missions import load_missions
from jarvis_engine.runtime_control import (
    capture_runtime_resource_snapshot,
    read_control_state,
    recommend_daemon_sleep,
    write_resource_pressure_state,
)

# Internal import — _windows_idle_seconds used by _gather_cycle_state
from jarvis_engine.gaming_mode import _windows_idle_seconds
from jarvis_engine.gaming_mode import (
    GamingModeState,
    detect_active_game_process as _gm_detect_active_game_process,
    load_gaming_processes as _gm_load_gaming_processes,
    read_gaming_mode_state as _gm_read_gaming_mode_state,
    write_gaming_mode_state as _gm_write_gaming_mode_state,
)

# Harvest discovery — all topic-discovery logic lives in harvest_discovery.py.
# Re-imported here so existing callers and tests using daemon_loop_mod.X still work.
from jarvis_engine.harvest_discovery import (  # noqa: F401
    _SQL_NODE_BY_RELATION,
    _SQL_RARE_RELATIONS,
    _SQL_RECENT_SUMMARIES,
    _SQL_SPARSE_NODES,
    _SQL_STRONG_LABELS,
    _add_phrases,
    _collect_from_activity_feed,
    _collect_from_kg_gaps,
    _collect_from_learning_missions,
    _collect_from_recent_memories,
    _collect_from_strong_kg_areas,
    _extract_topic_phrases,
    _get_recently_harvested_topics,
    _try_add_candidate,
    discover_harvest_topics as _discover_harvest_topics,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter bundle dataclasses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Daemon-scoped bus cache (avoids recreating MemoryEngine per periodic task)
# ---------------------------------------------------------------------------
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
    global _daemon_bus
    with _daemon_bus_lock:
        if _daemon_bus is None:
            _daemon_bus = get_bus()
        return _daemon_bus


# ---------------------------------------------------------------------------
# Daemon cycle state for KG regression tracking
# ---------------------------------------------------------------------------
_daemon_kg_prev_metrics: dict | None = None
_daemon_kg_prev_metrics_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Gaming mode helpers — thin wrappers around gaming_mode.py
# ---------------------------------------------------------------------------
# Tests patch ``daemon_loop_mod.repo_root``, so these wrappers call
# ``repo_root()`` from *this* module's namespace (patchable) and forward the
# resulting paths to the parameterised functions in ``gaming_mode.py``.


def gaming_mode_state_path() -> Path:
    """Return the path to the gaming mode JSON state file."""
    return _runtime_dir(repo_root()) / "gaming_mode.json"


def gaming_processes_path() -> Path:
    """Return the path to the gaming processes JSON config file."""
    return repo_root() / ".planning" / "gaming_processes.json"


def read_gaming_mode_state() -> GamingModeState:
    """Read gaming mode state using the daemon's repo_root."""
    return _gm_read_gaming_mode_state(gaming_mode_state_path())


def write_gaming_mode_state(state: dict[str, object]) -> GamingModeState:
    """Write gaming mode state using the daemon's repo_root."""
    return _gm_write_gaming_mode_state(state, gaming_mode_state_path())


def load_gaming_processes() -> list[str]:
    """Load gaming process list using the daemon's repo_root."""
    return _gm_load_gaming_processes(gaming_processes_path())


def detect_active_game_process() -> tuple[bool, str]:
    """Detect active game processes using loaded process list."""
    return _gm_detect_active_game_process(load_gaming_processes())


# ---------------------------------------------------------------------------
# Mission run helpers
# ---------------------------------------------------------------------------


def cmd_mission_run(mission_id: str, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    result = _get_daemon_bus().dispatch(MissionRunCommand(
        mission_id=mission_id, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: mission run failed")
        return result.return_code

    report = result.report
    print("learning_mission_completed=true")
    print(f"mission_id={report.get('mission_id', '')}")
    print(f"candidate_count={report.get('candidate_count', 0)}")
    print(f"verified_count={report.get('verified_count', 0)}")
    verified = report.get("verified_findings", [])
    if isinstance(verified, list):
        for idx, finding in enumerate(verified[:10], start=1):
            statement = str(finding.get("statement", "")) if isinstance(finding, dict) else ""
            sources = ",".join(finding.get("source_domains", [])) if isinstance(finding, dict) else ""
            print(f"verified_{idx}={statement}")
            print(f"verified_{idx}_sources={sources}")

    if result.ingested_record_id:
        print(f"mission_ingested_record_id={result.ingested_record_id}")
    return 0


def _run_next_pending_mission(*, max_results: int = 6, max_pages: int = 10) -> int:
    missions = load_missions(repo_root())
    for mission in missions:
        if str(mission.get("status", "")).lower() != "pending":
            continue
        mission_id = str(mission.get("mission_id", "")).strip()
        if not mission_id:
            continue
        print(f"mission_autorun_id={mission_id}")
        return cmd_mission_run(
            mission_id=mission_id,
            max_results=max_results,
            max_pages=max_pages,
            auto_ingest=True,
        )
    return 0


# ---------------------------------------------------------------------------
# Mobile API watchdog restart
# ---------------------------------------------------------------------------


def _restart_mobile_api(service_name: str) -> None:
    """Watchdog callback: restart mobile_api if it crashed.

    Only handles ``mobile_api`` — daemon restart is circular and widget is
    optional, so those are intentionally ignored.
    """
    import sys as _sys

    if service_name != "mobile_api":
        return
    root = repo_root()
    config_path = root / ".planning" / "security" / "mobile_api.json"
    if not config_path.exists():
        logger.warning("Watchdog: cannot restart mobile_api — config file missing: %s", config_path)
        return
    python = _sys.executable
    engine_src = str(root / "engine" / "src")
    cmd = [
        python, "-m", "jarvis_engine.main", "serve-mobile",
        "--host", "127.0.0.1", "--port", str(_DEFAULT_API_PORT),
        "--config-file", str(config_path),
    ]
    env = os.environ.copy()
    # Ensure engine source is on PYTHONPATH
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = engine_src + (os.pathsep + existing_pp if existing_pp else "")
    try:
        if _sys.platform == "win32":
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
        print("watchdog_restart_mobile_api=ok")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Watchdog: failed to restart mobile_api: %s", exc)
        print(f"watchdog_restart_mobile_api_error={exc}")


# ---------------------------------------------------------------------------
# Extracted subsystem helpers — each encapsulates one daemon responsibility
# ---------------------------------------------------------------------------


def _register_daemon_pid(root: Path) -> bool:
    """Register daemon PID file.  Returns True if registration succeeded."""
    from jarvis_engine.process_manager import is_service_running, write_pid_file

    if is_service_running("daemon", root):
        print("error: daemon is already running")
        return False
    write_pid_file("daemon", root)
    return True


def _log_cycle_start(cycles: int, cycle_start_ts: str) -> None:
    """Log daemon cycle start to activity feed (never raises)."""
    try:
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        log_activity(
            ActivityCategory.DAEMON_CYCLE,
            f"Daemon cycle {cycles} started",
            {"cycle": cycles, "ts": cycle_start_ts, "phase": "start"},
        )
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.debug("Activity feed cycle-start log failed: %s", exc)


def _log_cycle_end(cycles: int, rc: int) -> None:
    """Log daemon cycle end to activity feed (never raises)."""
    try:
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        log_activity(
            ActivityCategory.DAEMON_CYCLE,
            f"Daemon cycle {cycles} ended (rc={rc})",
            {"cycle": cycles, "rc": rc, "phase": "end"},
        )
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.debug("Activity feed cycle-end log failed: %s", exc)


def _print_cycle_status(
    cycles: int,
    cycle_start_ts: str,
    state: dict,
) -> None:
    """Print all per-cycle status lines to stdout."""
    print(f"cycle={cycles} ts={cycle_start_ts}")
    print(f"daemon_paused={state['daemon_paused']}")
    print(f"safe_mode={state['safe_mode']}")
    print(f"gaming_mode={state['gaming_mode_enabled']}")
    print(f"gaming_mode_auto_detect={state['auto_detect']}")
    if state["detected_process"]:
        print(f"gaming_mode_detected_process={state['detected_process']}")
    if state["gaming_state"].get("reason", ""):
        print(f"gaming_mode_reason={state['gaming_state'].get('reason', '')}")
    if state["control_state"].get("reason", ""):
        print(f"runtime_control_reason={state['control_state'].get('reason', '')}")
    print(f"device_active={state['is_active']}")
    print(f"resource_pressure_level={state['pressure_level']}")
    try:
        _m = state["resource_snapshot"].get("metrics", {})
        _rss = _m.get("process_memory_mb", {}).get("current", 0.0)
        _cpu = _m.get("process_cpu_pct", {}).get("current", 0.0)
        _emb = _m.get("embedding_cache_mb", {}).get("current", 0.0)
        print(f"resource_process_memory_mb={_rss}")
        print(f"resource_process_cpu_pct={_cpu}")
        print(f"resource_embedding_cache_mb={_emb}")
    except (AttributeError, KeyError, TypeError) as exc:
        logger.debug("Resource metric print failed: %s", exc)
    if state["pressure_level"] in {"mild", "severe"}:
        print(f"resource_throttle_sleep_s={state['sleep_seconds']}")
        if state["skip_heavy_tasks"]:
            print("resource_skip_heavy_tasks=true")
    if state["idle_seconds"] is not None:
        print(f"idle_seconds={round(state['idle_seconds'], 1)}")


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
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.RESOURCE_PRESSURE,
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
        except (ImportError, OSError, sqlite3.Error) as exc:
            logger.debug("Resource pressure activity log failed: %s", exc)
    elif pressure_level == "none" and last_pressure_level != "none":
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.RESOURCE_PRESSURE,
                "Resource pressure recovered",
                {
                    "pressure_level": "none",
                    "cycle": cycles,
                    "correlation_id": f"daemon-cycle-{cycles}",
                    "sleep_s": sleep_seconds,
                    "skip_heavy_tasks": skip_heavy_tasks,
                },
            )
        except (ImportError, OSError, sqlite3.Error) as exc:
            logger.debug("Resource pressure recovery log failed: %s", exc)


def _run_missions_cycle(root: Path, cycles: int, skip_heavy_tasks: bool) -> None:
    """Run pending missions and auto-generate new ones (never raises)."""
    global _mission_backoff_until_cycle
    # Skip if in failure backoff cooldown
    if cycles < _mission_backoff_until_cycle:
        print(f"mission_cycle_skipped=backoff_until_cycle_{_mission_backoff_until_cycle}")
        return
    try:
        mission_rc = _run_next_pending_mission()
    except (ImportError, OSError, sqlite3.Error, AttributeError, KeyError, ValueError, RuntimeError) as exc:
        mission_rc = 2
        logger.warning("Daemon mission cycle failed: %s", exc)
        print(f"mission_cycle_error={exc}")
        _mission_backoff_until_cycle = cycles + _MISSION_BACKOFF_CYCLES
        print(f"mission_backoff_set=until_cycle_{_mission_backoff_until_cycle}")
    else:
        print(f"mission_cycle_rc={mission_rc}")
    # Auto-generate new missions when queue is empty (every 50 cycles)
    if cycles % 50 == 0:
        if skip_heavy_tasks:
            print("mission_autogen_skipped=resource_pressure")
        else:
            try:
                from jarvis_engine.learning_missions import (
                    auto_generate_missions,
                    retry_failed_missions,
                )

                # First, retry any failed missions
                retried = retry_failed_missions(root)
                if retried:
                    print(f"mission_retried={retried}")
                # Then auto-generate if still no pending
                generated = auto_generate_missions(root, max_new=3)
                if generated:
                    topics = ", ".join(m.get("topic", "") for m in generated)
                    print(f"mission_auto_generated={len(generated)} topics=[{topics}]")
            except (ImportError, OSError, sqlite3.Error, KeyError, ValueError) as exc:
                logger.warning("Daemon mission auto-generation failed: %s", exc)
                print(f"mission_autogen_error={exc}")


def _run_sync_cycle(cmd_mobile_desktop_sync) -> None:
    """Run mobile-desktop sync (never raises)."""
    try:
        sync_rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        sync_rc = 2
        logger.warning("Daemon sync cycle failed: %s", exc)
        print(f"sync_cycle_error={exc}")
    else:
        print(f"sync_cycle_rc={sync_rc}")


def _run_watchdog_cycle(root: Path) -> None:
    """Check if mobile_api crashed and restart it (never raises)."""
    try:
        from jarvis_engine.process_manager import check_and_restart_services

        dead = check_and_restart_services(root, restart_callback=_restart_mobile_api)
        if dead:
            print(f"watchdog_dead_services={','.join(dead)}")
    except (ImportError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        logger.warning("Daemon watchdog check failed: %s", exc)
        print(f"watchdog_error={exc}")


def _run_self_heal_cycle(root: Path, cmd_self_heal) -> None:
    """Run self-heal and collect KG metrics (never raises)."""
    try:
        heal_rc = cmd_self_heal(
            force_maintenance=False,
            keep_recent=1800,
            snapshot_note="daemon-self-heal",
            as_json=False,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        heal_rc = 2
        logger.warning("Daemon self-heal cycle failed: %s", exc)
        print(f"self_heal_cycle_error={exc}")
    else:
        print(f"self_heal_cycle_rc={heal_rc}")
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
        except Exception:  # noqa: BLE001 — bus may not be initialized yet
            kg = None

        if kg is not None:
            metrics = collect_kg_metrics(kg)
        else:
            # Fallback: open a temporary connection when bus KG is unavailable
            db_path = _memory_db_path(root)
            if db_path.exists():
                from jarvis_engine._db_pragmas import connect_db as _connect_db

                _kg_conn = _connect_db(db_path)
                try:
                    class _KGShim:
                        def __init__(self, conn: _sqlite3.Connection) -> None:
                            self.db = conn

                    metrics = collect_kg_metrics(_KGShim(_kg_conn))
                finally:
                    _kg_conn.close()
            else:
                metrics = {"node_count": 0, "edge_count": 0}
        history_path = _runtime_dir(root) / _KG_METRICS_LOG
        append_kg_metrics(metrics, history_path)
        print(f"kg_metrics_nodes={metrics.get('node_count', 0)} edges={metrics.get('edge_count', 0)}")
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Daemon KG metrics collection failed: %s", exc)
        print(f"kg_metrics_error={exc}")


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
            quiz_history = _runtime_dir(root) / _SELF_TEST_HISTORY
            tester.save_quiz_result(quiz_result, quiz_history)
            regression = tester.check_regression(quiz_history)
            print(f"self_test_score={quiz_result.get('average_score', 0.0):.4f}")
            print(f"self_test_tasks={quiz_result.get('tasks_run', 0)}")
            if regression.get("regression_detected"):
                print(f"self_test_regression=true drop_pct={regression.get('drop_pct', 0.0)}")
        else:
            print("self_test_skipped=engine_not_initialized")
    except (ImportError, OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("Daemon self-test failed: %s", exc)
        print(f"self_test_error={exc}")


def _run_db_optimize_cycle(cycles: int) -> None:
    """Run SQLite ANALYZE (every 100 cycles) and VACUUM (every 500) (never raises)."""
    try:
        bus = _get_daemon_bus()
        engine = bus.ctx.engine
        if engine is not None:
            do_vacuum = (cycles % 500 == 0)
            opt_result = engine.optimize(vacuum=do_vacuum)
            print(f"db_optimize_analyzed={opt_result.get('analyzed', False)}")
            if do_vacuum:
                print(f"db_optimize_vacuumed={opt_result.get('vacuumed', False)}")
            if opt_result.get("errors"):
                print(f"db_optimize_errors={len(opt_result['errors'])}")
        else:
            print("db_optimize_skipped=engine_not_initialized")
    except (OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("Daemon DB optimize failed: %s", exc)
        print(f"db_optimize_error={exc}")


def _run_kg_regression_cycle(root: Path) -> None:
    """Run KG regression check with auto-restore on failure (never raises)."""
    try:
        from jarvis_engine.knowledge.regression import RegressionChecker
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        bus = _get_daemon_bus()
        kg = bus.ctx.kg
        if kg is not None:
            rc_checker = RegressionChecker(kg)
            current_metrics = rc_checker.capture_metrics()
            # Compare against previous snapshot stored in module state
            global _daemon_kg_prev_metrics
            with _daemon_kg_prev_metrics_lock:
                prev_metrics = _daemon_kg_prev_metrics
            comparison = rc_checker.compare(prev_metrics, current_metrics)
            with _daemon_kg_prev_metrics_lock:
                _daemon_kg_prev_metrics = current_metrics
            print(f"kg_regression_status={comparison.get('status', 'unknown')}")
            if comparison.get("status") in ("fail", "warn"):
                discrepancies = comparison.get("discrepancies", [])
                print(f"kg_regression_discrepancies={len(discrepancies)}")
                log_activity(
                    ActivityCategory.REGRESSION_CHECK,
                    f"KG regression detected: {comparison['status']}",
                    {"status": comparison["status"], "discrepancies": discrepancies},
                )
                # Auto-restore from backup on failure
                if comparison["status"] == "fail":
                    backup_dir = _runtime_dir(root) / "kg_backups"
                    if backup_dir.exists():
                        backups = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime)
                        if backups:
                            restored = rc_checker.restore_graph(backups[-1])
                            print(f"kg_regression_auto_restore={'ok' if restored else 'failed'}")
                            log_activity(
                                ActivityCategory.REGRESSION_CHECK,
                                f"KG auto-restore {'succeeded' if restored else 'failed'}",
                                {"backup": str(backups[-1]), "restored": restored},
                            )
        else:
            print("kg_regression_skipped=kg_not_initialized")
    except (ImportError, OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Daemon KG regression check failed: %s", exc)
        print(f"kg_regression_error={exc}")


def _run_usage_prediction_cycle() -> None:
    """Run usage pattern prediction (never raises)."""
    try:
        bus = _get_daemon_bus()
        usage_tracker = bus.ctx.usage_tracker
        if usage_tracker is not None:
            from datetime import datetime as _dt

            _now = _dt.now(UTC)
            prediction = usage_tracker.predict_context(_now.hour, _now.weekday())
            if prediction["interaction_count"] > 0:
                print(f"usage_predicted_route={prediction['likely_route']}")
                if prediction["common_topics"]:
                    print(f"usage_predicted_topics={','.join(prediction['common_topics'][:3])}")
                print(f"usage_interaction_count={prediction['interaction_count']}")
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        logger.warning("Daemon usage prediction failed: %s", exc)
        print(f"usage_prediction_error={exc}")


def _run_memory_consolidation_cycle() -> None:
    """Run memory consolidation (never raises)."""
    try:
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        bus = _get_daemon_bus()
        result = bus.dispatch(ConsolidateMemoryCommand())
        print(f"consolidation_groups={result.groups_found}")
        print(f"consolidation_new_facts={result.new_facts_created}")
        if result.errors:
            print(f"consolidation_errors={len(result.errors)}")
    except (ImportError, OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("Daemon memory consolidation failed: %s", exc)
        print(f"consolidation_error={exc}")


def _run_entity_resolution_cycle() -> None:
    """Run entity resolution with KG backup (never raises)."""
    try:
        from jarvis_engine.knowledge.entity_resolver import EntityResolver
        from jarvis_engine.knowledge.regression import RegressionChecker
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        bus = _get_daemon_bus()
        kg = bus.ctx.kg
        embed_svc = bus.ctx.embed_service
        if kg is not None:
            # Backup KG state before entity resolution
            try:
                rc_checker = RegressionChecker(kg)
                rc_checker.backup_graph(tag="pre-entity-resolve")
                print("entity_resolve_kg_backup=ok")
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                logger.warning("Daemon entity resolve KG backup failed: %s", exc)
                print(f"entity_resolve_kg_backup_error={exc}")
            resolver = EntityResolver(kg, embed_service=embed_svc)
            resolve_result = resolver.auto_resolve()
            print(f"entity_resolve_candidates={resolve_result.candidates_found}")
            print(f"entity_resolve_merges={resolve_result.merges_applied}")
            if resolve_result.errors:
                print(f"entity_resolve_errors={len(resolve_result.errors)}")
            log_activity(
                ActivityCategory.CONSOLIDATION,
                f"Entity resolution: {resolve_result.merges_applied} merges from {resolve_result.candidates_found} candidates",
                {
                    "candidates_found": resolve_result.candidates_found,
                    "merges_applied": resolve_result.merges_applied,
                    "errors": resolve_result.errors,
                },
            )
        else:
            print("entity_resolve_skipped=kg_not_initialized")
    except (ImportError, OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("Daemon entity resolution failed: %s", exc)
        print(f"entity_resolve_error={exc}")


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
        from jarvis_engine.activity_feed import log_activity, ActivityCategory

        harvest_topics = _discover_harvest_topics(root)
        if harvest_topics:
            # Build harvester with ingest pipeline so results are stored
            harvest_db_path = _memory_db_path(root)
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
                        print(f"auto_harvest_topic={topic} records={topic_records}")
                    log_activity(
                        ActivityCategory.HARVEST,
                        f"Auto-harvest: {len(harvest_topics)} topics, {total_records} records",
                        {"topics": harvest_topics, "total_records": total_records},
                    )
                elif not h_available:
                    print("auto_harvest_skipped=no_providers_available")
                else:
                    print("auto_harvest_skipped=no_ingest_pipeline")
            finally:
                if h_budget is not None:
                    h_budget.close()
        else:
            print("auto_harvest_skipped=no_topics_discovered")
    except (ImportError, OSError, sqlite3.Error, AttributeError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Daemon auto-harvest failed: %s", exc)
        print(f"auto_harvest_error={exc}")


# ---------------------------------------------------------------------------
# Main daemon loop implementation
# ---------------------------------------------------------------------------


def _gather_cycle_state(
    root: Path,
    active_interval: int,
    idle_interval: int,
    idle_after: int,
) -> dict:
    """Gather all per-cycle state: resource pressure, gaming mode, control state.

    Returns a dict with keys used by the main loop to decide whether to skip
    the cycle and how long to sleep.
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

    return {
        "idle_seconds": idle_seconds,
        "is_active": is_active,
        "sleep_seconds": sleep_seconds,
        "resource_snapshot": resource_snapshot,
        "pressure_level": pressure_level,
        "skip_heavy_tasks": skip_heavy_tasks,
        "gaming_state": gaming_state,
        "control_state": control_state,
        "auto_detect": auto_detect,
        "detected_process": detected_process,
        "gaming_mode_enabled": bool(gaming_state.get("enabled", False)) or auto_detect_hit,
        "daemon_paused": bool(control_state.get("daemon_paused", False)),
        "safe_mode": bool(control_state.get("safe_mode", False)),
    }


def _run_periodic_subsystems(
    root: Path,
    cycles: int,
    skip_heavy_tasks: bool,
    run_missions: bool,
    cmd_mobile_desktop_sync,
    cmd_self_heal,
    sync_every_cycles: int,
    self_heal_every_cycles: int,
    self_test_every_cycles: int,
    watchdog_every_cycles: int,
) -> None:
    """Run all non-core periodic subsystems for the current cycle.

    Failures here are logged but never affect the circuit breaker.
    """
    if run_missions:
        _run_missions_cycle(root, cycles, skip_heavy_tasks)
    if sync_every_cycles > 0 and (cycles == 1 or cycles % sync_every_cycles == 0):
        _run_sync_cycle(cmd_mobile_desktop_sync)
    if watchdog_every_cycles > 0 and cycles % watchdog_every_cycles == 0:
        _run_watchdog_cycle(root)
    if self_heal_every_cycles > 0 and (cycles == 2 or cycles % self_heal_every_cycles == 0):
        if skip_heavy_tasks:
            print("self_heal_cycle_skipped=resource_pressure")
        else:
            _run_self_heal_cycle(root, cmd_self_heal)
    if self_test_every_cycles > 0 and cycles % self_test_every_cycles == 0:
        if skip_heavy_tasks:
            print("self_test_skipped=resource_pressure")
        else:
            _run_self_test_cycle(root)
    if cycles % 100 == 0:
        if skip_heavy_tasks:
            print("db_optimize_skipped=resource_pressure")
        else:
            _run_db_optimize_cycle(cycles)
    if cycles % 10 == 0:
        _run_kg_regression_cycle(root)
    if cycles % 10 == 0:
        _run_usage_prediction_cycle()
    if cycles % 50 == 0:
        if skip_heavy_tasks:
            print("consolidation_skipped=resource_pressure")
        else:
            _run_memory_consolidation_cycle()
    if cycles % 100 == 0:
        if skip_heavy_tasks:
            print("entity_resolve_skipped=resource_pressure")
        else:
            _run_entity_resolution_cycle()
    if cycles % 200 == 0:
        if skip_heavy_tasks:
            print("auto_harvest_skipped=resource_pressure")
        else:
            _run_auto_harvest_cycle(root)
    if cycles % 50 == 0:
        _run_diagnostic_scan_cycle(root)


def _run_diagnostic_scan_cycle(root: Path) -> None:
    """Run a quick diagnostic scan and persist results to JSONL history."""
    try:
        from jarvis_engine.self_diagnosis import DiagnosticEngine

        diag = DiagnosticEngine(root)
        issues = diag.run_quick_scan()
        score = diag.health_score(issues)
        print(f"diagnostic_scan_score={score} issues={len(issues)}")

        # Persist to diagnostics_history.jsonl
        history_path = _runtime_dir(root) / "diagnostics_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now_iso(),
            "score": score,
            "issue_count": len(issues),
            "issues": [i.to_dict() for i in issues],
        }
        try:
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.debug("Failed to write diagnostics history: %s", exc)
    except (ImportError, RuntimeError, OSError, ValueError) as exc:
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
        print("safe_mode_override=execute_and_privileged_flags_forced_false")
    try:
        return cmd_ops_autopilot(
            snapshot_path=snapshot_path,
            actions_path=actions_path,
            execute=exec_cycle,
            approve_privileged=approve_cycle,
            auto_open_connectors=auto_open_connectors,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Daemon autopilot cycle failed: %s", exc)
        print(f"cycle_error={exc}")
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
    print(f"consecutive_failures={consecutive_failures}")
    if consecutive_failures >= max_consecutive_failures:
        print("daemon_circuit_breaker_open=true cooldown=300s")
        _interruptible_sleep(300)  # 5-minute cooldown instead of exit
        return 0  # Reset counter after cooldown
    return consecutive_failures


def _emit_cycle_status(
    cycles: int,
    state: dict,
    last_pressure_level: str,
) -> None:
    """Log and print all per-cycle status and resource pressure info."""
    cycle_start_ts = _now_iso()
    _log_cycle_start(cycles, cycle_start_ts)
    _print_cycle_status(cycles, cycle_start_ts, state)
    _log_resource_pressure(
        cycles, state["pressure_level"], last_pressure_level,
        state["resource_snapshot"], state["sleep_seconds"],
        state["skip_heavy_tasks"],
    )


def _should_skip_cycle(state: dict, idle_interval: int) -> str | None:
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
    from jarvis_engine.main import cmd_mobile_desktop_sync, cmd_self_heal
    from jarvis_engine.cli_ops import cmd_ops_autopilot

    _set_process_title("jarvis-daemon")
    root = repo_root()
    from jarvis_engine.process_manager import remove_pid_file

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
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("Conversation state init on daemon startup failed: %s", exc)

    # Warm-start STT backends in a background thread to eliminate cold-start
    # latency when the user first issues a voice command.
    try:
        from jarvis_engine.stt import warmup_stt_backends

        threading.Thread(target=warmup_stt_backends, daemon=True).start()
        logger.debug("STT backend warmup started in background thread")
    except (ImportError, OSError) as exc:
        logger.debug("STT backend warmup launch failed: %s", exc)

    print("jarvis_daemon_started=true")
    print(f"active_interval_s={active_interval}")
    print(f"idle_interval_s={idle_interval}")
    print(f"idle_after_s={idle_after}")
    try:
        global _cycle_start  # noqa: PLW0603
        while True:
            cycles += 1
            _cycle_start = time.monotonic()
            state = _gather_cycle_state(root, active_interval, idle_interval, idle_after)
            _emit_cycle_status(cycles, state, last_pressure_level)
            last_pressure_level = state["pressure_level"]

            skip_reason = _should_skip_cycle(state, idle_interval)
            if skip_reason:
                print(skip_reason)
                if cfg.max_cycles > 0 and cycles >= cfg.max_cycles:
                    break
                _interruptible_sleep(max(idle_interval, 600))
                continue

            _run_periodic_subsystems(
                root, cycles, state["skip_heavy_tasks"], cfg.run_missions,
                cmd_mobile_desktop_sync, cmd_self_heal,
                cfg.sync_every_cycles, cfg.self_heal_every_cycles,
                cfg.self_test_every_cycles, cfg.watchdog_every_cycles,
            )

            rc = _run_core_autopilot(
                cfg.snapshot_path, cfg.actions_path, cfg.execute,
                cfg.approve_privileged, cfg.auto_open_connectors,
                state["safe_mode"], cmd_ops_autopilot,
            )
            print(f"cycle_rc={rc}")
            _log_cycle_end(cycles, rc)
            consecutive_failures = _handle_circuit_breaker(rc, consecutive_failures)

            # Watchdog: warn if cycle exceeded timeout
            cycle_elapsed = time.monotonic() - _cycle_start
            if cycle_elapsed > _CYCLE_TIMEOUT_S:
                logger.warning(
                    "Daemon cycle %d exceeded timeout: %.1fs > %ds",
                    cycles, cycle_elapsed, _CYCLE_TIMEOUT_S,
                )
                print(f"cycle_timeout_warning={cycle_elapsed:.1f}s")

            if cfg.max_cycles > 0 and cycles >= cfg.max_cycles:
                break
            print(f"sleep_s={state['sleep_seconds']}")
            _interruptible_sleep(state["sleep_seconds"])
    except KeyboardInterrupt:
        print("jarvis_daemon_stopped=true")
    finally:
        # Persist conversation state before shutdown
        try:
            from jarvis_engine.conversation_state import get_conversation_state

            _csm_shutdown = get_conversation_state()
            _csm_shutdown.save()
            logger.debug("Conversation state saved on daemon shutdown")
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Conversation state save on shutdown failed: %s", exc)
        remove_pid_file("daemon", root)
    return 0
