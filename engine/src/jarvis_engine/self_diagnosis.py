"""Autonomous self-diagnosis engine for Jarvis health monitoring.

Runs configurable health checks across database, memory, gateway, missions,
knowledge graph, and voice subsystems.  Returns structured
:class:`DiagnosticIssue` instances sorted by severity, computes a 0-100
health score, and can auto-fix certain issues (VACUUM, FTS rebuild, WAL
checkpoint, stuck-mission cancellation).
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from jarvis_engine._compat import UTC
from jarvis_engine._shared import parse_iso_timestamp

logger = logging.getLogger(__name__)

# Severity ordering (lower = more severe)
_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

# Deduction table for health_score()
_SEVERITY_DEDUCTIONS: dict[str, int] = {
    "critical": 30,
    "high": 15,
    "medium": 5,
    "low": 2,
    "info": 0,
}

# Unit conversion
_BYTES_PER_MB = 1024.0 * 1024.0

# Thresholds
_WAL_SIZE_WARN_MB = 50.0
_DB_SIZE_WARN_MB = 500.0
_MEMORY_PRESSURE_MB = 512.0
_STUCK_MISSION_MINUTES = 10.0
_MISSION_FAILURE_RATE_THRESHOLD = 0.5
_ORPHAN_NODE_RATIO_THRESHOLD = 0.10
_KG_AVG_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class DiagnosticIssue:
    id: str
    severity: str          # critical / high / medium / low / info
    component: str         # "memory", "database", "gateway", "missions", "voice", "security", "knowledge_graph"
    description: str       # human-readable
    suggested_fix: str     # what to do
    auto_fixable: bool     # can be auto-fixed
    fix_action: str | None  # "vacuum_db", "rebuild_fts", etc.
    evidence: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _issue_id() -> str:
    return uuid.uuid4().hex[:8]


class DiagnosticEngine:
    """Run health checks across all Jarvis subsystems."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _get_db_path(self) -> Path:
        try:
            from jarvis_engine._shared import memory_db_path
            return memory_db_path(self._root)
        except (ImportError, OSError, ValueError):
            return self._root / ".planning" / "brain" / "jarvis_memory.db"

    # Public API

    def run_full_scan(self) -> list[DiagnosticIssue]:
        """Run all health checks, return sorted by severity."""
        issues: list[DiagnosticIssue] = []
        issues.extend(self._check_database_health())
        issues.extend(self._check_memory_pressure())
        issues.extend(self._check_gateway_connectivity())
        issues.extend(self._check_mission_health())
        issues.extend(self._check_knowledge_graph())
        issues.extend(self._check_voice_health())
        return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 99))

    def run_quick_scan(self) -> list[DiagnosticIssue]:
        """Quick scan: database + memory + gateway only."""
        issues: list[DiagnosticIssue] = []
        issues.extend(self._check_database_health())
        issues.extend(self._check_memory_pressure())
        issues.extend(self._check_gateway_connectivity())
        return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 99))

    @staticmethod
    def health_score(issues: list[DiagnosticIssue]) -> int:
        """Compute a 0-100 health score.

        Starts at 100 and deducts per severity:
        critical=-30, high=-15, medium=-5, low=-2, info=0.
        """
        score = 100
        for issue in issues:
            score -= _SEVERITY_DEDUCTIONS.get(issue.severity, 0)
        return max(0, score)

    def apply_fix(self, issue_id: str, issues: list[DiagnosticIssue]) -> dict[str, Any]:
        """Execute auto-fix for the given issue.

        Returns ``{"applied": bool, "result": str}``.
        """
        target = None
        for issue in issues:
            if issue.id == issue_id:
                target = issue
                break
        if target is None:
            return {"applied": False, "result": f"Issue {issue_id} not found"}
        if not target.auto_fixable:
            return {"applied": False, "result": f"Issue {issue_id} is not auto-fixable"}

        action = target.fix_action
        if action == "vacuum_db":
            return self._fix_vacuum_db()
        if action == "rebuild_fts":
            return self._fix_rebuild_fts()
        if action == "prune_wal":
            return self._fix_prune_wal()
        if action == "clear_stuck_missions":
            return self._fix_clear_stuck_missions()

        return {"applied": False, "result": f"Unknown fix action: {action}"}

    # Health check methods

    def _check_database_health(self) -> list[DiagnosticIssue]:
        """Check database integrity, WAL size, and total DB size."""
        issues: list[DiagnosticIssue] = []
        db_path = self._get_db_path()

        # Check if DB exists
        if not db_path.exists():
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="critical",
                component="database",
                description="Memory database file does not exist",
                suggested_fix="Run a command that initializes the database (e.g. ingest or query)",
                auto_fixable=False,
                fix_action=None,
                evidence={"path": str(db_path)},
            ))
            return issues

        # Check DB size
        try:
            db_size_bytes = db_path.stat().st_size
            db_size_mb = db_size_bytes / _BYTES_PER_MB
            if db_size_mb > _DB_SIZE_WARN_MB:
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="medium",
                    component="database",
                    description=f"Database size is {db_size_mb:.1f} MB (threshold: {_DB_SIZE_WARN_MB} MB)",
                    suggested_fix="Run VACUUM to reclaim space",
                    auto_fixable=True,
                    fix_action="vacuum_db",
                    evidence={"size_mb": round(db_size_mb, 2)},
                ))
        except OSError as exc:
            logger.debug("DB size check failed: %s", exc)

        # Check WAL file size
        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            try:
                wal_size_bytes = wal_path.stat().st_size
                wal_size_mb = wal_size_bytes / _BYTES_PER_MB
                if wal_size_mb > _WAL_SIZE_WARN_MB:
                    issues.append(DiagnosticIssue(
                        id=_issue_id(),
                        severity="high",
                        component="database",
                        description=f"WAL file is {wal_size_mb:.1f} MB (threshold: {_WAL_SIZE_WARN_MB} MB)",
                        suggested_fix="Run WAL checkpoint to truncate",
                        auto_fixable=True,
                        fix_action="prune_wal",
                        evidence={"wal_size_mb": round(wal_size_mb, 2)},
                    ))
            except OSError as exc:
                logger.debug("WAL size check failed: %s", exc)

        # Run integrity check
        try:
            from jarvis_engine._db_pragmas import connect_db
            conn = connect_db(db_path)
            try:
                result = conn.execute("PRAGMA quick_check").fetchone()
                if result and result[0] != "ok":
                    issues.append(DiagnosticIssue(
                        id=_issue_id(),
                        severity="critical",
                        component="database",
                        description=f"Database integrity check failed: {result[0]}",
                        suggested_fix="Database may need repair or restore from backup",
                        auto_fixable=False,
                        fix_action=None,
                        evidence={"check_result": str(result[0])},
                    ))
            finally:
                conn.close()
        except (ImportError, OSError, ValueError, sqlite3.Error) as exc:
            logger.debug("Integrity check failed: %s", exc)
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="high",
                component="database",
                description=f"Could not run integrity check: {exc}",
                suggested_fix="Ensure database is not locked by another process",
                auto_fixable=False,
                fix_action=None,
                evidence={"error": str(exc)},
            ))

        return issues

    def _check_memory_pressure(self) -> list[DiagnosticIssue]:
        """Check resource pressure state from runtime_control."""
        issues: list[DiagnosticIssue] = []
        try:
            from jarvis_engine.ops.runtime_control import read_resource_pressure_state
            pressure = read_resource_pressure_state(self._root)
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Resource pressure read failed: %s", exc)
            return issues

        if not pressure:
            return issues

        # Check pressure level
        pressure_level = str(pressure.get("pressure_level", "none")).lower()
        if pressure_level in ("high", "critical"):
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="high" if pressure_level == "critical" else "medium",
                component="memory",
                description=f"Resource pressure level is '{pressure_level}'",
                suggested_fix="Reduce memory usage or increase resource budgets",
                auto_fixable=False,
                fix_action=None,
                evidence={"pressure_level": pressure_level},
            ))

        # Check process memory
        metrics = pressure.get("metrics", {})
        if isinstance(metrics, dict):
            proc_mem = metrics.get("process_memory_mb", {})
            if isinstance(proc_mem, dict):
                current_mb = proc_mem.get("current", 0.0)
            else:
                current_mb = float(proc_mem) if proc_mem else 0.0
            try:
                current_mb = float(current_mb)
            except (TypeError, ValueError):
                current_mb = 0.0
            if current_mb > _MEMORY_PRESSURE_MB:
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="high",
                    component="memory",
                    description=f"Process memory usage is {current_mb:.0f} MB (threshold: {_MEMORY_PRESSURE_MB:.0f} MB)",
                    suggested_fix="Restart daemon to free memory, or investigate leaks",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={"process_memory_mb": round(current_mb, 1)},
                ))

        return issues

    def _check_gateway_connectivity(self) -> list[DiagnosticIssue]:
        """Check gateway API keys and Ollama reachability."""
        issues: list[DiagnosticIssue] = []
        try:
            import os
            # Check for API keys
            groq_key = os.environ.get("GROQ_API_KEY", "")
            if not groq_key:
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="info",
                    component="gateway",
                    description="GROQ_API_KEY is not set",
                    suggested_fix="Set GROQ_API_KEY environment variable for cloud LLM access",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={"key": "GROQ_API_KEY", "set": False},
                ))
        except (ImportError, OSError, ValueError):
            logger.debug("Gateway config check failed during self-diagnosis")

        # Check Ollama reachability
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status != 200:
                    issues.append(DiagnosticIssue(
                        id=_issue_id(),
                        severity="medium",
                        component="gateway",
                        description=f"Ollama returned non-200 status: {resp.status}",
                        suggested_fix="Check Ollama service status",
                        auto_fixable=False,
                        fix_action=None,
                        evidence={"status": resp.status},
                    ))
        except (ImportError, OSError, ValueError) as exc:
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="info",
                component="gateway",
                description=f"Ollama is not reachable at localhost:11434: {exc}",
                suggested_fix="Start Ollama service or verify it is running",
                auto_fixable=False,
                fix_action=None,
                evidence={"error": str(exc)},
            ))

        return issues

    def _check_mission_health(self) -> list[DiagnosticIssue]:
        """Check for stuck or failing learning missions."""
        issues: list[DiagnosticIssue] = []
        try:
            from jarvis_engine.learning.missions import load_missions
            missions = load_missions(self._root)
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Mission health check failed: %s", exc)
            return issues

        if not missions:
            return issues

        # Check for stuck missions (running > 10 minutes)
        now = datetime.now(UTC)

        stuck_ids: list[str] = []
        for mission in missions:
            status = str(mission.get("status", "")).lower()
            if status != "running":
                continue
            started_at = str(mission.get("started_at", "") or mission.get("updated_utc", ""))
            if not started_at:
                continue
            started = parse_iso_timestamp(started_at)
            if started is None:
                continue
            delta_minutes = (now - started).total_seconds() / 60.0
            if delta_minutes > _STUCK_MISSION_MINUTES:
                stuck_ids.append(str(mission.get("mission_id", "")))

        if stuck_ids:
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="medium",
                component="missions",
                description=f"{len(stuck_ids)} mission(s) stuck in 'running' for > {_STUCK_MISSION_MINUTES:.0f} minutes",
                suggested_fix="Cancel stuck missions or wait for completion",
                auto_fixable=True,
                fix_action="clear_stuck_missions",
                evidence={"stuck_mission_ids": stuck_ids},
            ))

        # Check failure rate of recent missions
        recent = missions[-10:] if len(missions) >= 10 else missions
        if len(recent) >= 3:
            failed = sum(1 for m in recent if str(m.get("status", "")).lower() == "failed")
            rate = failed / len(recent)
            if rate > _MISSION_FAILURE_RATE_THRESHOLD:
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="medium",
                    component="missions",
                    description=f"High mission failure rate: {rate:.0%} of last {len(recent)} missions failed",
                    suggested_fix="Investigate mission failure reasons, check web access and API keys",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={"failure_rate": round(rate, 2), "failed": failed, "total": len(recent)},
                ))

        return issues

    def _check_knowledge_graph(self) -> list[DiagnosticIssue]:
        """Check KG metrics for orphan nodes and low confidence."""
        issues: list[DiagnosticIssue] = []
        try:
            from jarvis_engine.proactive.kg_metrics import load_kg_history
            from jarvis_engine._constants import KG_METRICS_LOG
            from jarvis_engine._shared import runtime_dir
            history_path = runtime_dir(self._root) / KG_METRICS_LOG
            history = load_kg_history(history_path, limit=5)
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("KG metrics load failed: %s", exc)
            return issues

        if not history:
            return issues

        latest = history[-1]
        node_count = int(latest.get("node_count", 0))
        edge_count = int(latest.get("edge_count", 0))
        avg_confidence = float(latest.get("avg_confidence", 1.0))

        # Check orphan ratio (nodes with no edges)
        if node_count > 0 and edge_count >= 0:
            # Approximate: if node_count >> edge_count, many nodes are orphans
            # A better proxy: nodes without edges = node_count - (unique nodes in edges)
            # But from metrics we only have node_count and edge_count.
            # Use branch_counts to check for disconnected nodes if available.
            # Simple heuristic: if edge_count < node_count * 0.9 (10% orphan threshold)
            if node_count > 10 and edge_count < node_count * (1 - _ORPHAN_NODE_RATIO_THRESHOLD):
                ratio = max(0.0, 1.0 - (edge_count / node_count)) if node_count > 0 else 0.0
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="low",
                    component="knowledge_graph",
                    description=f"Estimated {ratio:.0%} orphan ratio in knowledge graph ({node_count} nodes, {edge_count} edges)",
                    suggested_fix="Run knowledge enrichment to connect orphan nodes",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "orphan_ratio_estimate": round(ratio, 3),
                    },
                ))

        # Check average confidence
        if avg_confidence < _KG_AVG_CONFIDENCE_THRESHOLD and node_count > 0:
            issues.append(DiagnosticIssue(
                id=_issue_id(),
                severity="medium",
                component="knowledge_graph",
                description=f"Average knowledge graph confidence is {avg_confidence:.2f} (threshold: {_KG_AVG_CONFIDENCE_THRESHOLD})",
                suggested_fix="Review low-confidence facts and verify or remove them",
                auto_fixable=False,
                fix_action=None,
                evidence={"avg_confidence": round(avg_confidence, 3), "node_count": node_count},
            ))

        return issues

    def _check_voice_health(self) -> list[DiagnosticIssue]:
        """Check voice subsystem health (personal vocab, VAD model)."""
        issues: list[DiagnosticIssue] = []

        # Check personal_vocab.txt
        try:
            vocab_path = Path(__file__).parent / "data" / "personal_vocab.txt"
            if not vocab_path.exists():
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="low",
                    component="voice",
                    description="Personal vocabulary file (data/personal_vocab.txt) not found",
                    suggested_fix="Create personal_vocab.txt with frequently used names and terms",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={"path": str(vocab_path)},
                ))
            else:
                try:
                    content = vocab_path.read_text(encoding="utf-8").strip()
                    lines = [l for l in content.splitlines() if l.strip()]
                    if not lines:
                        issues.append(DiagnosticIssue(
                            id=_issue_id(),
                            severity="low",
                            component="voice",
                            description="Personal vocabulary file is empty",
                            suggested_fix="Add frequently used names and terms to personal_vocab.txt",
                            auto_fixable=False,
                            fix_action=None,
                            evidence={"path": str(vocab_path), "line_count": 0},
                        ))
                except OSError as exc:
                    logger.debug("Personal vocab read failed: %s", exc)
        except (ImportError, OSError, ValueError):
            logger.debug("Voice pipeline check failed during self-diagnosis")

        # Check if VAD model is importable
        try:
            spec = find_spec("silero_vad")
            if spec is None:
                issues.append(DiagnosticIssue(
                    id=_issue_id(),
                    severity="info",
                    component="voice",
                    description="Silero VAD package not installed",
                    suggested_fix="Install silero_vad for voice activity detection",
                    auto_fixable=False,
                    fix_action=None,
                    evidence={"package": "silero_vad"},
                ))
        except (ImportError, OSError, ValueError):
            logger.debug("VAD model check failed during self-diagnosis")

        return issues

    # Auto-fix actions

    def _fix_vacuum_db(self) -> dict[str, Any]:
        """Run VACUUM + ANALYZE on memory.db."""
        db_path = self._get_db_path()

        if not db_path.exists():
            return {"applied": False, "result": "Database file does not exist"}

        try:
            import sqlite3
            from jarvis_engine._db_pragmas import connect_db
            conn = connect_db(db_path, timeout=30)
            try:
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
            finally:
                conn.close()
            return {"applied": True, "result": "VACUUM and ANALYZE completed successfully"}
        except (OSError, ValueError, sqlite3.Error) as exc:
            return {"applied": False, "result": f"VACUUM failed: {exc}"}

    def _fix_rebuild_fts(self) -> dict[str, Any]:
        """Rebuild FTS5 index."""
        db_path = self._get_db_path()

        if not db_path.exists():
            return {"applied": False, "result": "Database file does not exist"}

        try:
            import sqlite3
            from jarvis_engine._db_pragmas import connect_db
            conn = connect_db(db_path, timeout=30)
            try:
                conn.execute("INSERT INTO fts_records(fts_records) VALUES('rebuild')")
                conn.commit()
            finally:
                conn.close()
            return {"applied": True, "result": "FTS5 index rebuilt successfully"}
        except (sqlite3.Error, OSError, ValueError) as exc:
            return {"applied": False, "result": f"FTS rebuild failed: {exc}"}

    def _fix_prune_wal(self) -> dict[str, Any]:
        """Run WAL checkpoint (TRUNCATE) to reclaim WAL space."""
        db_path = self._get_db_path()

        if not db_path.exists():
            return {"applied": False, "result": "Database file does not exist"}

        try:
            import sqlite3
            from jarvis_engine._db_pragmas import connect_db
            conn = connect_db(db_path, timeout=30)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
            return {"applied": True, "result": "WAL checkpoint (TRUNCATE) completed"}
        except (OSError, ValueError, sqlite3.Error) as exc:
            return {"applied": False, "result": f"WAL checkpoint failed: {exc}"}

    def _fix_clear_stuck_missions(self) -> dict[str, Any]:
        """Cancel missions running > 30 minutes."""
        try:
            from jarvis_engine.learning.missions import load_missions, cancel_mission
            from jarvis_engine._compat import UTC
        except (ImportError, OSError, ValueError) as exc:
            return {"applied": False, "result": f"Cannot load mission module: {exc}"}

        try:
            missions = load_missions(self._root)
            now = datetime.now(UTC)
            cancelled_ids: list[str] = []

            for mission in missions:
                status = str(mission.get("status", "")).lower()
                if status != "running":
                    continue
                started_at = str(mission.get("started_at", "") or mission.get("updated_utc", ""))
                if not started_at:
                    continue
                started = parse_iso_timestamp(started_at)
                if started is None:
                    continue
                delta_minutes = (now - started).total_seconds() / 60.0
                if delta_minutes > 30.0:
                    mid = str(mission.get("mission_id", ""))
                    try:
                        cancel_mission(self._root, mission_id=mid)
                        cancelled_ids.append(mid)
                    except (ValueError, OSError) as cancel_exc:
                        logger.debug("Failed to cancel mission %s: %s", mid, cancel_exc)

            if cancelled_ids:
                return {"applied": True, "result": f"Cancelled {len(cancelled_ids)} stuck mission(s): {cancelled_ids}"}
            return {"applied": True, "result": "No missions needed cancellation"}
        except (OSError, ValueError) as exc:
            return {"applied": False, "result": f"Mission cleanup failed: {exc}"}
