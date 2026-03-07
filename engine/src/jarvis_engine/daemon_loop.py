"""Daemon loop — extracted from main.py for better separation of concerns."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from jarvis_engine._bus import get_bus
from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._constants import (
    DEFAULT_API_PORT as _DEFAULT_API_PORT,
    memory_db_path as _memory_db_path,
    KG_METRICS_LOG as _KG_METRICS_LOG,
    SELF_TEST_HISTORY as _SELF_TEST_HISTORY,
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

# Re-export gaming mode helpers for backward compatibility (tests patch these names)
from jarvis_engine.gaming_mode import (  # noqa: F401
    DEFAULT_GAMING_PROCESSES,
    _game_detect_cache,
    _GAME_DETECT_CACHE_TTL,
    _windows_idle_seconds,
)
from jarvis_engine.gaming_mode import (
    GamingModeState,
    detect_active_game_process as _gm_detect_active_game_process,
    load_gaming_processes as _gm_load_gaming_processes,
    read_gaming_mode_state as _gm_read_gaming_mode_state,
    write_gaming_mode_state as _gm_write_gaming_mode_state,
)

# Re-export harvest discovery helpers (used by _discover_harvest_topics below)
from jarvis_engine.harvest_discovery import (  # noqa: F401
    _SQL_NODE_BY_RELATION,
    _SQL_RARE_RELATIONS,
    _SQL_RECENT_SUMMARIES,
    _SQL_SPARSE_NODES,
    _SQL_STRONG_LABELS,
    _extract_topic_phrases,
    _get_recently_harvested_topics,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daemon-scoped bus cache (avoids recreating MemoryEngine per periodic task)
# ---------------------------------------------------------------------------
_daemon_bus: CommandBus | None = None
_daemon_bus_lock = threading.Lock()


def _get_daemon_bus() -> CommandBus:
    """Return cached daemon bus, creating once on first call (thread-safe)."""
    global _daemon_bus
    if _daemon_bus is None:
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
# Auto-harvest topic discovery for daemon cycle
# ---------------------------------------------------------------------------

def _discover_harvest_topics(root: Path) -> list[str]:
    """Discover 2-3 topics for autonomous knowledge harvesting.

    Topic sources (in priority order):
    1. Conversation-derived: recent memory entries (last 7 days) — multi-word phrases
    2. KG gap analysis: edge relation types with few instances or high-node/low-edge areas
    3. Complementary topics: strong KG areas expanded with "best practices"/"advanced"
    4. Activity feed: recent fact extraction summaries
    5. Fallback: completed learning mission topics

    All topics are 2-5 words.  Deduplicates against recently harvested topics.
    Returns up to 3 topic strings.  Never raises — returns [] on error.
    """
    _MAX_TOPICS = 3
    candidates: list[str] = []
    seen_lower: set[str] = set()

    # Load recently harvested topics for dedup
    recently_harvested = _get_recently_harvested_topics(root)

    def _add_candidate(topic: str) -> bool:
        """Add a topic candidate if unique and not recently harvested.  Returns True if added."""
        topic = topic.strip()
        if not topic or len(topic) < 4:
            return False
        tl = topic.lower()
        if tl in seen_lower or tl in recently_harvested:
            return False
        # Ensure 2-5 words
        word_count = len(topic.split())
        if word_count < 2 or word_count > 5:
            return False
        seen_lower.add(tl)
        candidates.append(topic)
        return len(candidates) >= _MAX_TOPICS

    # Open a single shared SQLite connection for sources 1-3 (memory + KG queries)
    from datetime import timedelta

    db_path = _memory_db_path(root)
    conn = None
    try:
        if db_path.exists():
            try:
                from jarvis_engine._db_pragmas import connect_db as _connect_db
                conn = _connect_db(db_path)
            except (sqlite3.Error, OSError) as exc:
                # Corrupt or inaccessible DB — skip all DB-based sources
                logger.debug("Failed to connect to memory DB: %s", exc)
                if conn is not None:
                    conn.close()
                conn = None

        # --- Source 1: Conversation-derived topics from recent memories ---
        if conn is not None:
            try:
                cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
                rows = conn.execute(
                    _SQL_RECENT_SUMMARIES, (cutoff,),
                ).fetchall()
                for row in rows:
                    summary = row["summary"] or ""
                    phrases = _extract_topic_phrases(summary)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break
            except sqlite3.OperationalError:
                pass  # Memory tables may not exist yet

        if len(candidates) >= _MAX_TOPICS:
            return candidates[:_MAX_TOPICS]

        # --- Source 2: KG gap analysis — relation types with few edges + sparse areas ---
        if conn is not None:
            try:
                # 2a: Find nodes that have few outgoing edges (surface-level knowledge)
                # These represent areas where we have facts but not much depth
                sparse_rows = conn.execute(_SQL_SPARSE_NODES).fetchall()
                for row in sparse_rows:
                    label = row["label"] or ""
                    phrases = _extract_topic_phrases(label)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break

                # 2b: Find relation types with few instances — structural KG gaps
                if len(candidates) < _MAX_TOPICS:
                    rel_rows = conn.execute(_SQL_RARE_RELATIONS).fetchall()
                    for row in rel_rows:
                        relation = row["relation"] or ""
                        # Turn relation into a topic: "causes" -> look up nodes
                        # Find a node connected by this rare relation for context
                        node_row = conn.execute(
                            _SQL_NODE_BY_RELATION, (relation,),
                        ).fetchone()
                        if node_row:
                            label = node_row["label"] or ""
                            phrases = _extract_topic_phrases(label)
                            for phrase in phrases:
                                if _add_candidate(phrase):
                                    break
                        if len(candidates) >= _MAX_TOPICS:
                            break
            except sqlite3.OperationalError:
                pass  # KG tables may not exist yet

        if len(candidates) >= _MAX_TOPICS:
            return candidates[:_MAX_TOPICS]

        # --- Source 3: Complementary topics — expand strong KG areas ---
        if conn is not None:
            try:
                # Fetch raw labels and extract first 2 word prefixes in Python
                label_rows = conn.execute(_SQL_STRONG_LABELS).fetchall()
                prefix_counts: dict[str, int] = {}
                for row in label_rows:
                    label = (row["label"] or "").strip()
                    words = label.split()
                    if len(words) >= 2:
                        prefix = " ".join(words[:2])
                        if len(prefix) > 3:
                            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
                # Keep prefixes with >= 5 nodes, sorted by count descending
                strong_prefixes = sorted(
                    ((p, c) for p, c in prefix_counts.items() if c >= 5),
                    key=lambda x: x[1],
                    reverse=True,
                )[:5]
                suffixes = ["best practices", "advanced techniques", "common patterns"]
                suffix_idx = 0
                for prefix, _cnt in strong_prefixes:
                    expanded = f"{prefix} {suffixes[suffix_idx % len(suffixes)]}"
                    suffix_idx += 1
                    if _add_candidate(expanded):
                        break
                    if len(candidates) >= _MAX_TOPICS:
                        break
            except (sqlite3.Error, OSError) as exc:
                logger.debug("Failed to discover harvest topics from knowledge graph: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    if len(candidates) >= _MAX_TOPICS:
        return candidates[:_MAX_TOPICS]

    # --- Source 4: Activity feed fact-extraction summaries ---
    try:
        from jarvis_engine.activity_feed import ActivityFeed, ActivityCategory
        feed_db = root / ".planning" / "brain" / "activity_feed.db"
        if feed_db.exists():
            feed = ActivityFeed(db_path=feed_db)
            events = feed.query(limit=20, category=ActivityCategory.FACT_EXTRACTED)
            for ev in events:
                summary = ev.summary or ""
                if len(summary) > 5:
                    phrases = _extract_topic_phrases(summary)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        logger.debug("Failed to extract harvest topics from activity feed fact summaries: %s", exc)

    if len(candidates) >= _MAX_TOPICS:
        return candidates[:_MAX_TOPICS]

    # --- Source 5: Fallback — completed learning mission topics ---
    try:
        missions = load_missions(root)
        for m in reversed(missions):
            status = str(m.get("status", "")).lower()
            if status in ("completed", "done", "running"):
                topic = str(m.get("topic", "")).strip()
                if topic:
                    # If it's already multi-word, use as-is; else skip (single words are poor)
                    if len(topic.split()) >= 2:
                        if _add_candidate(topic):
                            break
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("Failed to discover harvest topics from learning missions: %s", exc)

    return candidates[:_MAX_TOPICS]


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
    *,
    daemon_paused: bool,
    safe_mode: bool,
    gaming_mode_enabled: bool,
    auto_detect: bool,
    detected_process: str,
    gaming_state: dict,
    control_state: dict,
    is_active: bool,
    pressure_level: str,
    resource_snapshot: dict,
    sleep_seconds: int,
    skip_heavy_tasks: bool,
    idle_seconds: float | None,
) -> None:
    """Print all per-cycle status lines to stdout."""
    print(f"cycle={cycles} ts={cycle_start_ts}")
    print(f"daemon_paused={daemon_paused}")
    print(f"safe_mode={safe_mode}")
    print(f"gaming_mode={gaming_mode_enabled}")
    print(f"gaming_mode_auto_detect={auto_detect}")
    if detected_process:
        print(f"gaming_mode_detected_process={detected_process}")
    if gaming_state.get("reason", ""):
        print(f"gaming_mode_reason={gaming_state.get('reason', '')}")
    if control_state.get("reason", ""):
        print(f"runtime_control_reason={control_state.get('reason', '')}")
    print(f"device_active={is_active}")
    print(f"resource_pressure_level={pressure_level}")
    try:
        _m = resource_snapshot.get("metrics", {})
        _rss = _m.get("process_memory_mb", {}).get("current", 0.0)
        _cpu = _m.get("process_cpu_pct", {}).get("current", 0.0)
        _emb = _m.get("embedding_cache_mb", {}).get("current", 0.0)
        print(f"resource_process_memory_mb={_rss}")
        print(f"resource_process_cpu_pct={_cpu}")
        print(f"resource_embedding_cache_mb={_emb}")
    except (AttributeError, KeyError, TypeError) as exc:
        logger.debug("Resource metric print failed: %s", exc)
    if pressure_level in {"mild", "severe"}:
        print(f"resource_throttle_sleep_s={sleep_seconds}")
        if skip_heavy_tasks:
            print("resource_skip_heavy_tasks=true")
    if idle_seconds is not None:
        print(f"idle_seconds={round(idle_seconds, 1)}")


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
    try:
        mission_rc = _run_next_pending_mission()
    except (ImportError, OSError, sqlite3.Error, AttributeError, KeyError, ValueError, RuntimeError) as exc:
        mission_rc = 2
        logger.warning("Daemon mission cycle failed: %s", exc)
        print(f"mission_cycle_error={exc}")
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
    """Collect and append KG growth metrics (never raises)."""
    try:
        import sqlite3 as _sqlite3

        from jarvis_engine.proactive.kg_metrics import collect_kg_metrics, append_kg_metrics

        db_path = _memory_db_path(root)
        if db_path.exists():
            _kg_conn = _sqlite3.connect(str(db_path), timeout=5)
            from jarvis_engine._db_pragmas import configure_sqlite as _cfg_sql

            _cfg_sql(_kg_conn)
            try:
                # collect_kg_metrics uses kg.db — provide a lightweight shim
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


def cmd_daemon_run_impl(
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
    watchdog_every_cycles: int = 5,
) -> int:
    """Implementation body for daemon-run (called by handler via callback)."""
    from jarvis_engine.main import (
        cmd_mobile_desktop_sync,
        cmd_self_heal,
        cmd_ops_autopilot,
    )

    # Set descriptive process title for Task Manager visibility
    _set_process_title("jarvis-daemon")

    root = repo_root()
    # Register PID file for duplicate detection and dashboard visibility
    from jarvis_engine.process_manager import remove_pid_file

    if not _register_daemon_pid(root):
        return 4

    active_interval = max(30, interval_s)
    idle_interval = max(30, idle_interval_s)
    idle_after = max(60, idle_after_s)
    max_consecutive_failures = 10
    consecutive_failures = 0
    cycles = 0
    last_pressure_level = "none"
    print("jarvis_daemon_started=true")
    print(f"active_interval_s={active_interval}")
    print(f"idle_interval_s={idle_interval}")
    print(f"idle_after_s={idle_after}")
    try:
        while True:
            cycles += 1
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
            gaming_mode_enabled = bool(gaming_state.get("enabled", False)) or auto_detect_hit
            daemon_paused = bool(control_state.get("daemon_paused", False))
            safe_mode = bool(control_state.get("safe_mode", False))
            cycle_start_ts = _now_iso()
            # --- Activity feed: log cycle start ---
            _log_cycle_start(cycles, cycle_start_ts)
            # --- Print all cycle status lines ---
            _print_cycle_status(
                cycles,
                cycle_start_ts,
                daemon_paused=daemon_paused,
                safe_mode=safe_mode,
                gaming_mode_enabled=gaming_mode_enabled,
                auto_detect=auto_detect,
                detected_process=detected_process,
                gaming_state=gaming_state,
                control_state=control_state,
                is_active=is_active,
                pressure_level=pressure_level,
                resource_snapshot=resource_snapshot,
                sleep_seconds=sleep_seconds,
                skip_heavy_tasks=skip_heavy_tasks,
                idle_seconds=idle_seconds,
            )
            # --- Log resource pressure changes ---
            _log_resource_pressure(
                cycles, pressure_level, last_pressure_level,
                resource_snapshot, sleep_seconds, skip_heavy_tasks,
            )
            last_pressure_level = pressure_level
            if daemon_paused:
                print("cycle_skipped=runtime_control_daemon_paused")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            if gaming_mode_enabled:
                print("cycle_skipped=gaming_mode_enabled")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            # --- Non-core subsystems: isolated so failures never affect circuit breaker ---
            if run_missions:
                _run_missions_cycle(root, cycles, skip_heavy_tasks)
            if sync_every_cycles > 0 and (cycles == 1 or cycles % sync_every_cycles == 0):
                _run_sync_cycle(cmd_mobile_desktop_sync)
            if watchdog_every_cycles > 0 and cycles % watchdog_every_cycles == 0:
                _run_watchdog_cycle(root)
            if self_heal_every_cycles > 0 and (cycles == 1 or cycles % self_heal_every_cycles == 0):
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
            # --- Core autopilot: only this drives the circuit breaker ---
            exec_cycle = execute and not safe_mode
            approve_cycle = approve_privileged and not safe_mode
            if safe_mode and (execute or approve_privileged):
                print("safe_mode_override=execute_and_privileged_flags_forced_false")
            try:
                rc = cmd_ops_autopilot(
                    snapshot_path=snapshot_path,
                    actions_path=actions_path,
                    execute=exec_cycle,
                    approve_privileged=approve_cycle,
                    auto_open_connectors=auto_open_connectors,
                )
            except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
                rc = 2
                logger.warning("Daemon autopilot cycle failed: %s", exc)
                print(f"cycle_error={exc}")
            print(f"cycle_rc={rc}")
            # --- Activity feed: log cycle end ---
            _log_cycle_end(cycles, rc)
            # Circuit breaker: only autopilot (rc) counts toward consecutive failures.
            # Mission, sync, and self-heal failures are logged but never trigger shutdown.
            if rc == 0:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                print(f"consecutive_failures={consecutive_failures}")
                if consecutive_failures >= max_consecutive_failures:
                    print("daemon_circuit_breaker_open=true cooldown=300s")
                    consecutive_failures = 0  # Reset counter after cooldown
                    time.sleep(300)  # 5-minute cooldown instead of exit
            if max_cycles > 0 and cycles >= max_cycles:
                break
            print(f"sleep_s={sleep_seconds}")
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("jarvis_daemon_stopped=true")
    finally:
        remove_pid_file("daemon", root)
    return 0
