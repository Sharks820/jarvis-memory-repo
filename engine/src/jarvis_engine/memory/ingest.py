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
from typing import TYPE_CHECKING, TypedDict

from jarvis_engine._shared import now_iso as _now_iso, sha256_hex, sha256_short
from jarvis_engine.learning.provenance import LearningProvenanceStore
from jarvis_engine.learning.trust import (
    artifact_requires_quarantine,
    classify_learning_subject,
    detect_threat_indicators,
    safe_artifact_summary,
)

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

# Number of sentences from the end of chunk N to prepend to chunk N+1.
# Ensures information spanning chunk boundaries isn't lost during retrieval.
_CHUNK_OVERLAP_SENTENCES = 2


class RecordDict(TypedDict):
    """Memory record dict built by :meth:`EnrichedIngestPipeline._build_record`."""

    record_id: str
    ts: str
    source: str
    kind: str
    task_id: str
    branch: str
    tags: str
    summary: str
    content_hash: str
    confidence: float
    tier: str
    access_count: int
    last_accessed: str


# ---------------------------------------------------------------------------
# Rule-based importance scoring
# ---------------------------------------------------------------------------

_IMPORTANCE_RULES: list[tuple[list[str], float]] = [
    # (keywords, score) — first match wins, order matters
    (
        [
            "medication",
            "prescription",
            "allergy",
            "diagnosis",
            "doctor",
            "medical",
            "health",
        ],
        0.90,
    ),
    (["password", "key", "credential", "token", "secret"], 0.88),
    (["payment", "salary", "investment", "account", "budget", "financial"], 0.85),
    (["remember", "don't forget", "dont forget", "important"], 0.85),
    (["meeting", "appointment", "deadline", "due", "calendar", "schedule"], 0.82),
]

_GREETING_WORDS = {"hi", "hello", "hey", "yo", "sup", "howdy", "hiya", "greetings"}

_DEFAULT_IMPORTANCE = 0.72


def _score_importance(content: str) -> float:
    """Return a rule-based importance score for *content*.

    Checks keyword categories in priority order (medical > security > financial
    > commitments > calendar).  Short greetings are down-scored.  Default 0.72.
    """
    if not content:
        return _DEFAULT_IMPORTANCE

    lowered = content.lower()

    # Short greetings / small talk
    if len(content) < 20:
        words = set(lowered.split())
        if words & _GREETING_WORDS:
            return 0.50

    # Category keyword matching (first match wins)
    for keywords, score in _IMPORTANCE_RULES:
        for kw in keywords:
            if kw in lowered:
                return score

    return _DEFAULT_IMPORTANCE


