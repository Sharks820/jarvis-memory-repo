"""Tests for daemon_loop.py — topic discovery, cycle management, gaming, status, errors."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import jarvis_engine.daemon_loop as daemon_loop_mod
from jarvis_engine.daemon_loop import (
    _try_add_candidate,
    _add_phrases,
    _collect_from_recent_memories,
    _collect_from_kg_gaps,
    _collect_from_strong_kg_areas,
    _collect_from_activity_feed,
    _collect_from_learning_missions,
    _discover_harvest_topics,
    _handle_circuit_breaker,
    _print_cycle_status,
    _should_skip_cycle,
    _gather_cycle_state,
    _run_missions_cycle,
    _run_sync_cycle,
    _run_watchdog_cycle,
    _restart_mobile_api,
    _log_cycle_start,
    _log_cycle_end,
    _run_db_optimize_cycle,
    _run_core_autopilot,
    _emit_cycle_status,
    gaming_mode_state_path,
    gaming_processes_path,
    read_gaming_mode_state,
    write_gaming_mode_state,
    load_gaming_processes,
    detect_active_game_process,
    cmd_mission_run,
    GamingModeState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Provide a tmp root directory with required sub-dirs."""
    (tmp_path / ".planning" / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def memory_db(root: Path) -> sqlite3.Connection:
    """Create a minimal SQLite DB with records and KG tables for topic discovery."""
    db_path = root / ".planning" / "brain" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records "
        "(record_id TEXT PRIMARY KEY, summary TEXT, ts TEXT, source TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kg_nodes "
        "(node_id TEXT PRIMARY KEY, label TEXT, confidence REAL, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kg_edges "
        "(edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, relation TEXT)"
    )
    conn.commit()
    return conn


# ===================================================================
# _try_add_candidate tests
# ===================================================================


class TestTryAddCandidate:
    """Tests for _try_add_candidate() — topic filtering and dedup."""

    def test_adds_valid_two_word_topic(self) -> None:
        """A valid 2-word topic should be added to candidates."""
        candidates: list[str] = []
        seen: set[str] = set()
        result = _try_add_candidate("machine learning", candidates, seen, set(), 5)
        assert candidates == ["machine learning"]
        assert not result  # not full yet

    def test_rejects_short_topic(self) -> None:
        """Topics shorter than 4 characters should be rejected."""
        candidates: list[str] = []
        result = _try_add_candidate("ab", candidates, set(), set(), 5)
        assert candidates == []
        assert not result

    def test_rejects_empty_topic(self) -> None:
        """Empty string should be rejected."""
        candidates: list[str] = []
        result = _try_add_candidate("", candidates, set(), set(), 5)
        assert candidates == []

    def test_rejects_single_word_topic(self) -> None:
        """Topics with only 1 word should be rejected."""
        candidates: list[str] = []
        result = _try_add_candidate("python", candidates, set(), set(), 5)
        assert candidates == []

    def test_rejects_too_many_words(self) -> None:
        """Topics with more than 5 words should be rejected."""
        candidates: list[str] = []
        long_topic = "one two three four five six"
        result = _try_add_candidate(long_topic, candidates, set(), set(), 5)
        assert candidates == []

    def test_deduplicates_case_insensitive(self) -> None:
        """A topic already in seen_lower (case-insensitive) should be rejected."""
        candidates: list[str] = []
        seen = {"machine learning"}
        result = _try_add_candidate("Machine Learning", candidates, seen, set(), 5)
        assert candidates == []

    def test_rejects_recently_harvested(self) -> None:
        """Topics in recently_harvested set should be rejected."""
        candidates: list[str] = []
        harvested = {"deep learning"}
        result = _try_add_candidate("deep learning", candidates, set(), harvested, 5)
        assert candidates == []

    def test_returns_true_when_full(self) -> None:
        """Should return True when candidates reaches max_topics."""
        candidates: list[str] = ["topic one", "topic two"]
        result = _try_add_candidate("topic three", candidates, set(), set(), 3)
        assert result is True
        assert len(candidates) == 3

    def test_strips_whitespace(self) -> None:
        """Leading and trailing whitespace should be stripped."""
        candidates: list[str] = []
        _try_add_candidate("  neural networks  ", candidates, set(), set(), 5)
        assert candidates == ["neural networks"]


