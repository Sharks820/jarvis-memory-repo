"""Gateway package: unified LLM completion interface.

Exports ModelGateway, GatewayResponse, CostTracker, GatewayAudit,
IntentClassifier, and CLI provider utilities for use by the rest of the
Jarvis engine. Imports are wrapped in try/except so the package can be
imported even if optional SDKs (anthropic, ollama) are not installed.
"""

try:
    from jarvis_engine.gateway.audit import GatewayAudit
except ImportError:
    GatewayAudit = None  # type: ignore[assignment,misc]

try:
    from jarvis_engine.gateway.classifier import IntentClassifier
except ImportError:
    IntentClassifier = None  # type: ignore[assignment,misc]

try:
    from jarvis_engine.gateway.costs import CostTracker
except ImportError:
    CostTracker = None  # type: ignore[assignment,misc]

try:
    from jarvis_engine.gateway.models import GatewayResponse, ModelGateway
except ImportError:
    GatewayResponse = None  # type: ignore[assignment,misc]
    ModelGateway = None  # type: ignore[assignment,misc]

try:
    from jarvis_engine.gateway.cli_providers import detect_cli_providers
except ImportError:
    detect_cli_providers = None  # type: ignore[assignment,misc]

__all__ = [
    "ModelGateway",
    "GatewayResponse",
    "CostTracker",
    "GatewayAudit",
    "IntentClassifier",
    "detect_cli_providers",
]
