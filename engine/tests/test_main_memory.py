"""Tests for memory, brain, knowledge graph, harvesting, and learning CLI commands.

Covers: brain-status, brain-context, brain-compact, brain-regression,
memory-snapshot, memory-maintenance, ingest, knowledge-status,
contradiction-list/resolve, fact-lock, knowledge-regression,
harvest, ingest-session, harvest-budget, learn, cross-branch-query,
flag-expired, migrate-memory, proactive-check, cost-reduction, self-test,
run-task, self-heal, mobile-desktop-sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine import main as main_mod
from jarvis_engine import voice_pipeline as voice_pipeline_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine import auto_ingest as auto_ingest_mod
from jarvis_engine import _bus as bus_mod


# ===========================================================================
# Knowledge graph commands
# ===========================================================================


class TestKnowledgeStatus:
    """Tests for cmd_knowledge_status."""

    def test_knowledge_status_text_mode(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import KnowledgeStatusResult
        result = KnowledgeStatusResult(node_count=42, edge_count=100, locked_count=3,
                                       pending_contradictions=1, graph_hash="abc123")
        bus = mock_bus(result)
        rc = main_mod.cmd_knowledge_status(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "node_count=42" in out
        assert "edge_count=100" in out
        assert "locked_count=3" in out
        assert "pending_contradictions=1" in out
        assert "graph_hash=abc123" in out

    def test_knowledge_status_json_mode(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import KnowledgeStatusResult
        result = KnowledgeStatusResult(node_count=10, edge_count=20, locked_count=0,
                                       pending_contradictions=0, graph_hash="def456")
        bus = mock_bus(result)
        rc = main_mod.cmd_knowledge_status(as_json=True)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["node_count"] == 10
        assert parsed["graph_hash"] == "def456"


class TestContradictionList:
    """Tests for cmd_contradiction_list."""

    def test_contradiction_list_empty(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        result = ContradictionListResult(contradictions=[])
        bus = mock_bus(result)
        rc = main_mod.cmd_contradiction_list(status="pending", limit=20, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No contradictions found" in out

    def test_contradiction_list_with_items(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        items = [
            {"contradiction_id": 1, "node_id": "n1", "existing_value": "old",
             "incoming_value": "new", "status": "pending", "created_at": "2026-01-01"},
        ]
        result = ContradictionListResult(contradictions=items)
        bus = mock_bus(result)
        rc = main_mod.cmd_contradiction_list(status="pending", limit=20, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id=1" in out
        assert "node=n1" in out

    def test_contradiction_list_json(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        items = [{"contradiction_id": 5}]
        result = ContradictionListResult(contradictions=items)
        bus = mock_bus(result)
        rc = main_mod.cmd_contradiction_list(status="", limit=10, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["contradictions"][0]["contradiction_id"] == 5


class TestContradictionResolve:
    """Tests for cmd_contradiction_resolve."""

    def test_resolve_success(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import ContradictionResolveResult
        result = ContradictionResolveResult(success=True, node_id="n1",
                                            resolution="accept_new", message="Resolved.")
        bus = mock_bus(result)
        rc = main_mod.cmd_contradiction_resolve(contradiction_id=1, resolution="accept_new", merge_value="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "resolved=true" in out
        assert "node_id=n1" in out

    def test_resolve_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import ContradictionResolveResult
        result = ContradictionResolveResult(success=False, message="Not found.")
        bus = mock_bus(result)
        rc = main_mod.cmd_contradiction_resolve(contradiction_id=99, resolution="keep_old", merge_value="")
        assert rc == 1
        out = capsys.readouterr().out
        assert "resolved=false" in out


class TestFactLock:
    """Tests for cmd_fact_lock."""

    def test_lock_success(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=True, node_id="fact1", locked=True)
        bus = mock_bus(result)
        rc = main_mod.cmd_fact_lock(node_id="fact1", action="lock")
        assert rc == 0
        out = capsys.readouterr().out
        assert "success=true" in out

    def test_lock_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=False, node_id="missing")
        bus = mock_bus(result)
        rc = main_mod.cmd_fact_lock(node_id="missing", action="unlock")
        assert rc == 1

    def test_unlock_success(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=True, node_id="fact2", locked=False)
        bus = mock_bus(result)
        rc = main_mod.cmd_fact_lock(node_id="fact2", action="unlock")
        assert rc == 0
        out = capsys.readouterr().out
        assert "locked=False" in out


class TestKnowledgeRegression:
    """Tests for cmd_knowledge_regression."""

    def test_regression_text(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        report = {
            "status": "ok",
            "message": "All good",
            "discrepancies": [],
            "current": {"node_count": 10, "edge_count": 20, "locked_count": 1, "graph_hash": "aaa"},
        }
        result = KnowledgeRegressionResult(report=report)
        bus = mock_bus(result)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "status=ok" in out
        assert "nodes=10" in out

    def test_regression_json(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        result = KnowledgeRegressionResult(report={"status": "degraded"})
        bus = mock_bus(result)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "degraded"

    def test_regression_with_discrepancies(self, capsys, mock_bus):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        report = {
            "status": "warning",
            "discrepancies": [
                {"severity": "high", "type": "missing_node", "message": "Node X missing"},
            ],
            "current": {},
        }
        result = KnowledgeRegressionResult(report=report)
        bus = mock_bus(result)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing_node" in out


# ===========================================================================
# Harvesting commands
# ===========================================================================


class TestHarvest:
    """Tests for cmd_harvest."""

    def test_harvest_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import HarvestTopicResult
        result = HarvestTopicResult(
            topic="quantum computing",
            results=[
                {"provider": "anthropic", "status": "ok", "records_created": 3, "cost_usd": 0.001},
                {"provider": "groq", "status": "ok", "records_created": 2, "cost_usd": 0.0005},
            ],
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_harvest(topic="quantum computing", providers=None, max_tokens=2048)
        assert rc == 0
        out = capsys.readouterr().out
        assert "harvest_topic=quantum computing" in out
        assert "provider=anthropic" in out
        assert "records=3" in out

    def test_harvest_with_provider_filter(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import HarvestTopicResult
        result = HarvestTopicResult(topic="ML", results=[], return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_harvest(topic="ML", providers="groq,mistral", max_tokens=1024)
        assert rc == 0
        # Verify providers were parsed into a list
        cmd = bus.dispatch.call_args[0][0]
        assert cmd.providers == ["groq", "mistral"]


class TestIngestSession:
    """Tests for cmd_ingest_session."""

    def test_ingest_session_claude(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import IngestSessionResult
        result = IngestSessionResult(source="claude", sessions_processed=5, records_created=12, return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_ingest_session(source="claude", session_path=None, project_path=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "sessions_processed=5" in out
        assert "records_created=12" in out

    def test_ingest_session_with_path(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import IngestSessionResult
        result = IngestSessionResult(source="codex", sessions_processed=1, records_created=4, return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_ingest_session(source="codex", session_path="/tmp/session.json", project_path=None)
        assert rc == 0


class TestHarvestBudget:
    """Tests for cmd_harvest_budget."""

    def test_budget_status(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import HarvestBudgetResult
        result = HarvestBudgetResult(
            summary={"period_days": 30, "total_cost_usd": 0.15,
                     "providers": [{"provider": "groq", "total_cost_usd": 0.10, "total_requests": 50}]},
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_harvest_budget(action="status", provider=None, period=None,
                                         limit_usd=None, limit_requests=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "budget_period_days=30" in out
        assert "provider=groq" in out

    def test_budget_set(self, capsys, mock_bus):
        from jarvis_engine.commands.harvest_commands import HarvestBudgetResult
        result = HarvestBudgetResult(
            summary={"provider": "groq", "period": "daily", "limit_usd": 1.0},
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_harvest_budget(action="set", provider="groq", period="daily",
                                         limit_usd=1.0, limit_requests=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "budget_set" in out
        assert "provider=groq" in out


# ===========================================================================
# Learning commands
# ===========================================================================


class TestLearn:
    """Tests for cmd_learn."""

    def test_learn_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.learning_commands import LearnInteractionResult
        result = LearnInteractionResult(records_created=2, message="Learned 2 patterns.")
        bus = mock_bus(result)
        rc = main_mod.cmd_learn(user_message="How's the weather?", assistant_response="It's sunny.")
        assert rc == 0
        out = capsys.readouterr().out
        assert "records_created=2" in out
        assert "Learned 2 patterns" in out


class TestCrossBranchQuery:
    """Tests for cmd_cross_branch_query."""

    def test_cross_branch_query(self, capsys, mock_bus):
        from jarvis_engine.commands.learning_commands import CrossBranchQueryResult
        result = CrossBranchQueryResult(
            direct_results=[{"record_id": "r1", "distance": 0.12}],
            cross_branch_connections=[
                {"source_branch": "tech", "target_branch": "health", "relation": "related"},
            ],
            branches_involved=["tech", "health"],
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_cross_branch_query(query="AI in healthcare", k=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "direct_results=1" in out
        assert "cross_branch_connections=1" in out
        assert "tech" in out
        assert "health" in out

    def test_cross_branch_query_empty(self, capsys, mock_bus):
        from jarvis_engine.commands.learning_commands import CrossBranchQueryResult
        result = CrossBranchQueryResult()
        bus = mock_bus(result)
        rc = main_mod.cmd_cross_branch_query(query="nonexistent topic", k=5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "direct_results=0" in out


class TestFlagExpired:
    """Tests for cmd_flag_expired."""

    def test_flag_expired(self, capsys, mock_bus):
        from jarvis_engine.commands.learning_commands import FlagExpiredFactsResult
        result = FlagExpiredFactsResult(expired_count=7, message="Flagged 7 expired facts.")
        bus = mock_bus(result)
        rc = main_mod.cmd_flag_expired()
        assert rc == 0
        out = capsys.readouterr().out
        assert "expired_count=7" in out


# ===========================================================================
# Proactive / cost / self-test commands
# ===========================================================================


class TestProactiveCheck:
    """Tests for cmd_proactive_check."""

    def test_proactive_no_alerts(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import ProactiveCheckResult
        result = ProactiveCheckResult(alerts_fired=0, alerts=[], message="No alerts.")
        bus = mock_bus(result)
        rc = main_mod.cmd_proactive_check(snapshot_path="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "alerts_fired=0" in out

    def test_proactive_with_alerts(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import ProactiveCheckResult
        alerts_list = [{"rule_id": "bill_due", "message": "Electric bill due tomorrow"}]
        result = ProactiveCheckResult(alerts_fired=1, alerts=alerts_list, message="1 alert triggered.")
        bus = mock_bus(result)
        rc = main_mod.cmd_proactive_check(snapshot_path="/tmp/snapshot.json")
        assert rc == 0
        out = capsys.readouterr().out
        assert "alerts_fired=1" in out
        assert "bill_due" in out


class TestCostReduction:
    """Tests for cmd_cost_reduction."""

    def test_cost_reduction(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import CostReductionResult
        result = CostReductionResult(local_pct=85.3, cloud_cost_usd=0.42,
                                     trend="improving", message="Costs trending down.")
        bus = mock_bus(result)
        rc = main_mod.cmd_cost_reduction(days=30)
        assert rc == 0
        out = capsys.readouterr().out
        assert "local_pct=85.3" in out
        assert "cloud_cost_usd=0.42" in out
        assert "trend=improving" in out


class TestSelfTest:
    """Tests for cmd_self_test."""

    def test_self_test_passes(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import SelfTestResult
        result = SelfTestResult(
            average_score=0.85,
            tasks_run=5,
            regression_detected=False,
            message="All tests passed.",
            per_task_scores=[
                {"task_id": "recall_1", "score": 0.9},
                {"task_id": "recall_2", "score": 0.8},
            ],
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_self_test(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "average_score=0.8500" in out
        assert "tasks_run=5" in out
        assert "regression_detected=False" in out
        assert "recall_1" in out

    def test_self_test_with_regression(self, capsys, mock_bus):
        from jarvis_engine.commands.proactive_commands import SelfTestResult
        result = SelfTestResult(
            average_score=0.3,
            tasks_run=3,
            regression_detected=True,
            message="Regression detected!",
            per_task_scores=[],
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_self_test(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "regression_detected=True" in out


# ===========================================================================
# Brain commands (compact, regression, context edge cases)
# ===========================================================================


class TestBrainCompact:
    """Tests for cmd_brain_compact."""

    def test_brain_compact_text(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainCompactResult
        result = BrainCompactResult(result={"compacted": True, "removed": 50, "kept": 1800})
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_compact(keep_recent=1800, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "compacted=True" in out
        assert "removed=50" in out

    def test_brain_compact_json(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainCompactResult
        result = BrainCompactResult(result={"compacted": True})
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_compact(keep_recent=500, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["compacted"] is True


class TestBrainRegression:
    """Tests for cmd_brain_regression."""

    def test_brain_regression_text(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainRegressionResult
        result = BrainRegressionResult(report={"status": "healthy", "duplicate_ratio": 0.02})
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_regression(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "status=healthy" in out

    def test_brain_regression_json(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainRegressionResult
        result = BrainRegressionResult(report={"status": "ok"})
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_regression(as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "ok"


class TestBrainContext:
    """Tests for cmd_brain_context edge cases."""

    def test_brain_context_empty_query(self, capsys, monkeypatch):
        rc = main_mod.cmd_brain_context(query="   ", max_items=5, max_chars=1200, as_json=False)
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_brain_context_json_output(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainContextResult
        result = BrainContextResult(packet={
            "query": "gaming", "selected_count": 1,
            "selected": [{"branch": "tech", "source": "user", "kind": "semantic", "summary": "Gaming modes..."}],
            "canonical_facts": [{"key": "mode", "value": "gaming", "confidence": 0.9}],
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_context(query="gaming", max_items=5, max_chars=1200, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["query"] == "gaming"

    def test_brain_context_text_output(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainContextResult
        result = BrainContextResult(packet={
            "query": "test", "selected_count": 0, "selected": [], "canonical_facts": [],
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_context(query="test", max_items=5, max_chars=1200, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "brain_context" in out


class TestBrainStatus:
    """Tests for cmd_brain_status."""

    def test_brain_status_json(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainStatusResult
        result = BrainStatusResult(status={"updated_utc": "2026-01-01", "branch_count": 5, "branches": []})
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_status(as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["branch_count"] == 5

    def test_brain_status_text_with_branches(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import BrainStatusResult
        result = BrainStatusResult(status={
            "updated_utc": "2026-01-01", "branch_count": 1,
            "branches": [{"branch": "tech", "count": 42, "last_ts": "2026-01-01", "last_summary": "stuff"}],
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_brain_status(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "branch=tech" in out
        assert "count=42" in out


# ===========================================================================
# Ingest command
# ===========================================================================


class TestIngestCommand:
    """Tests for cmd_ingest."""

    def test_ingest_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import IngestResult
        result = IngestResult(record_id="rec-123", source="user", kind="semantic", task_id="t1")
        bus = mock_bus(result)
        rc = main_mod.cmd_ingest(source="user", kind="semantic", task_id="t1", content="Test content")
        assert rc == 0
        out = capsys.readouterr().out
        assert "id=rec-123" in out


# ===========================================================================
# Run task
# ===========================================================================


class TestRunTask:
    """Tests for cmd_run_task."""

    def test_run_task_success(self, capsys, mock_bus):
        from jarvis_engine.commands.task_commands import RunTaskResult
        result = RunTaskResult(
            allowed=True, provider="ollama", plan="Generate image", reason="approved",
            output_path="/tmp/output.png", output_text="Generated!", return_code=0,
            auto_ingest_record_id="rec-50",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_run_task(
            task_type="image", prompt="A sunset", execute=True,
            approve_privileged=False, model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434", quality_profile="max_quality",
            output_path="/tmp/output.png",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "allowed=True" in out
        assert "output_path=/tmp/output.png" in out
        assert "auto_ingest_record_id=rec-50" in out

    def test_run_task_denied(self, capsys, mock_bus):
        from jarvis_engine.commands.task_commands import RunTaskResult
        result = RunTaskResult(allowed=False, reason="privileged task denied", return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_run_task(
            task_type="video", prompt="test", execute=False,
            approve_privileged=False, model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434", quality_profile="max_quality",
            output_path=None,
        )
        assert rc == 2


# ===========================================================================
# Memory snapshot edge cases
# ===========================================================================


class TestMemorySnapshotEdgeCases:
    """Tests for cmd_memory_snapshot."""

    def test_snapshot_no_action(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(created=False, verified=False)
        bus = mock_bus(result)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path=None, note="")
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_snapshot_verify_ok(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            verified=True, ok=True, reason="Hashes match.",
            expected_sha256="abc", actual_sha256="abc",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path="/tmp/snap.zip", note="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "ok=True" in out

    def test_snapshot_verify_fail(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            verified=True, ok=False, reason="Hash mismatch.",
            expected_sha256="abc", actual_sha256="xyz",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path="/tmp/snap.zip", note="")
        assert rc == 2

    def test_snapshot_create(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            created=True, snapshot_path="/tmp/snap.zip",
            metadata_path="/tmp/snap.meta.json", signature_path="/tmp/snap.sig",
            sha256="abc123", file_count=10,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_memory_snapshot(create=True, verify_path=None, note="test")
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_snapshot_created=true" in out
        assert "file_count=10" in out


# ===========================================================================
# Memory maintenance
# ===========================================================================


class TestMemoryMaintenanceEdgeCases:
    """Tests for cmd_memory_maintenance via mock bus."""

    def test_maintenance_with_details(self, capsys, mock_bus):
        from jarvis_engine.commands.memory_commands import MemoryMaintenanceResult
        result = MemoryMaintenanceResult(report={
            "status": "ok", "report_path": "/tmp/report.json",
            "compact": {"compacted": True, "total_records": 2000, "kept_records": 1800},
            "regression": {"status": "healthy", "duplicate_ratio": 0.01, "unresolved_conflicts": 0},
            "snapshot": {"path": "/tmp/snap.zip"},
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_memory_maintenance(keep_recent=1800, snapshot_note="nightly")
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_maintenance" in out
        assert "compacted=True" in out
        assert "duplicate_ratio=0.01" in out


# ===========================================================================
# Self-heal with mock bus
# ===========================================================================


class TestSelfHealMocked:
    """Tests for cmd_self_heal via mocked bus."""

    def test_self_heal_json(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import SelfHealResult
        report = {"status": "ok", "actions": ["checked_db", "verified_config"],
                  "regression": {"status": "healthy", "duplicate_ratio": 0.0, "unresolved_conflicts": 0},
                  "report_path": "/tmp/heal.json"}
        result = SelfHealResult(report=report, return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_self_heal(force_maintenance=False, keep_recent=1800,
                                     snapshot_note="test", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "ok"

    def test_self_heal_text(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import SelfHealResult
        report = {"status": "repaired", "actions": ["fixed_index"],
                  "regression": {"status": "ok", "duplicate_ratio": 0.0, "unresolved_conflicts": 0},
                  "report_path": "/tmp/heal.json"}
        result = SelfHealResult(report=report, return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_self_heal(force_maintenance=False, keep_recent=500,
                                     snapshot_note="test", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "self_heal" in out
        assert "action=fixed_index" in out


# ===========================================================================
# Mobile desktop sync mocked
# ===========================================================================


class TestMobileDesktopSyncMocked:
    """Tests for cmd_mobile_desktop_sync via mocked bus."""

    def test_sync_json(self, capsys, monkeypatch, mock_bus):
        from jarvis_engine.commands.system_commands import MobileDesktopSyncResult
        result = MobileDesktopSyncResult(
            report={"sync_ok": True, "checks": [{"name": "config", "ok": True}]},
            return_code=0,
        )
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "_auto_ingest_memory", lambda **kw: "")
        rc = main_mod.cmd_mobile_desktop_sync(auto_ingest=False, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["sync_ok"] is True

    def test_sync_text(self, capsys, monkeypatch, mock_bus):
        from jarvis_engine.commands.system_commands import MobileDesktopSyncResult
        result = MobileDesktopSyncResult(
            report={"sync_ok": True, "report_path": "/tmp/sync.json",
                    "checks": [{"name": "config", "ok": True}]},
            return_code=0,
        )
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "_auto_ingest_memory", lambda **kw: "")
        rc = main_mod.cmd_mobile_desktop_sync(auto_ingest=False, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "mobile_desktop_sync" in out
        assert "check_config=True" in out


# ===========================================================================
# Migrate memory
# ===========================================================================


class TestMigrateMemory:
    """Tests for cmd_migrate_memory."""

    def test_migrate_success(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import MigrateMemoryResult
        result = MigrateMemoryResult(
            summary={"totals": {"inserted": 100, "skipped": 5, "errors": 0}, "db_path": "/tmp/mem.db"},
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_migrate_memory()
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_migration_complete" in out
        assert "total_inserted=100" in out

    def test_migrate_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import MigrateMemoryResult
        result = MigrateMemoryResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_migrate_memory()
        assert rc == 2