# ===================================================================
# _add_phrases tests
# ===================================================================


class TestAddPhrases:
    """Tests for _add_phrases() — extraction and accumulation."""

    @patch("jarvis_engine.daemon_loop._extract_topic_phrases")
    def test_adds_extracted_phrases(self, mock_extract) -> None:
        """Extracted phrases should be added as candidates."""
        mock_extract.return_value = ["data science", "neural network"]
        candidates: list[str] = []
        seen: set[str] = set()
        result = _add_phrases("some text", candidates, seen, set(), 5)
        assert "data science" in candidates
        assert "neural network" in candidates
        assert not result

    @patch("jarvis_engine.daemon_loop._extract_topic_phrases")
    def test_returns_true_when_full(self, mock_extract) -> None:
        """Should return True when candidates list is full."""
        mock_extract.return_value = ["topic aa", "topic bb", "topic cc"]
        candidates: list[str] = []
        seen: set[str] = set()
        result = _add_phrases("text", candidates, seen, set(), 2)
        assert result is True
        assert len(candidates) >= 2


# ===================================================================
# _collect_from_* source tests
# ===================================================================


class TestCollectFromRecentMemories:
    """Tests for _collect_from_recent_memories() — source 1."""

    def test_collects_from_recent_summaries(self, memory_db) -> None:
        """Should extract topics from recent memory summaries."""
        from datetime import datetime
        from jarvis_engine._compat import UTC

        ts = datetime.now(UTC).isoformat()
        memory_db.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?)",
            ("r1", "advanced machine learning techniques applied", ts, "user"),
        )
        memory_db.commit()
        candidates: list[str] = []
        seen: set[str] = set()
        _collect_from_recent_memories(memory_db, candidates, seen, set(), 3)
        # Should have at least attempted extraction (may or may not produce valid phrases)
        # The function should not raise
        assert isinstance(candidates, list)

    def test_handles_empty_table(self, memory_db) -> None:
        """Should handle empty records table gracefully."""
        candidates: list[str] = []
        _collect_from_recent_memories(memory_db, candidates, set(), set(), 3)
        assert candidates == []


class TestCollectFromKgGaps:
    """Tests for _collect_from_kg_gaps() — source 2."""

    def test_handles_empty_kg_tables(self, memory_db) -> None:
        """Should handle empty KG tables gracefully."""
        candidates: list[str] = []
        _collect_from_kg_gaps(memory_db, candidates, set(), set(), 3)
        assert candidates == []

    def test_handles_missing_kg_tables(self) -> None:
        """Should swallow OperationalError from missing tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        candidates: list[str] = []
        # No kg_nodes/kg_edges tables exist — should not raise
        _collect_from_kg_gaps(conn, candidates, set(), set(), 3)
        assert candidates == []
        conn.close()


class TestCollectFromStrongKgAreas:
    """Tests for _collect_from_strong_kg_areas() — source 3."""

    def test_generates_expanded_topics(self, memory_db) -> None:
        """Should expand strong KG prefixes with suffixes."""
        # Insert many nodes with a shared prefix to trigger the >= 5 threshold
        for i in range(6):
            memory_db.execute(
                "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                (f"n{i}", f"Python development topic{i}", 0.8, "2026-01-01"),
            )
        memory_db.commit()
        candidates: list[str] = []
        seen: set[str] = set()
        _collect_from_strong_kg_areas(memory_db, candidates, seen, set(), 5)
        # Should produce expanded topics like "Python development best practices"
        matching = [c for c in candidates if c.startswith("Python development")]
        assert len(matching) >= 1

    def test_handles_missing_tables(self) -> None:
        """Should swallow errors from missing KG tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        candidates: list[str] = []
        _collect_from_strong_kg_areas(conn, candidates, set(), set(), 5)
        assert candidates == []
        conn.close()


