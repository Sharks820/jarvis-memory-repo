"""Backward-compat shim -- moved to jarvis_engine.memory.basic_ingest."""
from jarvis_engine.memory.basic_ingest import (  # noqa: F401
    IngestRecord,
    IngestionPipeline,
    MemoryKind,
    SourceType,
    _VALID_KINDS,
    _VALID_SOURCES,
)
