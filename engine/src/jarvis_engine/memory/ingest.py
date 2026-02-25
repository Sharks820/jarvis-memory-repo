"""Enriched ingestion pipeline with chunking, embedding, and semantic classification.

Pipeline steps: sanitize -> deduplicate (SHA-256) -> chunk (>2000 chars) ->
embed -> classify branch -> write SQLite via MemoryEngine -> extract facts.

This is SEPARATE from engine/src/jarvis_engine/ingest.py (the old pipeline).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from jarvis_engine._compat import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

# Patterns for credential redaction (matches password, token, secret, api_key, etc.)
_CREDENTIAL_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(token|api[_-]?key|secret|signing[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(bearer)\s+\S+", re.IGNORECASE),
]


class EnrichedIngestPipeline:
    """Enriched memory ingestion with chunking, embedding, and branch classification."""

    def __init__(
        self,
        engine: "MemoryEngine",
        embed_service: "EmbeddingService",
        classifier: "BranchClassifier",
        knowledge_graph: "KnowledgeGraph | None" = None,
        gateway: "object | None" = None,
    ) -> None:
        self._engine = engine
        self._embed_service = embed_service
        self._classifier = classifier
        self._kg = knowledge_graph
        self._gateway = gateway
        self._fact_extractor = None  # Lazy-initialized on first use
        self._llm_extractor = None  # Lazy-initialized on first use

    def ingest(
        self,
        source: str,
        kind: str,
        task_id: str,
        content: str,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Ingest content through the enriched pipeline.

        Pipeline: sanitize -> dedup -> chunk -> embed -> classify -> store.

        Args:
            source: Origin of content (user, claude, task_outcome, etc.).
            kind: Memory kind (episodic, semantic, procedural).
            task_id: Task identifier.
            content: Raw content text.
            tags: Optional tags list.

        Returns:
            List of inserted record IDs (empty if all duplicates).
        """
        # Step 1: Sanitize
        sanitized = self._sanitize(content)
        if not sanitized:
            return []

        # Step 2: Pre-flight dedup for short content
        full_content_hash = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        chunks = self._chunk_content(sanitized)
        if len(chunks) == 1:
            # Single chunk -- check full content hash for quick dedup
            existing = self._engine.get_record_by_hash(full_content_hash)
            if existing is not None:
                return []

        # Step 3 & 4: Process each chunk
        ts = datetime.now(UTC).isoformat()
        tag_list = sorted({t.lower() for t in (tags or []) if t.strip()})[:10]
        tag_str = json.dumps(tag_list)
        inserted_ids: list[str] = []

        for chunk in chunks:
            # Skip empty chunks
            if not chunk.strip():
                continue

            # 4a: Per-chunk content hash (CRITICAL: hash chunk text, NOT full document)
            chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()

            # 4b: Generate record_id -- 32 hex chars (Codex finding: >16 to avoid collisions)
            id_material = f"{source}|{kind}|{task_id}|{chunk}".encode("utf-8")
            record_id = hashlib.sha256(id_material).hexdigest()[:32]

            # 4c: Generate embedding
            embedding = self._embed_service.embed(chunk, prefix="search_document")

            # 4d: Classify branch
            branch = self._classifier.classify(embedding)

            # 4e: Build summary with word-boundary truncation
            summary = chunk[:200]
            if len(chunk) > 200:
                # Truncate at last space to avoid mid-word break
                last_space = summary.rfind(" ")
                if last_space > 100:
                    summary = summary[:last_space]

            # 4f: Build record dict
            record = {
                "record_id": record_id,
                "ts": ts,
                "source": source,
                "kind": kind,
                "task_id": task_id,
                "branch": branch,
                "tags": tag_str,
                "summary": summary,
                "content_hash": chunk_hash,
                "confidence": 0.72,
                "tier": "warm",
                "access_count": 0,
                "last_accessed": "",
            }

            # 4g: Insert via engine (UNIQUE constraint on content_hash handles dedup)
            was_inserted = self._engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted_ids.append(record_id)

                # 4h: Extract facts into knowledge graph (side effect -- failure
                # must NOT prevent the record from being stored)
                if self._kg is not None:
                    try:
                        self._extract_facts(chunk, source, branch, record_id)
                    except Exception as exc:
                        logger.warning(
                            "Fact extraction failed for record %s: %s",
                            record_id,
                            exc,
                        )

                    # 4i: LLM-powered fact extraction (supplements regex extraction)
                    try:
                        llm_extractor = self._get_llm_extractor()
                        if llm_extractor:
                            llm_facts = llm_extractor.extract_facts(chunk, branch=branch)
                            for fact in llm_facts:
                                node_id = f"llm:{fact.entity}.{fact.relationship}.{fact.value}"[:64]
                                self._kg.add_fact(
                                    node_id=node_id,
                                    label=f"{fact.entity}: {fact.value}",
                                    confidence=fact.confidence,
                                    source_record=record_id,
                                    node_type=fact.category or "fact",
                                )
                    except Exception as exc:
                        logger.warning("LLM fact extraction failed: %s", exc)

        return inserted_ids

    def _get_llm_extractor(self) -> "object | None":
        """Lazy-initialize the LLM fact extractor if a gateway is available."""
        if self._llm_extractor is not None:
            return self._llm_extractor
        if self._gateway is None:
            return None
        try:
            from jarvis_engine.knowledge.llm_extractor import LLMFactExtractor

            self._llm_extractor = LLMFactExtractor(gateway=self._gateway)
            return self._llm_extractor
        except ImportError:
            return None

    def _extract_facts(
        self,
        content: str,
        source: str,
        branch: str,
        record_id: str,
    ) -> None:
        """Extract structured facts from content and insert into knowledge graph.

        Lazily imports FactExtractor to avoid circular imports.
        """
        if self._fact_extractor is None:
            from jarvis_engine.knowledge.facts import FactExtractor
            self._fact_extractor = FactExtractor()

        triples = self._fact_extractor.extract(content, source, branch)
        if not triples:
            return

        # Ensure provenance node exists before creating edges (avoids FK violation)
        provenance_id = f"ingest:{record_id}"
        self._kg.add_fact(
            node_id=provenance_id,
            label=f"Record {record_id}",
            confidence=1.0,
            node_type="provenance",
        )

        created_fact_ids: list[str] = []
        for triple in triples:
            self._kg.add_fact(
                node_id=triple.subject,
                label=triple.object_val,
                confidence=triple.confidence,
                source_record=record_id,
                node_type=triple.predicate,
            )
            self._kg.add_edge(
                source_id=provenance_id,
                target_id=triple.subject,
                relation="extracted_from",
                confidence=triple.confidence,
                source_record=record_id,
            )
            created_fact_ids.append(triple.subject)

        # Cross-branch edge creation: link new facts to related facts in other
        # knowledge branches.  Wrapped in try/except so failures never break
        # the ingest pipeline.
        try:
            from jarvis_engine.learning.cross_branch import create_cross_branch_edges

            for fact_id in created_fact_ids:
                create_cross_branch_edges(self._kg, fact_id, record_id)
        except Exception as exc:
            logger.debug("Cross-branch edge creation failed: %s", exc)

    def _sanitize(self, content: str) -> str:
        """Sanitize content: strip, truncate, redact credentials."""
        text = content.strip()
        if not text:
            return ""
        # Truncate to 10000 chars
        text = text[:10000]
        # Redact credential patterns
        for pattern in _CREDENTIAL_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def _chunk_content(self, content: str, max_chunk: int = 1500) -> list[str]:
        """Split content into chunks at sentence boundaries.

        If content is short enough (within 20% of max_chunk), return as single chunk.
        Otherwise split on sentence boundaries: '. ', '.\\n', '\\n\\n'.
        """
        if len(content) <= int(max_chunk * 1.2):
            return [content]

        # Split on sentence boundaries
        # First try paragraph breaks, then sentence ends
        sentences: list[str] = []
        # Split on double newlines first
        paragraphs = re.split(r"\n\n+", content)
        for para in paragraphs:
            # Split paragraphs on sentence-ending patterns
            parts = re.split(r"(?<=\. )", para)
            for part in parts:
                stripped = part.strip()
                if stripped:
                    sentences.append(stripped)

        if not sentences:
            # Fallback: just return the content as-is
            return [content]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence)

            # Handle oversized sentences that exceed max_chunk on their own
            if sentence_len > max_chunk:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                # Hard-split the oversized sentence at max_chunk boundaries
                for i in range(0, sentence_len, max_chunk):
                    chunks.append(sentence[i : i + max_chunk])
                continue

            # If adding this sentence would exceed max_chunk, start a new chunk
            if current_len + sentence_len + 1 > max_chunk and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(sentence)
            current_len += sentence_len + 1  # +1 for space

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks if chunks else [content]
