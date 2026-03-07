"""Public ops-autopilot orchestration logic.

Extracted from ``jarvis_engine.main`` so that ``OpsAutopilotHandler`` can
import the implementation without reaching into private ``main`` internals.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis_engine._bus import get_bus
from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
)
from jarvis_engine.commands.security_commands import ConnectBootstrapCommand

logger = logging.getLogger(__name__)


def run_ops_autopilot(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    """Implementation body for ops-autopilot (called by handler via callback).

    Orchestrates the full autopilot pipeline:
    1. Bootstrap connectors
    2. Sync live snapshot
    3. Build daily brief
    4. Export suggested actions
    5. Run automation
    """
    bus = get_bus()

    # 1. Bootstrap connectors
    bus.dispatch(ConnectBootstrapCommand(auto_open=auto_open_connectors))

    # 2. Sync live snapshot
    sync_result = bus.dispatch(OpsSyncCommand(output_path=snapshot_path))
    summary = sync_result.summary
    if summary is None:
        print("error: ops sync failed")
        return 2
    print(f"snapshot_path={summary.snapshot_path}")
    print(f"tasks={summary.tasks}")
    print(f"calendar_events={summary.calendar_events}")
    print(f"emails={summary.emails}")
    print(f"connectors_ready={summary.connectors_ready}")

    # 3. Build daily brief
    brief_result = bus.dispatch(OpsBriefCommand(snapshot_path=snapshot_path, output_path=None))
    print(brief_result.brief)
    if brief_result.saved_path:
        print(f"brief_saved={brief_result.saved_path}")

    # 4. Export suggested actions
    export_result = bus.dispatch(OpsExportActionsCommand(
        snapshot_path=snapshot_path, actions_path=actions_path,
    ))
    print(f"actions_exported={export_result.actions_path}")
    print(f"action_count={export_result.action_count}")

    # 5. Run automation
    auto_result = bus.dispatch(AutomationRunCommand(
        actions_path=actions_path,
        approve_privileged=approve_privileged,
        execute=execute,
    ))
    for out in auto_result.outcomes:
        print(
            f"title={out.title} allowed={out.allowed} executed={out.executed} "
            f"return_code={out.return_code} reason={out.reason}"
        )
        if out.stderr:
            print(f"stderr={out.stderr.strip()}")
    return 0
