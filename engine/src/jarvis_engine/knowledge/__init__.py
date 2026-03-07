"""Knowledge subsystem -- NetworkX knowledge graph with SQLite persistence and fact extraction."""

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.knowledge.facts import FactExtractor, FactTriple
from jarvis_engine.knowledge.locks import FactLockManager
from jarvis_engine.knowledge.contradictions import ContradictionManager
from jarvis_engine.knowledge.regression import RegressionChecker
from jarvis_engine.knowledge.entity_resolver import (
    EntityResolver,
    MergeCandidate,
    ResolutionResult,
)

__all__ = [
    "KnowledgeGraph",
    "FactExtractor",
    "FactTriple",
    "FactLockManager",
    "ContradictionManager",
    "RegressionChecker",
    "EntityResolver",
    "MergeCandidate",
    "ResolutionResult",
]