# Patterns for credential redaction (matches password, token, secret, api_key, etc.)
_CREDENTIAL_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(
        r"(token|api[_-]?key|secret|signing[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE
    ),
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
        self._provenance_store = LearningProvenanceStore(
            db=engine.db,
            write_lock=engine.write_lock,
            db_lock=engine.db_lock,
        )

    def set_gateway(self, gateway: "object | None") -> None:
        """Set the LLM gateway for fact extraction (late-binding).

        Called by the composition root when the gateway is created after the
        pipeline.  Avoids direct mutation of the private ``_gateway`` attribute.
        """
        self._gateway = gateway

    def _embed_chunks(self, valid_chunks: list[str]) -> list:
        """Generate embeddings for all chunks, preferring batch mode.

        Falls back to individual embedding calls if batch embedding fails.
        """
        if hasattr(self._embed_service, "embed_batch") and len(valid_chunks) > 1:
            try:
                return self._embed_service.embed_batch(
                    valid_chunks, prefix="search_document"
                )
            except (RuntimeError, OSError, ValueError) as exc:
                logger.debug(
                    "Batch embedding failed, falling back to individual: %s", exc
                )
        return [
            self._embed_service.embed(chunk, prefix="search_document")
            for chunk in valid_chunks
        ]

    @staticmethod
    def _build_summary(chunk: str) -> str:
        """Build a summary with word-boundary truncation (max 200 chars)."""
        summary = chunk[:200]
        if len(chunk) > 200:
            last_space = summary.rfind(" ")
            if last_space > 100:
                summary = summary[:last_space]
        return summary

    def _build_record(
        self,
        chunk: str,
        embedding: object,
        source: str,
        kind: str,
        task_id: str,
        ts: str,
        tag_str: str,
    ) -> RecordDict:
        """Build a single memory record dict from a chunk and its embedding."""
        chunk_hash = sha256_hex(chunk)
        id_material = f"{source}|{kind}|{task_id}|{chunk}".encode("utf-8")
        record_id = sha256_short(id_material)
        branch = self._classifier.classify(embedding)
        summary = self._build_summary(chunk)

        return {
            "record_id": record_id,
            "ts": ts,
            "source": source,
            "kind": kind,
            "task_id": task_id,
            "branch": branch,
            "tags": tag_str,
            "summary": summary,
            "content_hash": chunk_hash,
            "confidence": _score_importance(chunk),
            "tier": "warm",
            "access_count": 0,
            "last_accessed": "",
        }

    def _extract_all_facts(
        self,
        chunk: str,
        source: str,
        branch: str,
        record_id: str,
    ) -> None:
        """Extract facts into KG using both regex and LLM extractors.

        Failures are logged but never propagated -- the record is already stored.
        """
        if self._kg is None:
            return

        try:
            self._extract_facts(chunk, source, branch, record_id)
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            logger.warning(
                "Fact extraction failed for record %s: %s",
                record_id,
                exc,
            )

        try:
            llm_extractor = self._get_llm_extractor()
            if llm_extractor:
                llm_facts = llm_extractor.extract_facts(chunk, branch=branch)
                for fact in llm_facts:
                    node_id = f"llm:{hashlib.sha256(f'{fact.entity}.{fact.relationship}.{fact.value}'.encode()).hexdigest()[:16]}"
                    self._kg.add_fact(
                        node_id=node_id,
                        label=f"{fact.entity}: {fact.value}",
                        confidence=fact.confidence,
                        source_record=record_id,
                        node_type=fact.category or "fact",
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("LLM fact extraction failed: %s", exc)

    def ingest(
        self,
        source: str,
        kind: str,
        task_id: str,
        content: str,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Ingest content: sanitize -> dedup -> chunk -> embed -> classify -> store.

        Returns list of inserted record IDs (empty if all duplicates).
        """
        # Step 1: Sanitize
        sanitized = self._sanitize(content)
        if not sanitized:
            return []

        # Step 2: Pre-flight dedup for short content
        full_content_hash = sha256_hex(sanitized)
        chunks = self._chunk_content(sanitized)
        if len(chunks) == 1:
            existing = self._engine.get_record_by_hash(full_content_hash)
            if existing is not None:
                return []

        # Step 3: Prepare metadata
        ts = _now_iso()
        tag_list = sorted({t.lower() for t in (tags or []) if t.strip()})[:10]
        tag_str = json.dumps(tag_list)

        # Step 4: Filter and embed
        valid_chunks = [chunk for chunk in chunks if chunk.strip()]
        if not valid_chunks:
            return []
        all_embeddings = self._embed_chunks(valid_chunks)

        # Step 5: Store each chunk and extract facts
        inserted_ids: list[str] = []
        for chunk, embedding in zip(valid_chunks, all_embeddings):
            record = self._build_record(
                chunk,
                embedding,
                source,
                kind,
                task_id,
                ts,
                tag_str,
            )
            was_inserted = self._engine.insert_record(record, embedding=embedding)
            if was_inserted:
                record_id = record["record_id"]
                inserted_ids.append(record_id)
                provenance = classify_learning_subject(
                    subject_type="memory_record",
                    subject_id=record_id,
                    source_channel=source,
                    content=chunk,
                    tags=tag_list,
                    mission_id=task_id,
                )
                self._provenance_store.record_subject(
                    subject_type="memory_record",
                    subject_id=record_id,
                    metadata=provenance,
                )
                self._provenance_store.record_policy_event(
                    subject_type="memory_record",
                    subject_id=record_id,
                    action="observe",
                    verdict=provenance["promotion_state"],
                    policy_mode=provenance["policy_mode"],
                    reason="phase_14_09a_dual_write",
                    metadata={
                        "source": source,
                        "kind": kind,
                        "artifact_kind": provenance["artifact_kind"],
                    },
                )
                if artifact_requires_quarantine(provenance):
                    summary = safe_artifact_summary(chunk)
                    self._provenance_store.quarantine_artifact(
                        subject_type="memory_record",
                        subject_id=record_id,
                        source_hash=provenance["source_hash"],
                        source_channel=provenance["source_channel"],
                        artifact_kind=provenance["artifact_kind"],
                        safe_summary=summary,
                        quarantine_reason="shadow_artifact_observed",
                        metadata={
                            "policy_mode": provenance["policy_mode"],
                            "mission_id": task_id,
                        },
                        raw_preview=summary,
                    )
                    self._provenance_store.record_policy_event(
                        subject_type="memory_record",
                        subject_id=record_id,
                        action="shadow_quarantine",
                        verdict="quarantined",
                        policy_mode=provenance["policy_mode"],
                        reason="artifact_requires_verification",
                        metadata={"artifact_kind": provenance["artifact_kind"]},
                    )
                for indicator in detect_threat_indicators(chunk, tag_list):
                    self._provenance_store.record_threat_indicator(
                        indicator_type=indicator,
                        indicator_value=record_id,
                        subject_type="memory_record",
                        subject_id=record_id,
                        source_hash=provenance["source_hash"],
                        reason="artifact_signal_detected",
                        metadata={"source": source, "kind": kind},
                    )
                    self._provenance_store.record_policy_event(
                        subject_type="memory_record",
                        subject_id=record_id,
                        action="threat_indicator",
                        verdict=indicator,
                        policy_mode=provenance["policy_mode"],
                        reason="deterministic_pattern_match",
                        metadata={"indicator": indicator},
                    )
                self._extract_all_facts(
                    chunk,
                    source,
                    record["branch"],
                    record_id,
                )

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
            logger.debug(
                "LLMFactExtractor unavailable (knowledge.llm_extractor not installed)"
            )
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
        except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
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
        # Track sentences per chunk for overlap computation
        chunk_sentences: list[list[str]] = []

        for sentence in sentences:
            sentence_len = len(sentence)

            # Handle oversized sentences that exceed max_chunk on their own
            if sentence_len > max_chunk:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    chunk_sentences.append(list(current_chunk))
                    current_chunk = []
                    current_len = 0
                # Hard-split the oversized sentence at max_chunk boundaries
                for i in range(0, sentence_len, max_chunk):
                    chunks.append(sentence[i : i + max_chunk])
                    chunk_sentences.append(
                        []
                    )  # no sentence-level overlap for hard-splits
                continue

            # If adding this sentence would exceed max_chunk, start a new chunk
            if current_len + sentence_len + 1 > max_chunk and current_chunk:
                chunks.append(" ".join(current_chunk))
                chunk_sentences.append(list(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(sentence)
            current_len += sentence_len + 1  # +1 for space

        if current_chunk:
            chunks.append(" ".join(current_chunk))
            chunk_sentences.append(list(current_chunk))

        if not chunks:
            return [content]

        # Apply overlap: prepend last N sentences of chunk i to chunk i+1
        if _CHUNK_OVERLAP_SENTENCES > 0 and len(chunk_sentences) > 1:
            overlapped: list[str] = [chunks[0]]
            for i in range(1, len(chunk_sentences)):
                prev_sents = chunk_sentences[i - 1]
                cur_sents = chunk_sentences[i]
                if prev_sents and cur_sents:
                    overlap = prev_sents[-_CHUNK_OVERLAP_SENTENCES:]
                    merged = overlap + cur_sents
                    overlapped.append(" ".join(merged))
                else:
                    overlapped.append(chunks[i] if i < len(chunks) else "")
            return [c for c in overlapped if c]

        return chunks
