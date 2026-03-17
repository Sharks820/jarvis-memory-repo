"""Route mixin classes for MobileIngestHandler."""

from __future__ import annotations

from jarvis_engine.mobile_routes.agent import AgentRoutesMixin
from jarvis_engine.mobile_routes.auth import AuthRoutesMixin
from jarvis_engine.mobile_routes.command import CommandRoutesMixin
from jarvis_engine.mobile_routes.data import DataRoutesMixin
from jarvis_engine.mobile_routes.health import HealthRoutesMixin
from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
from jarvis_engine.mobile_routes.scam import ScamRoutesMixin
from jarvis_engine.mobile_routes.security import SecurityRoutesMixin
from jarvis_engine.mobile_routes.sync import SyncRoutesMixin
from jarvis_engine.mobile_routes.voice import VoiceRoutesMixin

__all__ = [
    "AgentRoutesMixin",
    "AuthRoutesMixin",
    "CommandRoutesMixin",
    "DataRoutesMixin",
    "HealthRoutesMixin",
    "IntelligenceRoutesMixin",
    "ScamRoutesMixin",
    "SecurityRoutesMixin",
    "SyncRoutesMixin",
    "VoiceRoutesMixin",
]