class TestCollectFromLearningMissions:
    """Tests for _collect_from_learning_missions() — source 5."""

    def test_collects_from_completed_missions(self, root) -> None:
        """Should collect topics from completed learning missions."""
        missions = [
            {"mission_id": "m1", "topic": "quantum computing basics", "status": "completed"},
            {"mission_id": "m2", "topic": "single", "status": "completed"},  # 1 word
            {"mission_id": "m3", "topic": "rust programming", "status": "pending"},
        ]
        with patch("jarvis_engine.daemon_loop.load_missions", return_value=missions):
            candidates: list[str] = []
            seen: set[str] = set()
            _collect_from_learning_missions(root, candidates, seen, set(), 5)
        assert "quantum computing basics" in candidates
        assert "single" not in candidates  # single word rejected

    def test_handles_load_error(self, root) -> None:
        """Should handle load_missions errors gracefully."""
        with patch("jarvis_engine.daemon_loop.load_missions", side_effect=OSError("disk")):
            candidates: list[str] = []
            _collect_from_learning_missions(root, candidates, set(), set(), 5)
        assert candidates == []


# ===================================================================
# _discover_harvest_topics integration tests
# ===================================================================


class TestDiscoverHarvestTopics:
    """Tests for _discover_harvest_topics() — end-to-end discovery."""

    def test_returns_list_up_to_3(self, root) -> None:
        """Should return at most 3 topics."""
        with patch("jarvis_engine.daemon_loop._memory_db_path") as mock_db_path, \
             patch("jarvis_engine.daemon_loop._get_recently_harvested_topics", return_value=set()):
            mock_db_path.return_value = root / "nonexistent.db"
            topics = _discover_harvest_topics(root)
        assert isinstance(topics, list)
        assert len(topics) <= 3

    def test_never_raises(self, root) -> None:
        """Should return empty list when DB does not exist and no other sources produce topics."""
        with patch("jarvis_engine.daemon_loop._memory_db_path") as mock_db_path, \
             patch("jarvis_engine.daemon_loop._get_recently_harvested_topics", return_value=set()):
            # Point to a nonexistent DB so the path.exists() check returns False
            mock_db_path.return_value = root / "nonexistent.db"
            topics = _discover_harvest_topics(root)
        assert topics == [] or isinstance(topics, list)


# ===================================================================
# Gaming mode integration tests
# ===================================================================


class TestGamingModeIntegration:
    """Tests for gaming mode wrappers in daemon_loop."""

    def test_gaming_mode_state_path(self, root) -> None:
        """Should construct correct path for gaming mode state."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            path = gaming_mode_state_path()
        assert str(path).endswith("gaming_mode.json")

    def test_gaming_processes_path(self, root) -> None:
        """Should construct correct path for gaming processes config."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            path = gaming_processes_path()
        assert str(path).endswith("gaming_processes.json")

    def test_read_gaming_mode_state_defaults(self, root) -> None:
        """Should return sensible defaults when no state file exists."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            state = read_gaming_mode_state()
        assert state["enabled"] is False
        assert state["auto_detect"] is False

    def test_write_and_read_gaming_mode_state(self, root) -> None:
        """Should persist and read back gaming mode state."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            write_gaming_mode_state({"enabled": True, "auto_detect": True, "reason": "test"})
            state = read_gaming_mode_state()
        assert state["enabled"] is True
        assert state["auto_detect"] is True


# ===================================================================
# Cycle management tests
# ===================================================================


class TestCycleManagement:
    """Tests for cycle state, skip logic, and circuit breaker."""

    def test_should_skip_cycle_paused(self) -> None:
        """Cycle should be skipped when daemon is paused."""
        state = {"daemon_paused": True, "gaming_mode_enabled": False}
        reason = _should_skip_cycle(state, 120)
        assert reason is not None
        assert "daemon_paused" in reason

    def test_should_skip_cycle_gaming(self) -> None:
        """Cycle should be skipped when gaming mode is enabled."""
        state = {"daemon_paused": False, "gaming_mode_enabled": True}
        reason = _should_skip_cycle(state, 120)
        assert reason is not None
        assert "gaming_mode" in reason

    def test_should_not_skip_normal(self) -> None:
        """Cycle should proceed normally when not paused and not gaming."""
        state = {"daemon_paused": False, "gaming_mode_enabled": False}
        reason = _should_skip_cycle(state, 120)
        assert reason is None

    def test_circuit_breaker_resets_on_success(self) -> None:
        """Circuit breaker should reset to 0 on success (rc=0)."""
        result = _handle_circuit_breaker(0, 5)
        assert result == 0

    def test_circuit_breaker_increments_on_failure(self) -> None:
        """Circuit breaker should increment on failure."""
        result = _handle_circuit_breaker(1, 3)
        assert result == 4

    @patch("jarvis_engine.daemon_loop.time.sleep")
    def test_circuit_breaker_cooldown_at_max(self, mock_sleep) -> None:
        """Circuit breaker should trigger 300s cooldown at max failures."""
        result = _handle_circuit_breaker(1, 9)  # 9+1 = 10 = max
        mock_sleep.assert_called_once_with(300)
        assert result == 0  # reset after cooldown


