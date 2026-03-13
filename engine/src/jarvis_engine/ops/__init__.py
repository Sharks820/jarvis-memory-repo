"""Ops subpackage — operations/runtime modules.

Re-exports key public names for backward compatibility.
"""

from jarvis_engine.ops.autopilot import run_ops_autopilot
from jarvis_engine.ops.sync import (
    SyncSummary,
    build_live_snapshot,
    load_calendar_events,
    load_task_items,
)
from jarvis_engine.ops.runtime_control import (
    ControlState,
    DaemonSleepRecommendation,
    ResourceSnapshot,
    read_control_state,
    write_control_state,
    reset_control_state,
    read_resource_budgets,
    read_resource_pressure_state,
    capture_runtime_resource_snapshot,
    write_resource_pressure_state,
    recommend_daemon_sleep,
)
from jarvis_engine.ops.process_manager import (
    SERVICES,
    is_service_running,
    write_pid_file,
    read_pid_file,
    remove_pid_file,
    kill_service,
    list_services,
    check_and_restart_services,
)
from jarvis_engine.ops.resilience import (
    run_mobile_desktop_sync,
    run_self_heal,
    SyncReport,
)

__all__ = [
    "run_ops_autopilot",
    "SyncSummary",
    "build_live_snapshot",
    "load_calendar_events",
    "load_task_items",
    "ControlState",
    "DaemonSleepRecommendation",
    "ResourceSnapshot",
    "read_control_state",
    "write_control_state",
    "reset_control_state",
    "read_resource_budgets",
    "read_resource_pressure_state",
    "capture_runtime_resource_snapshot",
    "write_resource_pressure_state",
    "recommend_daemon_sleep",
    "SERVICES",
    "is_service_running",
    "write_pid_file",
    "read_pid_file",
    "remove_pid_file",
    "kill_service",
    "list_services",
    "check_and_restart_services",
    "run_mobile_desktop_sync",
    "run_self_heal",
    "SyncReport",
]
