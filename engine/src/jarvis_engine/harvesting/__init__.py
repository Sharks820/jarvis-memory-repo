"""Knowledge harvesting package: multi-provider LLM knowledge extraction.

Exports the orchestrator, command/result types, all provider classes,
session ingestors, and budget management.
Provider imports are wrapped in try/except for graceful degradation when
optional SDKs (openai, google-genai) are not installed.
"""

from jarvis_engine.harvesting.budget import BudgetManager
from jarvis_engine.harvesting.harvester import (
    HarvestCommand,
    HarvestResult,
    KnowledgeHarvester,
)
from jarvis_engine.harvesting.providers import (
    GeminiProvider,
    HarvesterProvider,
    KimiNvidiaProvider,
    KimiProvider,
    MiniMaxProvider,
)
from jarvis_engine.harvesting.session_ingestors import (
    ClaudeCodeIngestor,
    CodexIngestor,
)

__all__ = [
    "KnowledgeHarvester",
    "HarvestCommand",
    "HarvestResult",
    "HarvesterProvider",
    "MiniMaxProvider",
    "KimiProvider",
    "KimiNvidiaProvider",
    "GeminiProvider",
    "ClaudeCodeIngestor",
    "CodexIngestor",
    "BudgetManager",
]