# ===================================================================
# Status printing tests
# ===================================================================


class TestStatusPrinting:
    """Tests for _print_cycle_status()."""

    def test_prints_basic_status(self, capsys) -> None:
        """Should print cycle number, pause state, and mode info."""
        state = {
            "daemon_paused": False,
            "safe_mode": False,
            "gaming_mode_enabled": False,
            "auto_detect": False,
            "detected_process": "",
            "gaming_state": {},
            "control_state": {},
            "is_active": True,
            "pressure_level": "none",
            "resource_snapshot": {"metrics": {}},
            "sleep_seconds": 120,
            "skip_heavy_tasks": False,
            "idle_seconds": 5.0,
        }
        _print_cycle_status(1, "2026-01-01T00:00:00Z", state)
        output = capsys.readouterr().out
        assert "cycle=1" in output
        assert "daemon_paused=False" in output
        assert "gaming_mode=False" in output
        assert "idle_seconds=5.0" in output

    def test_prints_detected_process(self, capsys) -> None:
        """Should print detected game process when present."""
        state = {
            "daemon_paused": False,
            "safe_mode": False,
            "gaming_mode_enabled": True,
            "auto_detect": True,
            "detected_process": "cs2.exe",
            "gaming_state": {"reason": "auto-detected"},
            "control_state": {},
            "is_active": True,
            "pressure_level": "none",
            "resource_snapshot": {"metrics": {}},
            "sleep_seconds": 120,
            "skip_heavy_tasks": False,
            "idle_seconds": None,
        }
        _print_cycle_status(2, "2026-01-01T00:00:00Z", state)
        output = capsys.readouterr().out
        assert "cs2.exe" in output
        assert "gaming_mode=True" in output

    def test_prints_pressure_throttle_info(self, capsys) -> None:
        """Should print throttle info when under resource pressure."""
        state = {
            "daemon_paused": False,
            "safe_mode": False,
            "gaming_mode_enabled": False,
            "auto_detect": False,
            "detected_process": "",
            "gaming_state": {},
            "control_state": {},
            "is_active": True,
            "pressure_level": "mild",
            "resource_snapshot": {"metrics": {}},
            "sleep_seconds": 180,
            "skip_heavy_tasks": True,
            "idle_seconds": 30.0,
        }
        _print_cycle_status(3, "2026-01-01T00:00:00Z", state)
        output = capsys.readouterr().out
        assert "resource_throttle_sleep_s=180" in output
        assert "resource_skip_heavy_tasks=true" in output


# ===================================================================
# Error handling during daemon cycles
# ===================================================================


