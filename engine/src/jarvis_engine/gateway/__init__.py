"""Gateway package: unified LLM completion interface.

Exports ModelGateway, GatewayResponse, CostTracker, and IntentClassifier
for use by the rest of the Jarvis engine. Imports are at module level but
only reference local modules (no external SDK imports here).
"""

from jarvis_engine.gateway.classifier import IntentClassifier
from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.gateway.models import GatewayResponse, ModelGateway

__all__ = ["ModelGateway", "GatewayResponse", "CostTracker", "IntentClassifier"]
