"""mobile_routes — Route mixin classes for MobileIngestHandler.

Each mixin groups related HTTP endpoint handlers by domain.
MobileIngestHandler inherits from all mixins, gaining the handler
methods while keeping the core HTTP plumbing in mobile_api.py.
"""

from __future__ import annotations

from jarvis_engine.mobile_routes.auth import AuthRoutesMixin
from jarvis_engine.mobile_routes.command import CommandRoutesMixin
from jarvis_engine.mobile_routes.data import DataRoutesMixin
from jarvis_engine.mobile_routes.health import HealthRoutesMixin
from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
from jarvis_engine.mobile_routes.scam import ScamRoutesMixin
from jarvis_engine.mobile_routes.security import SecurityRoutesMixin
from jarvis_engine.mobile_routes.sync import SyncRoutesMixin

__all__ = [
    "AuthRoutesMixin",
    "CommandRoutesMixin",
    "DataRoutesMixin",
    "HealthRoutesMixin",
    "IntelligenceRoutesMixin",
    "ScamRoutesMixin",
    "SecurityRoutesMixin",
    "SyncRoutesMixin",
]