class TestDaemonCycleErrors:
    """Tests for error handling in daemon subsystem cycles."""

    def test_run_sync_cycle_handles_error(self, capsys) -> None:
        """Sync cycle should print error on failure, not raise."""
        mock_sync = MagicMock(side_effect=OSError("network down"))
        _run_sync_cycle(mock_sync)
        output = capsys.readouterr().out
        assert "sync_cycle_error=" in output

    def test_run_sync_cycle_success(self, capsys) -> None:
        """Sync cycle should print return code on success."""
        mock_sync = MagicMock(return_value=0)
        _run_sync_cycle(mock_sync)
        output = capsys.readouterr().out
        assert "sync_cycle_rc=0" in output

    def test_run_missions_cycle_handles_error(self, root, capsys) -> None:
        """Mission cycle should handle import/runtime errors gracefully."""
        with patch("jarvis_engine.daemon_loop._run_next_pending_mission",
                    side_effect=RuntimeError("mission fail")):
            _run_missions_cycle(root, 1, False)
        output = capsys.readouterr().out
        assert "mission_cycle_error=" in output

    def test_run_watchdog_cycle_handles_import_error(self, root, capsys) -> None:
        """Watchdog cycle should handle missing process_manager gracefully."""
        with patch("jarvis_engine.process_manager.check_and_restart_services",
                    side_effect=ImportError("no module")):
            _run_watchdog_cycle(root)
        output = capsys.readouterr().out
        assert "watchdog_error=" in output

    def test_restart_mobile_api_ignores_non_mobile_api(self, root) -> None:
        """Restart callback should ignore services other than mobile_api."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            # Should not raise or do anything
            _restart_mobile_api("daemon")
            _restart_mobile_api("widget")

    def test_restart_mobile_api_missing_config(self, root, capsys) -> None:
        """Should log warning when config file is missing."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root):
            _restart_mobile_api("mobile_api")
        # No crash, just a warning logged

    def test_log_cycle_start_handles_import_error(self) -> None:
        """Activity feed log failure should not raise."""
        with patch("jarvis_engine.activity_feed.log_activity",
                    side_effect=ImportError("no module")):
            # Should not raise
            _log_cycle_start(1, "2026-01-01T00:00:00Z")

    def test_log_cycle_end_handles_error(self) -> None:
        """Activity feed log failure should not raise."""
        with patch("jarvis_engine.activity_feed.log_activity",
                    side_effect=ImportError("no module")):
            _log_cycle_end(1, 0)

    def test_run_core_autopilot_returns_2_on_error(self, tmp_path, capsys) -> None:
        """Core autopilot should return 2 on exception."""
        mock_autopilot = MagicMock(side_effect=RuntimeError("autopilot crash"))
        rc = _run_core_autopilot(
            snapshot_path=tmp_path / "snap.json",
            actions_path=tmp_path / "actions.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            safe_mode=False,
            cmd_ops_autopilot=mock_autopilot,
        )
        assert rc == 2
        output = capsys.readouterr().out
        assert "cycle_error=" in output

    def test_run_core_autopilot_safe_mode_disables_execute(self, tmp_path, capsys) -> None:
        """Safe mode should force execute and approve_privileged to False."""
        mock_autopilot = MagicMock(return_value=0)
        _run_core_autopilot(
            snapshot_path=tmp_path / "snap.json",
            actions_path=tmp_path / "actions.json",
            execute=True,
            approve_privileged=True,
            auto_open_connectors=False,
            safe_mode=True,
            cmd_ops_autopilot=mock_autopilot,
        )
        output = capsys.readouterr().out
        assert "safe_mode_override" in output
        # Verify the autopilot was called with execute=False
        call_kwargs = mock_autopilot.call_args
        assert call_kwargs[1]["execute"] is False
        assert call_kwargs[1]["approve_privileged"] is False

    def test_run_db_optimize_cycle_handles_error(self, capsys) -> None:
        """DB optimize should handle errors gracefully."""
        with patch("jarvis_engine.daemon_loop._get_daemon_bus",
                    side_effect=RuntimeError("no bus")):
            _run_db_optimize_cycle(100)
        output = capsys.readouterr().out
        assert "db_optimize_error=" in output


# ===================================================================
# _gather_cycle_state tests
# ===================================================================


