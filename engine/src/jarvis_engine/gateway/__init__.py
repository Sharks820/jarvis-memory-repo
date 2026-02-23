"""Gateway package: unified LLM completion interface.

Exports ModelGateway, GatewayResponse, CostTracker, and IntentClassifier
for use by the rest of the Jarvis engine. Imports are wrapped in try/except
so the package can be imported even if optional SDKs (anthropic, ollama) are
not installed.
"""

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

__all__ = ["ModelGateway", "GatewayResponse", "CostTracker", "IntentClassifier"]
