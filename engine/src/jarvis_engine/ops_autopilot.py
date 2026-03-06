"""Public ops-autopilot orchestration logic.

Extracted from ``jarvis_engine.main`` so that ``OpsAutopilotHandler`` can
import the implementation without reaching into private ``main`` internals.
"""

from __future__ import annotations

import logging
from pathlib import Path

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
    # Lazy imports to avoid circular dependencies -- these functions live in
    # main.py but dispatch through the CQRS bus, so we import them at call time.
    from jarvis_engine.main import (
        cmd_automation_run,
        cmd_connect_bootstrap,
        cmd_ops_brief,
        cmd_ops_export_actions,
        cmd_ops_sync,
    )

    cmd_connect_bootstrap(auto_open=auto_open_connectors)
    sync_rc = cmd_ops_sync(snapshot_path)
    if sync_rc != 0:
        return sync_rc
    brief_rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    if brief_rc != 0:
        return brief_rc
    export_rc = cmd_ops_export_actions(snapshot_path=snapshot_path, actions_path=actions_path)
    if export_rc != 0:
        return export_rc
    return cmd_automation_run(
        actions_path=actions_path,
        approve_privileged=approve_privileged,
        execute=execute,
    )