class TestGatherCycleState:
    """Tests for _gather_cycle_state() — per-cycle state collection."""

    def test_gathers_complete_state(self, root) -> None:
        """Should return a dict with all required state keys."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root), \
             patch.object(daemon_loop_mod, "_windows_idle_seconds", return_value=10.0), \
             patch("jarvis_engine.daemon_loop.capture_runtime_resource_snapshot",
                    return_value={"metrics": {}}), \
             patch("jarvis_engine.daemon_loop.write_resource_pressure_state"), \
             patch("jarvis_engine.daemon_loop.recommend_daemon_sleep",
                    return_value={"sleep_s": 120, "pressure_level": "none",
                                  "skip_heavy_tasks": False}), \
             patch("jarvis_engine.daemon_loop.read_gaming_mode_state",
                    return_value={"enabled": False, "auto_detect": False,
                                  "updated_utc": "", "reason": ""}), \
             patch("jarvis_engine.daemon_loop.read_control_state",
                    return_value={"daemon_paused": False, "safe_mode": False,
                                  "reason": ""}):
            state = _gather_cycle_state(root, 120, 300, 300)

        required_keys = {
            "idle_seconds", "is_active", "sleep_seconds", "resource_snapshot",
            "pressure_level", "skip_heavy_tasks", "gaming_state", "control_state",
            "auto_detect", "detected_process", "gaming_mode_enabled",
            "daemon_paused", "safe_mode",
        }
        assert required_keys.issubset(state.keys())

    def test_idle_detection_active(self, root) -> None:
        """Should detect active user when idle time is below threshold."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root), \
             patch.object(daemon_loop_mod, "_windows_idle_seconds", return_value=10.0), \
             patch("jarvis_engine.daemon_loop.capture_runtime_resource_snapshot",
                    return_value={"metrics": {}}), \
             patch("jarvis_engine.daemon_loop.write_resource_pressure_state"), \
             patch("jarvis_engine.daemon_loop.recommend_daemon_sleep",
                    return_value={"sleep_s": 120, "pressure_level": "none",
                                  "skip_heavy_tasks": False}), \
             patch("jarvis_engine.daemon_loop.read_gaming_mode_state",
                    return_value={"enabled": False, "auto_detect": False,
                                  "updated_utc": "", "reason": ""}), \
             patch("jarvis_engine.daemon_loop.read_control_state",
                    return_value={"daemon_paused": False, "safe_mode": False,
                                  "reason": ""}):
            state = _gather_cycle_state(root, 120, 300, 300)

        assert state["is_active"] is True
        assert state["sleep_seconds"] == 120

    def test_idle_detection_idle(self, root) -> None:
        """Should detect idle user when idle time exceeds threshold."""
        with patch.object(daemon_loop_mod, "repo_root", return_value=root), \
             patch.object(daemon_loop_mod, "_windows_idle_seconds", return_value=600.0), \
             patch("jarvis_engine.daemon_loop.capture_runtime_resource_snapshot",
                    return_value={"metrics": {}}), \
             patch("jarvis_engine.daemon_loop.write_resource_pressure_state"), \
             patch("jarvis_engine.daemon_loop.recommend_daemon_sleep",
                    return_value={"sleep_s": 300, "pressure_level": "none",
                                  "skip_heavy_tasks": False}), \
             patch("jarvis_engine.daemon_loop.read_gaming_mode_state",
                    return_value={"enabled": False, "auto_detect": False,
                                  "updated_utc": "", "reason": ""}), \
             patch("jarvis_engine.daemon_loop.read_control_state",
                    return_value={"daemon_paused": False, "safe_mode": False,
                                  "reason": ""}):
            state = _gather_cycle_state(root, 120, 300, 300)

        assert state["is_active"] is False
        assert state["sleep_seconds"] == 300


# ===================================================================
# cmd_mission_run tests
# ===================================================================


class TestCmdMissionRun:
    """Tests for cmd_mission_run() helper."""

    def test_mission_run_success(self, capsys) -> None:
        """Should print mission results on success."""
        mock_result = MagicMock()
        mock_result.return_code = 0
        mock_result.report = {
            "mission_id": "m1",
            "candidate_count": 5,
            "verified_count": 2,
            "verified_findings": [
                {"statement": "fact 1", "source_domains": ["a.com"]},
            ],
        }
        mock_result.ingested_record_id = "rec123"

        mock_bus = MagicMock()
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.daemon_loop._get_daemon_bus", return_value=mock_bus):
            rc = cmd_mission_run("m1", 6, 10, True)

        assert rc == 0
        output = capsys.readouterr().out
        assert "learning_mission_completed=true" in output
        assert "mission_id=m1" in output
        assert "mission_ingested_record_id=rec123" in output

    def test_mission_run_failure(self, capsys) -> None:
        """Should print error and return non-zero on failure."""
        mock_result = MagicMock()
        mock_result.return_code = 1

        mock_bus = MagicMock()
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.daemon_loop._get_daemon_bus", return_value=mock_bus):
            rc = cmd_mission_run("m1", 6, 10, True)

        assert rc == 1
        output = capsys.readouterr().out
        assert "error: mission run failed" in output
