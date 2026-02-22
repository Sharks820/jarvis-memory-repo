from __future__ import annotations

from jarvis_engine.automation import AutomationExecutor, PlannedAction
from jarvis_engine.memory_store import MemoryStore


def test_automation_denies_privileged_without_approval(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    executor = AutomationExecutor(store)
    actions = [
        PlannedAction(
            title="Pay utility",
            action_class="privileged",
            command="",
            reason="bill",
        )
    ]
    outcomes = executor.run(actions, has_explicit_approval=False, execute=False)
    assert len(outcomes) == 1
    assert outcomes[0].allowed is False
    assert outcomes[0].executed is False

