"""Backward-compatibility shim — real implementation moved to jarvis_engine.ops.task_orchestrator."""
from jarvis_engine.ops.task_orchestrator import *  # noqa: F401, F403
from jarvis_engine.ops.task_orchestrator import (  # noqa: F401, E402
    TaskRequest,
    TaskResult,
    TaskOrchestrator,
    DEFAULT_FALLBACK_MODELS,
    _SHELL_COMMAND_ALLOWLIST,
    _PRIVILEGED_SHELL_ALLOWLIST,
    run_shell_command,
)
