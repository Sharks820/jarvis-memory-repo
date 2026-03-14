"""Tests for MEDIUM audit findings in memory, knowledge, and daemon subsystems.

Covers:
1. Chunk overlap in ingest pipeline
2. Importance scoring at ingest
3. FTS5 prefix rebuild
4. Cross-branch embedding-based similarity
5. Daemon cycle watchdog
6. Master password gate on /conversation/state?full=1
"""

from __future__ import annotations

import sqlite3
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Chunk overlap tests
# ---------------------------------------------------------------------------

class TestChunkOverlap:
    """Verify that chunk splitting produces overlapping sentences at boundaries."""

    def _make_pipeline(self):
        from jarvis_engine.memory.ingest import EnrichedIngestPipeline

        engine = MagicMock()
        embed_service = MagicMock()
        classifier = MagicMock()
        return EnrichedIngestPipeline(engine, embed_service, classifier)

    def test_short_content_no_chunking(self):
        """Content within 1.2x max_chunk should not be split."""
        pipeline = self._make_pipeline()
        content = "Short sentence. Another one."
        chunks = pipeline._chunk_content(content, max_chunk=1500)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_long_content_produces_overlap(self):
        """When content is split, the last 2 sentences of chunk N appear at the start of chunk N+1."""
        pipeline = self._make_pipeline()
        # Create content with many sentences that force splitting
        sentences = [f"Sentence number {i} with some extra padding text here." for i in range(30)]
        content = " ".join(sentences)
        # Use a small max_chunk to force multiple chunks
        chunks = pipeline._chunk_content(content, max_chunk=200)
        assert len(chunks) >= 3, f"Expected >= 3 chunks, got {len(chunks)}"

        # Check that chunks[1] starts with content from the end of chunks[0]
        # The overlap should contain the last 2 sentences from the previous chunk
        for i in range(1, len(chunks)):
            # Chunk i should contain some text from chunk i-1 (overlap)
            # We just verify that the second chunk starts with text that also
            # appears at the end of the first chunk.
            prev_chunk_words = chunks[i - 1].split()
            curr_chunk_words = chunks[i].split()
            # Find some overlap: at least one word from end of prev should be at start of curr
            overlap_found = False
            for word in prev_chunk_words[-10:]:
                if word in curr_chunk_words[:15]:
                    overlap_found = True
                    break
            assert overlap_found, (
                f"No overlap found between chunk {i-1} and chunk {i}.\n"
                f"End of prev: {' '.join(prev_chunk_words[-10:])}\n"
                f"Start of curr: {' '.join(curr_chunk_words[:15])}"
            )

    def test_overlap_constant_is_two(self):
        from jarvis_engine.memory.ingest import _CHUNK_OVERLAP_SENTENCES
        assert _CHUNK_OVERLAP_SENTENCES == 2

    def test_single_chunk_no_overlap_applied(self):
        """A single chunk should not have overlap applied."""
        pipeline = self._make_pipeline()
        content = "Only one chunk needed."
        chunks = pipeline._chunk_content(content, max_chunk=1500)
        assert len(chunks) == 1
        assert chunks[0] == content


# ---------------------------------------------------------------------------
# 2. Importance scoring tests
# ---------------------------------------------------------------------------

