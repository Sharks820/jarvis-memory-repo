from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jarvis_engine.capability import CapabilityGate
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.policy import PolicyEngine
from jarvis_engine.task_orchestrator import run_shell_command


@dataclass
class PlannedAction:
    title: str
    action_class: str
    command: str
    reason: str


@dataclass
class ActionOutcome:
    title: str
    allowed: bool
    executed: bool
    return_code: int
    reason: str
    stdout: str
    stderr: str


def load_actions(path: Path) -> list[PlannedAction]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Failed to load actions from {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(raw).__name__}")
    out: list[PlannedAction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            PlannedAction(
                title=str(item.get("title", "")),
                action_class=str(item.get("action_class", "bounded_write")),
                command=str(item.get("command", "")),
                reason=str(item.get("reason", "")),
            )
        )
    return out


class AutomationExecutor:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._gate = CapabilityGate()
        self._policy = PolicyEngine()

    def run(
        self,
        actions: list[PlannedAction],
        *,
        has_explicit_approval: bool,
        execute: bool,
    ) -> list[ActionOutcome]:
        outcomes: list[ActionOutcome] = []
        for action in actions:
            decision = self._gate.authorize(
                action_class=action.action_class,
                has_explicit_approval=has_explicit_approval,
                task_requires_expansion=False,
            )
            if not decision.allowed:
                outcome = ActionOutcome(
                    title=action.title,
                    allowed=False,
                    executed=False,
                    return_code=-1,
                    reason=decision.reason,
                    stdout="",
                    stderr="",
                )
                outcomes.append(outcome)
                self._log(outcome)
                continue

            if not execute or not action.command.strip():
                outcome = ActionOutcome(
                    title=action.title,
                    allowed=True,
                    executed=False,
                    return_code=0,
                    reason="Dry-run or no command specified.",
                    stdout="",
                    stderr="",
                )
                outcomes.append(outcome)
                self._log(outcome)
                continue

            if not self._policy.is_allowed(action.command):
                outcome = ActionOutcome(
                    title=action.title,
                    allowed=False,
                    executed=False,
                    return_code=-1,
                    reason="Command denied by policy allowlist.",
                    stdout="",
                    stderr=action.command,
                )
                outcomes.append(outcome)
                self._log(outcome)
                continue

            rc, stdout, stderr = run_shell_command(action.command, timeout_s=90)
            outcome = ActionOutcome(
                title=action.title,
                allowed=True,
                executed=True,
                return_code=rc,
                reason="Executed.",
                stdout=stdout,
                stderr=stderr,
            )
            outcomes.append(outcome)
            self._log(outcome)
        return outcomes

    def _log(self, outcome: ActionOutcome) -> None:
        message = (
            f"title={outcome.title} allowed={outcome.allowed} executed={outcome.executed} "
            f"return_code={outcome.return_code} reason={outcome.reason}"
        )
        self._store.append(event_type="automation_executor", message=message)
