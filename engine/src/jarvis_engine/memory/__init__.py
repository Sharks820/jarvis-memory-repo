"""Memory subsystem package -- SQLite + FTS5 + sqlite-vec engine with tiered storage."""

from jarvis_engine.memory.classify import BranchClassifier, BRANCH_DESCRIPTIONS
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.ingest import EnrichedIngestPipeline
from jarvis_engine.memory.search import hybrid_search
from jarvis_engine.memory.tiers import Tier, TierManager

# Re-export knowledge graph types for convenience
from jarvis_engine.knowledge import KnowledgeGraph, FactExtractor, FactTriple

__all__ = [
    "BranchClassifier",
    "BRANCH_DESCRIPTIONS",
    "EmbeddingService",
    "EnrichedIngestPipeline",
    "FactExtractor",
    "FactTriple",
    "KnowledgeGraph",
    "MemoryEngine",
    "Tier",
    "TierManager",
    "hybrid_search",
]
