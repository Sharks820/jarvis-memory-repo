"""Tests for the autonomous self-diagnosis engine (self_diagnosis.py)."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jarvis_engine.self_diagnosis import (
    DiagnosticEngine,
    DiagnosticIssue,
    _SEVERITY_ORDER,
    _issue_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_root(tmp_path: Path) -> Path:
    """Create a minimal project root with brain directory."""
    brain = tmp_path / ".planning" / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    runtime = tmp_path / ".planning" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _create_db(root: Path, *, wal_size_mb: float = 0.0) -> Path:
    """Create a minimal SQLite DB at the expected path."""
    db_path = root / ".planning" / "brain" / "jarvis_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO test_table (data) VALUES ('test')")
    conn.commit()
    conn.close()

    # Create WAL file if requested
    if wal_size_mb > 0:
        wal_path = Path(str(db_path) + "-wal")
        with open(wal_path, "wb") as f:
            f.write(b"\x00" * int(wal_size_mb * 1024 * 1024))

    return db_path


def _create_pressure_state(root: Path, level: str = "none", memory_mb: float = 200.0) -> None:
    """Write a fake resource pressure state file."""
    pressure_path = root / ".planning" / "runtime" / "resource_pressure.json"
    pressure_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "pressure_level": level,
        "metrics": {
            "process_memory_mb": {"current": memory_mb, "budget": 2048.0},
        },
    }
    pressure_path.write_text(json.dumps(state), encoding="utf-8")


def _create_missions(root: Path, missions: list[dict]) -> None:
    """Write fake missions.json at the correct path (.planning/missions.json)."""
    missions_path = root / ".planning" / "missions.json"
    missions_path.parent.mkdir(parents=True, exist_ok=True)
    missions_path.write_text(json.dumps(missions), encoding="utf-8")


def _create_kg_metrics(root: Path, node_count: int = 100, edge_count: int = 90, avg_confidence: float = 0.8) -> None:
    """Write fake KG metrics JSONL."""
    metrics_path = root / ".planning" / "runtime" / "kg_metrics.jsonl"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "node_count": node_count,
        "edge_count": edge_count,
        "avg_confidence": avg_confidence,
        "cross_branch_edges": 5,
        "locked_facts": 10,
        "branch_counts": {"general": node_count},
    }
    metrics_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# DiagnosticIssue tests
# ---------------------------------------------------------------------------


class TestDiagnosticIssue:
    def test_issue_has_timestamp(self):
        issue = DiagnosticIssue(
            id="abc123",
            severity="medium",
            component="database",
            description="Test issue",
            suggested_fix="Fix it",
            auto_fixable=False,
            fix_action=None,
        )
        assert issue.timestamp  # non-empty

    def test_to_dict(self):
        issue = DiagnosticIssue(
            id="abc123",
            severity="high",
            component="memory",
            description="High memory",
            suggested_fix="Restart",
            auto_fixable=True,
            fix_action="vacuum_db",
            evidence={"key": "value"},
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = issue.to_dict()
        assert d["id"] == "abc123"
        assert d["severity"] == "high"
        assert d["component"] == "memory"
        assert d["auto_fixable"] is True
        assert d["fix_action"] == "vacuum_db"
        assert d["evidence"] == {"key": "value"}

    def test_issue_id_unique(self):
        ids = {_issue_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


# ---------------------------------------------------------------------------
# Database health checks
# ---------------------------------------------------------------------------


class TestDatabaseHealth:
    def test_missing_db_critical(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        assert any(i.severity == "critical" and i.component == "database" for i in issues)

    def test_healthy_db_no_issues(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        # Should have no critical/high issues
        assert not any(i.severity in ("critical", "high") for i in issues)

    def test_large_wal_detected(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root, wal_size_mb=60.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        wal_issues = [i for i in issues if "WAL" in i.description]
        assert len(wal_issues) == 1
        assert wal_issues[0].severity == "high"
        assert wal_issues[0].auto_fixable is True
        assert wal_issues[0].fix_action == "prune_wal"

    def test_small_wal_no_issue(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root, wal_size_mb=1.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        assert not any("WAL" in i.description for i in issues)

    def test_large_db_detected(self, tmp_path):
        root = _make_root(tmp_path)
        db_path = _create_db(root)
        # Create a large file by padding
        with open(db_path, "ab") as f:
            f.write(b"\x00" * (550 * 1024 * 1024))
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        size_issues = [i for i in issues if "size" in i.description.lower() and "database" in i.description.lower()]
        assert len(size_issues) == 1
        assert size_issues[0].auto_fixable is True
        assert size_issues[0].fix_action == "vacuum_db"

    def test_integrity_pass(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        issues = diag._check_database_health()
        integrity_issues = [i for i in issues if "integrity" in i.description.lower()]
        assert len(integrity_issues) == 0


# ---------------------------------------------------------------------------
# Memory pressure checks
# ---------------------------------------------------------------------------


class TestMemoryPressure:
    def test_no_pressure_file(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_memory_pressure()
        assert len(issues) == 0

    def test_normal_pressure(self, tmp_path):
        root = _make_root(tmp_path)
        _create_pressure_state(root, level="none", memory_mb=200.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_memory_pressure()
        assert len(issues) == 0

    def test_high_pressure_detected(self, tmp_path):
        root = _make_root(tmp_path)
        _create_pressure_state(root, level="high", memory_mb=300.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_memory_pressure()
        pressure_issues = [i for i in issues if "pressure" in i.description.lower()]
        assert len(pressure_issues) == 1
        assert pressure_issues[0].severity == "medium"

    def test_critical_pressure_detected(self, tmp_path):
        root = _make_root(tmp_path)
        _create_pressure_state(root, level="critical", memory_mb=300.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_memory_pressure()
        pressure_issues = [i for i in issues if "pressure" in i.description.lower()]
        assert len(pressure_issues) == 1
        assert pressure_issues[0].severity == "high"

    def test_high_memory_usage(self, tmp_path):
        root = _make_root(tmp_path)
        _create_pressure_state(root, level="none", memory_mb=600.0)
        diag = DiagnosticEngine(root)
        issues = diag._check_memory_pressure()
        mem_issues = [i for i in issues if "memory usage" in i.description.lower()]
        assert len(mem_issues) == 1
        assert mem_issues[0].severity == "high"


# ---------------------------------------------------------------------------
# Gateway connectivity checks
# ---------------------------------------------------------------------------


class TestGatewayConnectivity:
    def test_groq_key_missing(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
            # Make sure the key is absent
            os.environ.pop("GROQ_API_KEY", None)
            issues = diag._check_gateway_connectivity()
        key_issues = [i for i in issues if "GROQ_API_KEY" in i.description]
        assert len(key_issues) == 1
        assert key_issues[0].severity == "info"

    def test_groq_key_present(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}):
            issues = diag._check_gateway_connectivity()
        key_issues = [i for i in issues if "GROQ_API_KEY" in i.description]
        assert len(key_issues) == 0

    def test_ollama_unreachable(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
                issues = diag._check_gateway_connectivity()
        ollama_issues = [i for i in issues if "Ollama" in i.description]
        assert len(ollama_issues) == 1
        assert ollama_issues[0].severity == "info"


# ---------------------------------------------------------------------------
# Mission health checks
# ---------------------------------------------------------------------------


class TestMissionHealth:
    def test_no_missions(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        assert len(issues) == 0

    def test_healthy_missions(self, tmp_path):
        root = _make_root(tmp_path)
        missions = [
            {"mission_id": "m1", "status": "completed", "created_utc": "2026-01-01T00:00:00Z"},
            {"mission_id": "m2", "status": "completed", "created_utc": "2026-01-02T00:00:00Z"},
        ]
        _create_missions(root, missions)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        assert len(issues) == 0

    def test_stuck_mission_detected(self, tmp_path):
        root = _make_root(tmp_path)
        # Mission running since 20 minutes ago
        started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        missions = [
            {"mission_id": "stuck1", "status": "running", "started_at": started, "updated_utc": started},
        ]
        _create_missions(root, missions)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        stuck_issues = [i for i in issues if "stuck" in i.description.lower()]
        assert len(stuck_issues) == 1
        assert stuck_issues[0].auto_fixable is True
        assert stuck_issues[0].fix_action == "clear_stuck_missions"

    def test_recent_running_not_stuck(self, tmp_path):
        root = _make_root(tmp_path)
        # Mission running since 2 minutes ago (not stuck)
        started = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        missions = [
            {"mission_id": "ok1", "status": "running", "started_at": started, "updated_utc": started},
        ]
        _create_missions(root, missions)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        stuck_issues = [i for i in issues if "stuck" in i.description.lower()]
        assert len(stuck_issues) == 0

    def test_high_failure_rate(self, tmp_path):
        root = _make_root(tmp_path)
        # 8 failed out of 10
        missions = [
            {"mission_id": f"m{i}", "status": "failed" if i < 8 else "completed", "created_utc": "2026-01-01T00:00:00Z"}
            for i in range(10)
        ]
        _create_missions(root, missions)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        rate_issues = [i for i in issues if "failure rate" in i.description.lower()]
        assert len(rate_issues) == 1
        assert rate_issues[0].severity == "medium"

    def test_low_failure_rate_ok(self, tmp_path):
        root = _make_root(tmp_path)
        # 2 failed out of 10
        missions = [
            {"mission_id": f"m{i}", "status": "failed" if i < 2 else "completed", "created_utc": "2026-01-01T00:00:00Z"}
            for i in range(10)
        ]
        _create_missions(root, missions)
        diag = DiagnosticEngine(root)
        issues = diag._check_mission_health()
        rate_issues = [i for i in issues if "failure rate" in i.description.lower()]
        assert len(rate_issues) == 0


# ---------------------------------------------------------------------------
# Knowledge graph checks
# ---------------------------------------------------------------------------


class TestKnowledgeGraph:
    def test_no_kg_metrics(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_knowledge_graph()
        assert len(issues) == 0

    def test_healthy_kg(self, tmp_path):
        root = _make_root(tmp_path)
        _create_kg_metrics(root, node_count=100, edge_count=95, avg_confidence=0.9)
        diag = DiagnosticEngine(root)
        issues = diag._check_knowledge_graph()
        assert len(issues) == 0

    def test_low_confidence_detected(self, tmp_path):
        root = _make_root(tmp_path)
        _create_kg_metrics(root, node_count=100, edge_count=95, avg_confidence=0.3)
        diag = DiagnosticEngine(root)
        issues = diag._check_knowledge_graph()
        conf_issues = [i for i in issues if "confidence" in i.description.lower()]
        assert len(conf_issues) == 1
        assert conf_issues[0].severity == "medium"

    def test_high_orphan_ratio(self, tmp_path):
        root = _make_root(tmp_path)
        # 50 nodes, only 20 edges -> ratio > 10%
        _create_kg_metrics(root, node_count=50, edge_count=20, avg_confidence=0.9)
        diag = DiagnosticEngine(root)
        issues = diag._check_knowledge_graph()
        orphan_issues = [i for i in issues if "orphan" in i.description.lower()]
        assert len(orphan_issues) == 1
        assert orphan_issues[0].severity == "low"


# ---------------------------------------------------------------------------
# Voice health checks
# ---------------------------------------------------------------------------


class TestVoiceHealth:
    def test_voice_health_runs_without_error(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_voice_health()
        # Should return a list regardless of whether personal_vocab or silero_vad exist
        assert isinstance(issues, list)

    def test_voice_health_all_issues_have_voice_component(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issues = diag._check_voice_health()
        for issue in issues:
            assert issue.component == "voice"


# ---------------------------------------------------------------------------
# Health score computation
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_score_100_no_issues(self):
        assert DiagnosticEngine.health_score([]) == 100

    def test_score_deduction_critical(self):
        issues = [
            DiagnosticIssue(
                id="a", severity="critical", component="db",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            ),
        ]
        assert DiagnosticEngine.health_score(issues) == 70  # 100 - 30

    def test_score_deduction_high(self):
        issues = [
            DiagnosticIssue(
                id="a", severity="high", component="db",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            ),
        ]
        assert DiagnosticEngine.health_score(issues) == 85  # 100 - 15

    def test_score_deduction_medium(self):
        issues = [
            DiagnosticIssue(
                id="a", severity="medium", component="db",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            ),
        ]
        assert DiagnosticEngine.health_score(issues) == 95  # 100 - 5

    def test_score_deduction_low(self):
        issues = [
            DiagnosticIssue(
                id="a", severity="low", component="db",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            ),
        ]
        assert DiagnosticEngine.health_score(issues) == 98  # 100 - 2

    def test_info_no_deduction(self):
        issues = [
            DiagnosticIssue(
                id="a", severity="info", component="gw",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            ),
        ]
        assert DiagnosticEngine.health_score(issues) == 100

    def test_score_floor_at_zero(self):
        issues = [
            DiagnosticIssue(
                id=f"i{n}", severity="critical", component="db",
                description="x", suggested_fix="y", auto_fixable=False,
                fix_action=None,
            )
            for n in range(5)
        ]
        assert DiagnosticEngine.health_score(issues) == 0  # 100 - 150 = 0 (clamped)

    def test_mixed_severities(self):
        issues = [
            DiagnosticIssue(id="a", severity="critical", component="db", description="x", suggested_fix="y", auto_fixable=False, fix_action=None),
            DiagnosticIssue(id="b", severity="high", component="db", description="x", suggested_fix="y", auto_fixable=False, fix_action=None),
            DiagnosticIssue(id="c", severity="medium", component="db", description="x", suggested_fix="y", auto_fixable=False, fix_action=None),
            DiagnosticIssue(id="d", severity="low", component="db", description="x", suggested_fix="y", auto_fixable=False, fix_action=None),
        ]
        # 100 - 30 - 15 - 5 - 2 = 48
        assert DiagnosticEngine.health_score(issues) == 48


# ---------------------------------------------------------------------------
# Auto-fix actions
# ---------------------------------------------------------------------------


class TestAutoFix:
    def test_vacuum_db(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        result = diag._fix_vacuum_db()
        assert result["applied"] is True
        assert "VACUUM" in result["result"]

    def test_vacuum_db_no_file(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        result = diag._fix_vacuum_db()
        assert result["applied"] is False

    def test_prune_wal(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root, wal_size_mb=1.0)
        diag = DiagnosticEngine(root)
        result = diag._fix_prune_wal()
        assert result["applied"] is True
        assert "checkpoint" in result["result"].lower()

    def test_prune_wal_no_file(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        result = diag._fix_prune_wal()
        assert result["applied"] is False

    def test_rebuild_fts_missing_table(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        # fts_records table doesn't exist in test DB, so this should fail gracefully
        result = diag._fix_rebuild_fts()
        assert result["applied"] is False
        assert "fts_records" in result["result"].lower() or "fts" in result["result"].lower()

    def test_apply_fix_not_found(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        result = diag.apply_fix("nonexistent", [])
        assert result["applied"] is False
        assert "not found" in result["result"].lower()

    def test_apply_fix_not_auto_fixable(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issue = DiagnosticIssue(
            id="test1", severity="high", component="db",
            description="x", suggested_fix="y", auto_fixable=False,
            fix_action=None,
        )
        result = diag.apply_fix("test1", [issue])
        assert result["applied"] is False
        assert "not auto-fixable" in result["result"].lower()

    def test_apply_fix_vacuum(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        issue = DiagnosticIssue(
            id="test1", severity="medium", component="database",
            description="Large DB", suggested_fix="VACUUM",
            auto_fixable=True, fix_action="vacuum_db",
        )
        result = diag.apply_fix("test1", [issue])
        assert result["applied"] is True

    def test_apply_fix_unknown_action(self, tmp_path):
        root = _make_root(tmp_path)
        diag = DiagnosticEngine(root)
        issue = DiagnosticIssue(
            id="test1", severity="medium", component="db",
            description="x", suggested_fix="y",
            auto_fixable=True, fix_action="unknown_action",
        )
        result = diag.apply_fix("test1", [issue])
        assert result["applied"] is False
        assert "unknown" in result["result"].lower()


# ---------------------------------------------------------------------------
# Full and quick scan
# ---------------------------------------------------------------------------


class TestScans:
    def test_full_scan_returns_sorted(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        _create_pressure_state(root, level="none", memory_mb=200.0)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                issues = diag.run_full_scan()
        # Check sorting: each severity <= next
        for i in range(len(issues) - 1):
            assert _SEVERITY_ORDER.get(issues[i].severity, 99) <= _SEVERITY_ORDER.get(issues[i + 1].severity, 99)

    def test_quick_scan_subset(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                issues = diag.run_quick_scan()
        # Quick scan only checks database, memory, gateway
        components = {i.component for i in issues}
        assert components <= {"database", "memory", "gateway"}

    def test_full_scan_includes_all_components(self, tmp_path):
        """Full scan exercises all checkers (may produce no issues but must run)."""
        root = _make_root(tmp_path)
        _create_db(root)
        _create_pressure_state(root, level="critical", memory_mb=600.0)
        _create_missions(root, [
            {"mission_id": "m1", "status": "failed", "created_utc": "2026-01-01T00:00:00Z"},
            {"mission_id": "m2", "status": "failed", "created_utc": "2026-01-01T00:00:00Z"},
            {"mission_id": "m3", "status": "failed", "created_utc": "2026-01-01T00:00:00Z"},
            {"mission_id": "m4", "status": "failed", "created_utc": "2026-01-01T00:00:00Z"},
        ])
        _create_kg_metrics(root, node_count=100, edge_count=30, avg_confidence=0.3)
        diag = DiagnosticEngine(root)
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                issues = diag.run_full_scan()
        # Should find issues across multiple components
        components = {i.component for i in issues}
        assert "memory" in components
        assert "gateway" in components
        assert "knowledge_graph" in components


# ---------------------------------------------------------------------------
# CQRS handler
# ---------------------------------------------------------------------------


class TestDiagnosticRunHandler:
    def test_handler_full_scan(self, tmp_path):
        from jarvis_engine.commands.ops_commands import DiagnosticRunCommand, DiagnosticRunResult
        from jarvis_engine.handlers.ops_handlers import DiagnosticRunHandler

        root = _make_root(tmp_path)
        _create_db(root)
        handler = DiagnosticRunHandler(root)
        cmd = DiagnosticRunCommand(full_scan=True)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = handler.handle(cmd)
        assert isinstance(result, DiagnosticRunResult)
        assert result.return_code == 0
        assert isinstance(result.score, int)
        assert isinstance(result.issues, list)

    def test_handler_quick_scan(self, tmp_path):
        from jarvis_engine.commands.ops_commands import DiagnosticRunCommand, DiagnosticRunResult
        from jarvis_engine.handlers.ops_handlers import DiagnosticRunHandler

        root = _make_root(tmp_path)
        _create_db(root)
        handler = DiagnosticRunHandler(root)
        cmd = DiagnosticRunCommand(full_scan=False)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = handler.handle(cmd)
        assert isinstance(result, DiagnosticRunResult)
        assert result.return_code == 0

    def test_handler_healthy_when_score_high(self, tmp_path):
        from jarvis_engine.commands.ops_commands import DiagnosticRunCommand
        from jarvis_engine.handlers.ops_handlers import DiagnosticRunHandler

        root = _make_root(tmp_path)
        _create_db(root)
        _create_pressure_state(root, level="none", memory_mb=100.0)
        handler = DiagnosticRunHandler(root)
        cmd = DiagnosticRunCommand(full_scan=False)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = handler.handle(cmd)
        # Ollama unreachable is info level (no deduction), so score should be high
        assert result.healthy is True


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------


class TestSeverityOrder:
    def test_ordering_exists(self):
        assert _SEVERITY_ORDER["critical"] < _SEVERITY_ORDER["high"]
        assert _SEVERITY_ORDER["high"] < _SEVERITY_ORDER["medium"]
        assert _SEVERITY_ORDER["medium"] < _SEVERITY_ORDER["low"]
        assert _SEVERITY_ORDER["low"] < _SEVERITY_ORDER["info"]

    def test_all_severities_covered(self):
        for sev in ("critical", "high", "medium", "low", "info"):
            assert sev in _SEVERITY_ORDER


# ---------------------------------------------------------------------------
# Dashboard integration
# ---------------------------------------------------------------------------


class TestDashboardIntegration:
    def test_safe_diagnostics_returns_dict(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        from jarvis_engine.intelligence_dashboard import _safe_diagnostics
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = _safe_diagnostics(root)
        assert isinstance(result, dict)
        assert "score" in result
        assert "healthy" in result
        assert "issue_count" in result

    def test_safe_diagnostics_graceful_failure(self, tmp_path):
        root = _make_root(tmp_path)
        from jarvis_engine.intelligence_dashboard import _safe_diagnostics
        # Patch the module-level import inside the function
        with patch("jarvis_engine.self_diagnosis.DiagnosticEngine", side_effect=RuntimeError("boom")):
            result = _safe_diagnostics(root)
        assert result == {}


# ---------------------------------------------------------------------------
# Daemon integration
# ---------------------------------------------------------------------------


class TestDaemonIntegration:
    def test_diagnostic_scan_cycle_runs(self, tmp_path):
        root = _make_root(tmp_path)
        _create_db(root)
        from jarvis_engine.daemon_loop import _run_diagnostic_scan_cycle
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                _run_diagnostic_scan_cycle(root)
        # Check that history file was created
        history_path = root / ".planning" / "runtime" / "diagnostics_history.jsonl"
        assert history_path.exists()
        content = history_path.read_text(encoding="utf-8").strip()
        entry = json.loads(content)
        assert "score" in entry
        assert "issue_count" in entry
        assert "ts" in entry
