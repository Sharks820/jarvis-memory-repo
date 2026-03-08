"""Gateway package: unified LLM completion interface.

Exports ModelGateway, GatewayResponse, CostTracker, GatewayAudit,
IntentClassifier, BudgetEnforcer, ProviderHealthTracker, and CLI
provider utilities for use by the rest of the Jarvis engine. Core
gateway imports are intentionally fail-fast so import errors surface
at module load rather than later as ``None`` dereferences.
"""

from jarvis_engine.gateway.audit import GatewayAudit
from jarvis_engine.gateway.budget import BudgetEnforcer, BudgetExceededError
from jarvis_engine.gateway.circuit_breaker import ProviderHealthTracker
from jarvis_engine.gateway.classifier import IntentClassifier
from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.gateway.models import GatewayResponse, ModelGateway
from jarvis_engine.gateway.cli_providers import detect_cli_providers

__all__ = [
    "ModelGateway",
    "GatewayResponse",
    "CostTracker",
    "GatewayAudit",
    "IntentClassifier",
    "BudgetEnforcer",
    "BudgetExceededError",
    "ProviderHealthTracker",
    "detect_cli_providers",
]
