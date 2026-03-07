from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityDecision:
    allowed: bool
    reason: str


class CapabilityGate:
    """
    Tiered authorization model.
    Tier 0: read-only
    Tier 1: bounded write
    Tier 2: privileged (explicit approval required)
    """

    def __init__(self) -> None:
        self._tier_map = {
            "read": 0,
            "bounded_write": 1,
            "privileged": 2,
        }

    def authorize(
        self,
        action_class: str,
        has_explicit_approval: bool,
        task_requires_expansion: bool,
    ) -> CapabilityDecision:
        if action_class not in self._tier_map:
            return CapabilityDecision(False, "Unknown action class.")

        tier = self._tier_map[action_class]
        if tier <= 1:
            return CapabilityDecision(True, "Allowed under non-privileged tier.")

        if has_explicit_approval:
            return CapabilityDecision(True, "Privileged action explicitly approved.")

        if task_requires_expansion:
            return CapabilityDecision(
                False,
                "Task may require expansion, but privileged execution still needs explicit approval.",
            )

        return CapabilityDecision(
            False, "Privileged action denied without explicit approval."
        )
