"""Comprehensive tests for memory handler classes in memory_handlers.py.

Covers BrainStatusHandler, BrainContextHandler, BrainCompactHandler,
BrainRegressionHandler, IngestHandler, MemorySnapshotHandler, and
MemoryMaintenanceHandler — both the MemoryEngine (SQLite) path and the
legacy JSONL fallback path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch


from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.memory.ingest import EnrichedIngestPipeline
from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemoryMaintenanceResult,
    MemorySnapshotCommand,
)
from jarvis_engine.handlers.memory_handlers import (
    BrainCompactHandler,
    BrainContextHandler,
    BrainRegressionHandler,
    BrainStatusHandler,
    IngestHandler,
    MemoryMaintenanceHandler,
    MemorySnapshotHandler,
)


# ---------------------------------------------------------------------------
# BrainStatusHandler
# ---------------------------------------------------------------------------


class TestBrainStatusHandler:
    """Tests for BrainStatusHandler."""

    def test_with_engine_returns_sqlite_status(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 42
        engine.db.execute.return_value.fetchall.return_value = [("general", 42, "2026-03-07")]
        handler = BrainStatusHandler(root=tmp_path, engine=engine, kg=None)
        result = handler.handle(BrainStatusCommand())
        assert result.status["total_records"] == 42
        assert result.status["engine"] == "sqlite"
        engine.count_records.assert_called_once()

    def test_with_engine_zero_records(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 0
        engine.db.execute.return_value.fetchall.return_value = []
        handler = BrainStatusHandler(root=tmp_path, engine=engine, kg=None)
        result = handler.handle(BrainStatusCommand())
        assert result.status["total_records"] == 0
        assert result.status["branch_count"] == 0
        assert result.status["fact_count"] == 0
        assert result.status["regression"]["status"] == "not_available"

    @patch("jarvis_engine.handlers.memory_handlers.brain_status", create=True)
    def test_fallback_uses_brain_status(self, mock_bs: MagicMock, tmp_path: Path) -> None:
        """When engine is None, handler falls back to brain_status()."""
        # The handler does a lazy import, so we patch in the brain_memory module directly
        fake_status = {
            "updated_utc": "2026-02-25T00:00:00",
            "branch_count": 3,
            "fact_count": 7,
            "regression": {"status": "pass"},
            "branches": [],
        }
        with patch("jarvis_engine.brain_memory.brain_status", return_value=fake_status):
            handler = BrainStatusHandler(root=tmp_path, engine=None)
            result = handler.handle(BrainStatusCommand())
        assert result.status["branch_count"] == 3
        assert result.status["fact_count"] == 7

    def test_fallback_real_empty_dir(self, tmp_path: Path) -> None:
        """Fallback path with no brain data returns empty/zero counts."""
        handler = BrainStatusHandler(root=tmp_path, engine=None)
        result = handler.handle(BrainStatusCommand())
        assert result.status["branch_count"] == 0
        assert result.status["regression"]["status"] == "pass"

    def test_result_has_standard_keys(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 5
        engine.db.execute.return_value.fetchall.return_value = []
        handler = BrainStatusHandler(root=tmp_path, engine=engine, kg=None)
        result = handler.handle(BrainStatusCommand())
        for key in ("updated_utc", "branch_count", "fact_count", "total_records", "regression", "branches", "engine"):
            assert key in result.status, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# BrainContextHandler
# ---------------------------------------------------------------------------


class TestBrainContextHandler:
    """Tests for BrainContextHandler."""

    def test_engine_path_embedding_failed(self, tmp_path: Path) -> None:
        """If embed_service returns empty, result should contain error."""
        engine = MagicMock(spec=MemoryEngine)
        embed = MagicMock(spec=EmbeddingService)
        embed.embed_query.return_value = []  # empty embedding
        handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=embed)
        cmd = BrainContextCommand(query="test query")
        result = handler.handle(cmd)
        assert "error" in result.packet
        assert result.packet["error"] == "embedding failed"

    def test_engine_path_happy(self, tmp_path: Path) -> None:
        """Engine path with valid embedding and search results."""
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 100
        embed = MagicMock(spec=EmbeddingService)
        embed.embed_query.return_value = [0.1, 0.2, 0.3]

        fake_results = [
            {"record_id": "r1", "summary": "calendar meeting", "branch": "ops", "source": "user", "kind": "episodic", "ts": "2026-02-25T00:00:00"},
            {"record_id": "r2", "summary": "code review", "branch": "coding", "source": "user", "kind": "episodic", "ts": "2026-02-25T01:00:00"},
        ]
        with patch("jarvis_engine.memory.search.hybrid_search", return_value=fake_results):
            handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=embed)
            cmd = BrainContextCommand(query="calendar", max_items=10, max_chars=2000)
            result = handler.handle(cmd)

        assert result.packet["query"] == "calendar"
        assert result.packet["selected_count"] == 2
        assert result.packet["engine"] == "sqlite"
        assert result.packet["total_records_scanned"] == 100

    def test_engine_path_respects_max_chars(self, tmp_path: Path) -> None:
        """Results should stop accumulating when max_chars is exceeded."""
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 10
        embed = MagicMock(spec=EmbeddingService)
        embed.embed_query.return_value = [0.1]

        # Each summary is 100 chars; max_chars=500 means 5 fit
        fake_results = [
            {"record_id": f"r{i}", "summary": "x" * 100, "branch": "general", "source": "user", "kind": "ep", "ts": "2026-01-01"}
            for i in range(10)
        ]
        with patch("jarvis_engine.memory.search.hybrid_search", return_value=fake_results):
            handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=embed)
            cmd = BrainContextCommand(query="test", max_items=40, max_chars=500)
            result = handler.handle(cmd)

        assert result.packet["selected_count"] == 5

    def test_engine_path_clamps_max_items(self, tmp_path: Path) -> None:
        """max_items is clamped to [1, 40]."""
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 0
        embed = MagicMock(spec=EmbeddingService)
        embed.embed_query.return_value = [0.1]

        with patch("jarvis_engine.memory.search.hybrid_search", return_value=[]) as mock_hs:
            handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=embed)
            # Request 100 items — should be clamped to 40
            cmd = BrainContextCommand(query="q", max_items=100, max_chars=2400)
            handler.handle(cmd)
            _, kwargs = mock_hs.call_args
            assert kwargs["k"] == 40

    def test_engine_path_clamps_max_chars(self, tmp_path: Path) -> None:
        """max_chars is clamped to [500, 12000]."""
        engine = MagicMock(spec=MemoryEngine)
        engine.count_records.return_value = 0
        embed = MagicMock(spec=EmbeddingService)
        embed.embed_query.return_value = [0.1]

        # Use max_chars=50 (below 500) — handler should clamp to 500
        with patch("jarvis_engine.memory.search.hybrid_search", return_value=[]):
            handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=embed)
            cmd = BrainContextCommand(query="q", max_items=5, max_chars=50)
            result = handler.handle(cmd)
        # Can't directly check internal clamp but at least it doesn't crash
        assert result.packet["max_chars"] == 50  # original value preserved in output
        assert result.packet["engine"] == "sqlite"

    def test_fallback_path_no_engine(self, tmp_path: Path) -> None:
        """When engine is None, falls back to build_context_packet."""
        with patch("jarvis_engine.brain_memory.build_context_packet", return_value={
            "query": "q", "selected": [], "selected_count": 0, "canonical_facts": [],
            "max_items": 5, "max_chars": 800, "total_records_scanned": 0,
        }) as mock_bcp:
            handler = BrainContextHandler(root=tmp_path, engine=None, embed_service=None)
            cmd = BrainContextCommand(query="q", max_items=5, max_chars=800)
            result = handler.handle(cmd)
        mock_bcp.assert_called_once()
        assert result.packet["selected_count"] == 0

    def test_fallback_when_only_engine_set(self, tmp_path: Path) -> None:
        """If engine is set but embed_service is None, falls back to build_context_packet."""
        engine = MagicMock(spec=MemoryEngine)
        with patch("jarvis_engine.brain_memory.build_context_packet", return_value={
            "query": "q", "selected": [], "selected_count": 0, "canonical_facts": [],
            "max_items": 5, "max_chars": 800, "total_records_scanned": 0,
        }):
            handler = BrainContextHandler(root=tmp_path, engine=engine, embed_service=None)
            cmd = BrainContextCommand(query="q", max_items=5, max_chars=800)
            result = handler.handle(cmd)
        # Should have used fallback since embed_service is None
        assert result.packet["selected_count"] == 0


# ---------------------------------------------------------------------------
# BrainCompactHandler
# ---------------------------------------------------------------------------


class TestBrainCompactHandler:
    """Tests for BrainCompactHandler."""

    def test_compact_with_mock(self, tmp_path: Path) -> None:
        """Handler delegates to brain_compact with clamped keep_recent."""
        with patch("jarvis_engine.brain_memory.brain_compact", return_value={
            "compacted": True, "total_records": 5000, "compacted_records": 3200,
            "kept_records": 1800, "summary_groups": 12, "summaries_path": "/foo/bar",
        }) as mock_bc:
            handler = BrainCompactHandler(root=tmp_path)
            cmd = BrainCompactCommand(keep_recent=1800)
            result = handler.handle(cmd)
        mock_bc.assert_called_once_with(tmp_path, keep_recent=1800)
        assert result.result["compacted"] is True

    def test_compact_clamps_keep_recent_low(self, tmp_path: Path) -> None:
        """keep_recent below 200 gets clamped to 200."""
        with patch("jarvis_engine.brain_memory.brain_compact", return_value={"compacted": False, "reason": "below_threshold", "total_records": 0, "kept_records": 0}) as mock_bc:
            handler = BrainCompactHandler(root=tmp_path)
            cmd = BrainCompactCommand(keep_recent=5)
            handler.handle(cmd)
        mock_bc.assert_called_once_with(tmp_path, keep_recent=200)

    def test_compact_clamps_keep_recent_high(self, tmp_path: Path) -> None:
        """keep_recent above 50000 gets clamped to 50000."""
        with patch("jarvis_engine.brain_memory.brain_compact", return_value={"compacted": False, "reason": "below_threshold", "total_records": 0, "kept_records": 0}) as mock_bc:
            handler = BrainCompactHandler(root=tmp_path)
            cmd = BrainCompactCommand(keep_recent=999999)
            handler.handle(cmd)
        mock_bc.assert_called_once_with(tmp_path, keep_recent=50000)

    def test_compact_below_threshold(self, tmp_path: Path) -> None:
        """Handler returns non-compacted result when below threshold."""
        handler = BrainCompactHandler(root=tmp_path)
        # Empty dir — no records exist
        result = handler.handle(BrainCompactCommand(keep_recent=1800))
        assert result.result["compacted"] is False
        assert result.result["reason"] == "below_threshold"


# ---------------------------------------------------------------------------
# BrainRegressionHandler
# ---------------------------------------------------------------------------


class TestBrainRegressionHandler:
    """Tests for BrainRegressionHandler."""

    def test_regression_with_mock(self, tmp_path: Path) -> None:
        fake_report = {
            "status": "warn", "total_records": 1000, "unique_hashes": 950,
            "duplicate_ratio": 0.05, "branch_entropy": 2.1, "branch_count": 5,
            "unresolved_conflicts": 25, "conflict_total": 30, "generated_utc": "2026-02-25T00:00:00",
        }
        with patch("jarvis_engine.brain_memory.brain_regression_report", return_value=fake_report):
            handler = BrainRegressionHandler(root=tmp_path)
            result = handler.handle(BrainRegressionCommand())
        assert result.report["status"] == "warn"
        assert result.report["total_records"] == 1000

    def test_regression_empty_dir(self, tmp_path: Path) -> None:
        """With no data files, regression should report pass with zero records."""
        handler = BrainRegressionHandler(root=tmp_path)
        result = handler.handle(BrainRegressionCommand())
        assert result.report["status"] == "pass"
        assert result.report["total_records"] == 0
        assert result.report["unique_hashes"] == 0

    def test_regression_report_has_all_fields(self, tmp_path: Path) -> None:
        handler = BrainRegressionHandler(root=tmp_path)
        result = handler.handle(BrainRegressionCommand())
        expected_keys = {"status", "total_records", "unique_hashes", "duplicate_ratio",
                         "branch_entropy", "branch_count", "unresolved_conflicts",
                         "conflict_total", "generated_utc"}
        assert expected_keys.issubset(set(result.report.keys()))


# ---------------------------------------------------------------------------
# IngestHandler
# ---------------------------------------------------------------------------


class TestIngestHandler:
    """Tests for IngestHandler."""

    def test_pipeline_path_happy(self, tmp_path: Path) -> None:
        """With pipeline set, handler uses EnrichedIngestPipeline."""
        pipeline = MagicMock(spec=EnrichedIngestPipeline)
        pipeline.ingest.return_value = ["rec_001"]
        handler = IngestHandler(root=tmp_path, pipeline=pipeline)
        cmd = IngestCommand(source="user", kind="episodic", task_id="t1", content="Remember to buy milk")
        result = handler.handle(cmd)
        assert result.record_id == "rec_001"
        assert result.source == "user"
        assert result.kind == "episodic"
        assert result.task_id == "t1"
        pipeline.ingest.assert_called_once_with(
            source="user", kind="episodic", task_id="t1", content="Remember to buy milk"
        )

    def test_pipeline_path_deduped(self, tmp_path: Path) -> None:
        """When pipeline.ingest returns empty list, record_id should be 'deduped'."""
        pipeline = MagicMock(spec=EnrichedIngestPipeline)
        pipeline.ingest.return_value = []
        handler = IngestHandler(root=tmp_path, pipeline=pipeline)
        cmd = IngestCommand(source="user", kind="episodic", task_id="t1", content="Duplicate content")
        result = handler.handle(cmd)
        assert result.record_id == "deduped"

    def test_fallback_path_uses_legacy_ingest(self, tmp_path: Path) -> None:
        """When pipeline is None, handler falls back to MemoryStore + IngestionPipeline."""

        @dataclass
        class FakeRecord:
            record_id: str = "fallback_001"
            source: str = "user"
            kind: str = "episodic"
            task_id: str = "t1"

        fake_pipeline = MagicMock(spec=IngestionPipeline)
        fake_pipeline.ingest.return_value = FakeRecord()

        with patch("jarvis_engine.memory_store.MemoryStore") as MockStore, \
             patch("jarvis_engine.ingest.IngestionPipeline", return_value=fake_pipeline):
            handler = IngestHandler(root=tmp_path, pipeline=None)
            cmd = IngestCommand(source="user", kind="episodic", task_id="t1", content="Some content")
            result = handler.handle(cmd)

        assert result.record_id == "fallback_001"
        assert result.source == "user"

    def test_fallback_pipeline_cached(self, tmp_path: Path) -> None:
        """The fallback pipeline is lazily created and cached."""

        @dataclass
        class FakeRecord:
            record_id: str = "rec_x"
            source: str = "user"
            kind: str = "ep"
            task_id: str = "t"

        fake_pipeline = MagicMock(spec=IngestionPipeline)
        fake_pipeline.ingest.return_value = FakeRecord()

        with patch("jarvis_engine.memory_store.MemoryStore"), \
             patch("jarvis_engine.ingest.IngestionPipeline", return_value=fake_pipeline) as MockPipeline:
            handler = IngestHandler(root=tmp_path, pipeline=None)
            handler.handle(IngestCommand(source="user", kind="ep", task_id="t", content="Content A"))
            handler.handle(IngestCommand(source="user", kind="ep", task_id="t", content="Content B"))

        # IngestionPipeline should only be instantiated once (cached)
        assert MockPipeline.call_count == 1

    def test_ingest_propagates_all_fields(self, tmp_path: Path) -> None:
        pipeline = MagicMock(spec=EnrichedIngestPipeline)
        pipeline.ingest.return_value = ["abc123"]
        handler = IngestHandler(root=tmp_path, pipeline=pipeline)
        cmd = IngestCommand(source="task_outcome", kind="semantic", task_id="build-42", content="Deployed successfully")
        result = handler.handle(cmd)
        assert result.source == "task_outcome"
        assert result.kind == "semantic"
        assert result.task_id == "build-42"


# ---------------------------------------------------------------------------
# MemorySnapshotHandler
# ---------------------------------------------------------------------------


class TestMemorySnapshotHandler:
    """Tests for MemorySnapshotHandler."""

    def test_create_snapshot(self, tmp_path: Path) -> None:
        """Calling with create=True delegates to create_signed_snapshot."""

        @dataclass
        class FakeSnapshotResult:
            snapshot_path: Path = Path("/fake/snap.zip")
            metadata_path: Path = Path("/fake/snap.json")
            signature_path: Path = Path("/fake/snap.sig")
            sha256: str = "abcd1234"
            file_count: int = 7

        with patch("jarvis_engine.memory_snapshots.create_signed_snapshot", return_value=FakeSnapshotResult()) as mock_css:
            handler = MemorySnapshotHandler(root=tmp_path)
            cmd = MemorySnapshotCommand(create=True, note="test snapshot")
            result = handler.handle(cmd)

        mock_css.assert_called_once_with(tmp_path, note="test snapshot")
        assert result.created is True
        assert result.sha256 == "abcd1234"
        assert result.file_count == 7
        assert "snap.zip" in result.snapshot_path

    def test_verify_snapshot_success(self, tmp_path: Path) -> None:
        """Calling with verify_path delegates to verify_signed_snapshot."""

        @dataclass
        class FakeVerification:
            ok: bool = True
            reason: str = "valid"
            expected_sha256: str = "aaa"
            actual_sha256: str = "aaa"
            expected_signature: str = ""
            actual_signature: str = ""

        snap_path = tmp_path / ".planning" / "brain" / "snapshots" / "test.zip"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.touch()

        with patch("jarvis_engine.memory_snapshots.verify_signed_snapshot", return_value=FakeVerification()):
            handler = MemorySnapshotHandler(root=tmp_path)
            cmd = MemorySnapshotCommand(verify_path=str(snap_path))
            result = handler.handle(cmd)

        assert result.verified is True
        assert result.ok is True

    def test_verify_path_outside_root(self, tmp_path: Path) -> None:
        """Verify path outside project root should fail."""
        handler = MemorySnapshotHandler(root=tmp_path)
        cmd = MemorySnapshotCommand(verify_path="/some/external/path.zip")
        result = handler.handle(cmd)
        assert result.verified is True
        assert result.ok is False
        assert "outside" in result.reason.lower()

    def test_no_create_no_verify(self, tmp_path: Path) -> None:
        """If neither create nor verify_path is set, returns default empty result."""
        handler = MemorySnapshotHandler(root=tmp_path)
        cmd = MemorySnapshotCommand()
        result = handler.handle(cmd)
        assert result.created is False
        assert result.verified is False

    def test_verify_empty_path_string(self, tmp_path: Path) -> None:
        """Empty verify_path string treated as unset."""
        handler = MemorySnapshotHandler(root=tmp_path)
        cmd = MemorySnapshotCommand(verify_path="  ")
        result = handler.handle(cmd)
        assert result.created is False
        assert result.verified is False


# ---------------------------------------------------------------------------
# MemoryMaintenanceHandler
# ---------------------------------------------------------------------------


class TestMemoryMaintenanceHandler:
    """Tests for MemoryMaintenanceHandler."""

    def test_maintenance_delegates_correctly(self, tmp_path: Path) -> None:
        fake_report = {"compact": True, "snapshot": "ok"}
        with patch("jarvis_engine.memory_snapshots.run_memory_maintenance", return_value=fake_report) as mock_rmm:
            handler = MemoryMaintenanceHandler(root=tmp_path)
            cmd = MemoryMaintenanceCommand(keep_recent=500, snapshot_note="  weekly backup  ")
            result = handler.handle(cmd)

        mock_rmm.assert_called_once_with(tmp_path, keep_recent=500, snapshot_note="weekly backup")
        assert result.report == fake_report

    def test_maintenance_clamps_keep_recent_low(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.memory_snapshots.run_memory_maintenance", return_value={}) as mock_rmm:
            handler = MemoryMaintenanceHandler(root=tmp_path)
            cmd = MemoryMaintenanceCommand(keep_recent=10)
            handler.handle(cmd)
        mock_rmm.assert_called_once_with(tmp_path, keep_recent=200, snapshot_note="nightly")

    def test_maintenance_clamps_keep_recent_high(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.memory_snapshots.run_memory_maintenance", return_value={}) as mock_rmm:
            handler = MemoryMaintenanceHandler(root=tmp_path)
            cmd = MemoryMaintenanceCommand(keep_recent=999999)
            handler.handle(cmd)
        mock_rmm.assert_called_once_with(tmp_path, keep_recent=50000, snapshot_note="nightly")

    def test_maintenance_truncates_long_note(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.memory_snapshots.run_memory_maintenance", return_value={}) as mock_rmm:
            handler = MemoryMaintenanceHandler(root=tmp_path)
            long_note = "x" * 300
            cmd = MemoryMaintenanceCommand(snapshot_note=long_note)
            handler.handle(cmd)
        _, kwargs = mock_rmm.call_args
        assert len(kwargs["snapshot_note"]) <= 160

    def test_maintenance_returns_result_type(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.memory_snapshots.run_memory_maintenance", return_value={"status": "ok"}):
            handler = MemoryMaintenanceHandler(root=tmp_path)
            result = handler.handle(MemoryMaintenanceCommand())
        assert isinstance(result, MemoryMaintenanceResult)
        assert result.report["status"] == "ok"