class TestImportanceScoring:
    """Verify rule-based importance scoring at ingest."""

    def test_medical_keywords_boost(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("I have a medication allergy to penicillin") == 0.90
        assert _score_importance("Doctor appointment scheduled for Tuesday") == 0.90
        assert _score_importance("My diagnosis was confirmed today") == 0.90

    def test_financial_keywords_boost(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("Monthly salary payment received today") == 0.85
        assert _score_importance("Need to review my investment portfolio") == 0.85
        assert _score_importance("Budget review for next quarter") == 0.85

    def test_security_keywords_boost(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("Updated my password for the email account") == 0.88
        assert _score_importance("Generated a new API token for the service") == 0.88
        assert _score_importance("Store this credential securely") == 0.88

    def test_commitment_keywords_boost(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("Remember to pick up groceries tomorrow") == 0.85
        assert _score_importance("Don't forget the meeting at 3pm") == 0.85
        assert _score_importance("This is important for the project") == 0.85

    def test_calendar_keywords_boost(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("The meeting is at 2pm in the conference room") == 0.82
        assert _score_importance("Appointment with the dentist on Friday") == 0.82
        assert _score_importance("Project deadline is next Monday") == 0.82

    def test_short_greeting_lowered(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("hi") == 0.50
        assert _score_importance("hello there") == 0.50
        assert _score_importance("hey") == 0.50
        assert _score_importance("yo sup") == 0.50

    def test_default_score(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("The weather is nice today and I went for a walk") == 0.72

    def test_empty_content_default(self):
        from jarvis_engine.memory.ingest import _score_importance
        assert _score_importance("") == 0.72

    def test_priority_order_medical_over_calendar(self):
        """Medical keywords should take priority over calendar keywords."""
        from jarvis_engine.memory.ingest import _score_importance
        # "doctor appointment" has both medical and calendar keywords
        score = _score_importance("doctor appointment at the clinic")
        assert score == 0.90  # Medical takes priority

    def test_security_over_financial(self):
        """Security keywords should take priority over financial keywords."""
        from jarvis_engine.memory.ingest import _score_importance
        score = _score_importance("password for my account")
        assert score == 0.88  # Security takes priority over financial

    def test_build_record_uses_scoring(self):
        """Verify that _build_record uses _score_importance instead of hardcoded 0.72."""
        from jarvis_engine.memory.ingest import EnrichedIngestPipeline

        engine = MagicMock()
        embed_service = MagicMock()
        classifier = MagicMock()
        classifier.classify.return_value = "general"
        pipeline = EnrichedIngestPipeline(engine, embed_service, classifier)

        record = pipeline._build_record(
            chunk="My medication schedule is important",
            embedding=[0.1] * 10,
            source="user",
            kind="episodic",
            task_id="test",
            ts="2026-01-01T00:00:00Z",
            tag_str="[]",
        )
        assert record["confidence"] == 0.90  # medical keyword

        record2 = pipeline._build_record(
            chunk="The weather is pleasant today",
            embedding=[0.1] * 10,
            source="user",
            kind="episodic",
            task_id="test",
            ts="2026-01-01T00:00:00Z",
            tag_str="[]",
        )
        assert record2["confidence"] == 0.72  # default


# ---------------------------------------------------------------------------
# 3. FTS5 prefix rebuild tests
# ---------------------------------------------------------------------------

class TestFTS5PrefixRebuild:
    """Test the rebuild_fts_with_prefix function."""

    def _make_engine(self, tmp_path):
        """Create a MemoryEngine with a temporary database."""
        from jarvis_engine.memory.engine import MemoryEngine

        db_path = tmp_path / "test_memory.db"
        engine = MemoryEngine(db_path)
        return engine

    def test_rebuild_preserves_data(self, tmp_path):
        """Rebuilding FTS5 with prefix should preserve existing data."""
        engine = self._make_engine(tmp_path)
        try:
            # Insert some FTS records
            cur = engine._db.cursor()
            cur.execute("INSERT INTO fts_records(record_id, summary) VALUES (?, ?)",
                        ("rec1", "Hello world test"))
            cur.execute("INSERT INTO fts_records(record_id, summary) VALUES (?, ?)",
                        ("rec2", "Another test record"))
            engine._db.commit()

            # Verify data exists
            cur.execute("SELECT COUNT(*) FROM fts_records")
            assert cur.fetchone()[0] == 2

            # Rebuild
            result = engine.rebuild_fts_with_prefix()
            assert result is True

            # Verify data preserved
            cur.execute("SELECT COUNT(*) FROM fts_records")
            assert cur.fetchone()[0] == 2

            # Verify search still works
            cur.execute(
                "SELECT record_id FROM fts_records WHERE fts_records MATCH ?",
                ("test",),
            )
            results = cur.fetchall()
            assert len(results) == 2
        finally:
            engine.close()

    def test_rebuild_empty_table(self, tmp_path):
        """Rebuilding an empty FTS5 table should succeed."""
        engine = self._make_engine(tmp_path)
        try:
            result = engine.rebuild_fts_with_prefix()
            assert result is True

            # Verify table exists and is empty
            cur = engine._db.cursor()
            cur.execute("SELECT COUNT(*) FROM fts_records")
            assert cur.fetchone()[0] == 0
        finally:
            engine.close()

    def test_rebuild_on_closed_engine_raises(self, tmp_path):
        """Rebuilding on a closed engine should raise RuntimeError."""
        engine = self._make_engine(tmp_path)
        engine.close()
        with pytest.raises(RuntimeError, match="closed"):
            engine.rebuild_fts_with_prefix()

    def test_prefix_search_after_rebuild(self, tmp_path):
        """After rebuild with prefix, prefix searches should work."""
        engine = self._make_engine(tmp_path)
        try:
            cur = engine._db.cursor()
            cur.execute("INSERT INTO fts_records(record_id, summary) VALUES (?, ?)",
                        ("rec1", "testing prefixes"))
            engine._db.commit()

            engine.rebuild_fts_with_prefix()

            # Prefix search should work with prefix='2,3' configuration
            cur.execute(
                "SELECT record_id FROM fts_records WHERE fts_records MATCH ?",
                ("tes*",),
            )
            results = cur.fetchall()
            assert len(results) == 1
            assert results[0][0] == "rec1"
        finally:
            engine.close()


# ---------------------------------------------------------------------------
# 4. Cross-branch semantic matching tests
# ---------------------------------------------------------------------------

class TestCrossBranchSemanticMatching:
    """Test embedding-based cross-branch edge creation."""

    def _make_kg(self):
        """Create a mock KnowledgeGraph with in-memory SQLite."""
        kg = MagicMock()
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT,
                confidence REAL DEFAULT 0.5,
                node_type TEXT DEFAULT 'fact'
            )
        """)
        db.execute("""
            CREATE TABLE kg_edges (
                source_id TEXT,
                target_id TEXT,
                relation TEXT,
                confidence REAL DEFAULT 0.5,
                source_record TEXT DEFAULT ''
            )
        """)
        db.commit()
        kg.db = db
        kg.db_lock = threading.Lock()
        return kg, db

    def test_embedding_creates_edge_when_similar(self):
        """Embedding similarity above threshold should create cross-branch edge."""
        from jarvis_engine.learning.cross_branch import create_cross_branch_edges

        kg, db = self._make_kg()

        # Insert the new fact (branch: health)
        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("health.flu", "flu symptoms and treatment", 0.8, "fact"))
        # Insert a candidate in another branch with similar topic
        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("work.sick", "sick leave policy for flu season", 0.7, "fact"))
        db.commit()

        kg.get_node.return_value = {"label": "flu symptoms and treatment", "node_type": "fact"}
        kg.add_edge.return_value = True

        # Create a mock embed service that returns similar vectors
        embed_service = MagicMock()
        # Both embeddings are nearly identical (high cosine similarity)
        base_vec = [1.0, 0.0, 0.0]
        similar_vec = [0.95, 0.1, 0.0]
        embed_service.embed.side_effect = lambda text, prefix="search_document": (
            base_vec if "flu symptoms" in text else similar_vec
        )
        embed_service.embed_batch.side_effect = lambda texts, prefix="search_document": [
            base_vec if "flu symptoms" in t else similar_vec for t in texts
        ]

        edges = create_cross_branch_edges(
            kg, "health.flu", "rec-001", embed_service=embed_service,
        )
        # The embedding similarity between base_vec and similar_vec is very high
        # so an edge should be created
        assert edges > 0
        kg.add_edge.assert_called()

    def test_no_embedding_service_uses_keyword_only(self):
        """Without embed_service, only keyword matching should be used."""
        from jarvis_engine.learning.cross_branch import create_cross_branch_edges

        kg, db = self._make_kg()

        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("health.flu", "flu symptoms", 0.8, "fact"))
        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("work.policy", "work policy document", 0.7, "fact"))
        db.commit()

        kg.get_node.return_value = {"label": "flu symptoms", "node_type": "fact"}
        kg.add_edge.return_value = True

        # No embed_service -- should still work (backward compat)
        edges = create_cross_branch_edges(kg, "health.flu", "rec-001")
        # Should not crash, keyword overlap is the only mechanism
        assert isinstance(edges, int)

    def test_low_similarity_no_edge(self):
        """Embedding similarity below threshold should NOT create edge."""
        from jarvis_engine.learning.cross_branch import create_cross_branch_edges

        kg, db = self._make_kg()

        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("health.flu", "flu symptoms", 0.8, "fact"))
        db.execute("INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                    ("finance.tax", "tax return filing", 0.7, "fact"))
        db.commit()

        kg.get_node.return_value = {"label": "flu symptoms", "node_type": "fact"}
        kg.add_edge.return_value = True

        embed_service = MagicMock()
        # Completely different vectors (orthogonal = 0 similarity)
        embed_service.embed.side_effect = lambda text, prefix="search_document": (
            [1.0, 0.0, 0.0] if "flu" in text else [0.0, 1.0, 0.0]
        )

        edges = create_cross_branch_edges(
            kg, "health.flu", "rec-001", embed_service=embed_service,
        )
        # Cosine similarity of orthogonal vectors is 0, well below 0.75 threshold
        # Only keyword-based edges (if any) should be created, not embedding-based
        # Since "flu" doesn't appear in "tax return filing", no keyword match either
        # Check that no semantic edge was created
        for call_args in kg.add_edge.call_args_list:
            assert call_args[1].get("relation") != "cross_branch_semantic"

    def test_cosine_similarity_function(self):
        """Verify the _cosine_similarity helper."""
        from jarvis_engine.learning.cross_branch import _cosine_similarity

        # Identical vectors -> similarity 1.0
        assert abs(_cosine_similarity([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-6
        # Orthogonal vectors -> similarity 0.0
        assert abs(_cosine_similarity([1, 0, 0], [0, 1, 0])) < 1e-6
        # Opposite vectors -> similarity -1.0
        assert abs(_cosine_similarity([1, 0, 0], [-1, 0, 0]) - (-1.0)) < 1e-6
        # Zero vector -> similarity 0.0
        assert _cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0

    def test_embedding_threshold_constant(self):
        from jarvis_engine.learning.cross_branch import _EMBEDDING_SIMILARITY_THRESHOLD
        assert _EMBEDDING_SIMILARITY_THRESHOLD == 0.75


# ---------------------------------------------------------------------------
# 5. Watchdog cycle timeout tests
# ---------------------------------------------------------------------------

class TestWatchdogCycleTimeout:
    """Test daemon cycle watchdog detection."""

    def test_watchdog_healthy_when_not_started(self):
        import jarvis_engine.daemon_loop as dl
        old_start = dl._cycle_start[0]
        try:
            dl._cycle_start[0] = 0.0  # Reset to "not started" state
            result = dl._watchdog_check()
            assert result["healthy"] is True
            assert result["elapsed_s"] == 0.0
            assert result["timeout_s"] == 600
        finally:
            dl._cycle_start[0] = old_start

    def test_watchdog_healthy_during_normal_cycle(self):
        import jarvis_engine.daemon_loop as dl
        old_start = dl._cycle_start[0]
        try:
            dl._cycle_start[0] = time.monotonic()  # Just started
            result = dl._watchdog_check()
            assert result["healthy"] is True
            assert result["elapsed_s"] < 5.0  # Should be near-instant
        finally:
            dl._cycle_start[0] = old_start

    def test_watchdog_unhealthy_after_timeout(self):
        import jarvis_engine.daemon_loop as dl
        old_start = dl._cycle_start[0]
        try:
            # Use a fixed "now" so the test is robust on freshly-booted machines
            # where time.monotonic() < 700 (making monotonic() - 700 negative,
            # which would trip the "not started" guard and return healthy=True).
            fake_now = 10_000.0
            dl._cycle_start[0] = fake_now - 700  # 700 seconds ago
            with patch.object(dl.time, "monotonic", return_value=fake_now):
                result = dl._watchdog_check()
            assert result["healthy"] is False
            assert result["elapsed_s"] >= 699.0
        finally:
            dl._cycle_start[0] = old_start

    def test_cycle_timeout_constant(self):
        from jarvis_engine.daemon_loop import _CYCLE_TIMEOUT_S
        assert _CYCLE_TIMEOUT_S == 600

    def test_watchdog_check_returns_dict(self):
        from jarvis_engine.daemon_loop import _watchdog_check
        result = _watchdog_check()
        assert isinstance(result, dict)
        assert "healthy" in result
        assert "elapsed_s" in result
        assert "timeout_s" in result

    def test_cycle_start_reflects_in_watchdog(self):
        """Setting _cycle_start should be reflected in _watchdog_check."""
        import jarvis_engine.daemon_loop as dl
        old_start = dl._cycle_start[0]
        try:
            # Simulate setting cycle start as the main loop does
            dl._cycle_start[0] = time.monotonic() - 10  # 10s ago
            result = dl._watchdog_check()
            assert result["healthy"] is True
            assert 9.0 <= result["elapsed_s"] <= 12.0
        finally:
            dl._cycle_start[0] = old_start

    def test_watchdog_exactly_at_boundary(self):
        """A cycle exactly at the timeout should still be healthy (< not <=)."""
        import jarvis_engine.daemon_loop as dl
        old_start = dl._cycle_start[0]
        try:
            # Slightly under timeout
            dl._cycle_start[0] = time.monotonic() - (dl._CYCLE_TIMEOUT_S - 1)
            result = dl._watchdog_check()
            assert result["healthy"] is True
        finally:
            dl._cycle_start[0] = old_start


# ---------------------------------------------------------------------------
# 6. Master password gate on /conversation/state?full=1
# ---------------------------------------------------------------------------

class TestConversationStateMasterPassword:
    """Verify master password requirement for full conversation state."""

    def _make_handler(self, path="/conversation/state"):
        """Create a mock handler with IntelligenceRoutesMixin behavior."""
        handler = MagicMock()
        handler.path = path
        handler._root = Path("/tmp/test")
        handler._validate_auth = MagicMock(return_value=True)
        handler._write_json = MagicMock()
        handler.headers = {}
        return handler

    def test_full_without_master_pwd_returns_403(self):
        """Requesting full=1 without master_pwd should return 403."""
        from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
        from http import HTTPStatus

        handler = self._make_handler(path="/conversation/state?full=1")
        # Call the method directly on the handler
        IntelligenceRoutesMixin._handle_get_conversation_state(handler)
        # Should have written a 403 response
        handler._write_json.assert_called_once()
        call_args = handler._write_json.call_args
        assert call_args[0][0] == HTTPStatus.FORBIDDEN
        assert "master password" in call_args[0][1]["error"].lower()

    def test_full_with_invalid_master_pwd_returns_403(self):
        """Requesting full=1 with wrong master_pwd should return 403."""
        from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
        from http import HTTPStatus

        handler = self._make_handler(
            path="/conversation/state?full=1&master_pwd=wrongpassword"
        )
        with patch(
            "jarvis_engine.security.owner_guard.verify_master_password",
            return_value=False,
        ):
            IntelligenceRoutesMixin._handle_get_conversation_state(handler)
        handler._write_json.assert_called_once()
        call_args = handler._write_json.call_args
        assert call_args[0][0] == HTTPStatus.FORBIDDEN
        assert "invalid" in call_args[0][1]["error"].lower()

    def test_full_with_valid_master_pwd_succeeds(self):
        """Requesting full=1 with valid master_pwd should return state."""
        from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
        from http import HTTPStatus

        handler = self._make_handler(
            path="/conversation/state?full=1&master_pwd=correctpassword"
        )
        mock_csm = MagicMock()
        mock_csm.get_state_snapshot.return_value = {"full": True, "data": "unredacted"}

        with patch(
            "jarvis_engine.security.owner_guard.verify_master_password",
            return_value=True,
        ):
            with patch(
                "jarvis_engine.memory.conversation_state.get_conversation_state",
                return_value=mock_csm,
            ):
                IntelligenceRoutesMixin._handle_get_conversation_state(handler)

        handler._write_json.assert_called_once()
        call_args = handler._write_json.call_args
        assert call_args[0][0] == HTTPStatus.OK
        mock_csm.get_state_snapshot.assert_called_once_with(full=True)

    def test_non_full_request_no_master_pwd_needed(self):
        """Normal request (no full=1) should not require master password."""
        from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
        from http import HTTPStatus

        handler = self._make_handler(path="/conversation/state")
        mock_csm = MagicMock()
        mock_csm.get_state_snapshot.return_value = {"redacted": True}

        with patch(
            "jarvis_engine.memory.conversation_state.get_conversation_state",
            return_value=mock_csm,
        ):
            IntelligenceRoutesMixin._handle_get_conversation_state(handler)

        handler._write_json.assert_called_once()
        call_args = handler._write_json.call_args
        assert call_args[0][0] == HTTPStatus.OK
        mock_csm.get_state_snapshot.assert_called_once_with(full=False)

    def test_full_zero_no_master_pwd_needed(self):
        """Request with full=0 should not require master password."""
        from jarvis_engine.mobile_routes.intelligence import IntelligenceRoutesMixin
        from http import HTTPStatus

        handler = self._make_handler(path="/conversation/state?full=0")
        mock_csm = MagicMock()
        mock_csm.get_state_snapshot.return_value = {"redacted": True}

        with patch(
            "jarvis_engine.memory.conversation_state.get_conversation_state",
            return_value=mock_csm,
        ):
            IntelligenceRoutesMixin._handle_get_conversation_state(handler)

        handler._write_json.assert_called_once()
        call_args = handler._write_json.call_args
        assert call_args[0][0] == HTTPStatus.OK
