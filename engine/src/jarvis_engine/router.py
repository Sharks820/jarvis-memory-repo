from __future__ import annotations

import warnings
from dataclasses import dataclass


@dataclass
class RouteDecision:
    provider: str
    reason: str


class ModelRouter:
    def __init__(self, cloud_burst_enabled: bool) -> None:
        warnings.warn(
            "ModelRouter is deprecated; use IntentClassifier + ModelGateway instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.cloud_burst_enabled = cloud_burst_enabled

    def route(self, risk: str, complexity: str) -> RouteDecision:
        if risk in {"high", "critical"} and self.cloud_burst_enabled:
            return RouteDecision(
                provider="cloud_verifier",
                reason="High-risk task routed for stronger verification.",
            )

        if complexity in {"hard", "very_hard"} and self.cloud_burst_enabled:
            return RouteDecision(
                provider="cloud_burst",
                reason="Complex task routed to cloud burst path.",
            )

        return RouteDecision(
            provider="local_primary",
            reason="Default local-first routing.",
        )

