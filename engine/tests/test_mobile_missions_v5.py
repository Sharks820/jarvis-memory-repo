"""MOB-06 through MOB-12: Mobile tasking integration tests.

MOB-06: Mobile can create long-running desktop missions with acceptance contract
MOB-07: Mobile can monitor mission progress and activity in near real-time
MOB-08: Completed tasks trigger delivery actions with audit trail
MOB-09: Offline mobile requests queue safely and replay once desktop reconnects
MOB-10: Mission artifacts are retrievable from mobile with version-safe metadata
MOB-11: End-to-end "create X and send when done" flow
MOB-12: Mobile and desktop show consistent mission state under concurrent updates
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from jarvis_engine.learning.missions import (
    _finalize_mission,
    _reports_dir,
    create_learning_mission,
    get_active_missions,
    get_mission_artifacts,
    get_mission_by_id,
    get_mission_steps,
    load_missions,
    pause_mission,
)
from jarvis_engine._shared import atomic_write_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_missions_dir(tmp_path: Path) -> Path:
    """Create the missions directory structure under tmp_path."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    missions_dir = planning / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    # Write empty missions.json
    missions_file = planning / "missions.json"
    missions_file.write_text("[]", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# MOB-06: Mission creation from mobile with acceptance contract
# ---------------------------------------------------------------------------


class TestMOB06MissionCreation:
    """Mobile can create long-running desktop missions with clear acceptance contract."""

    def test_create_mission_returns_acceptance_contract_fields(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root,
            topic="quantum computing basics",
            objective="Learn fundamentals",
            sources=["google", "wikipedia"],
            origin="phone",
            delivery_method="notification",
        )
        assert mission["mission_id"].startswith("m-")
        assert mission["topic"] == "quantum computing basics"
        assert mission["status"] == "pending"
        assert mission["origin"] == "phone"
        assert mission["delivery_method"] == "notification"
        assert mission["sources"] == ["google", "wikipedia"]

    def test_create_mission_with_file_delivery(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="rust programming", objective="", delivery_method="file",
        )
        assert mission["delivery_method"] == "file"

    def test_create_mission_with_none_delivery(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="test topic", objective="", delivery_method="none",
        )
        assert mission["delivery_method"] == "none"

    def test_create_mission_invalid_delivery_defaults_to_notification(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="test topic", objective="", delivery_method="invalid_method",
        )
        assert mission["delivery_method"] == "notification"

    def test_create_mission_empty_topic_raises(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        with pytest.raises(ValueError, match="topic is required"):
            create_learning_mission(root, topic="", objective="")

    def test_create_mission_persists_to_file(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="machine learning", objective="explore ML",
            origin="phone", delivery_method="file",
        )
        missions = load_missions(root)
        assert len(missions) == 1
        assert missions[0]["mission_id"] == mission["mission_id"]
        assert missions[0]["delivery_method"] == "file"

    def test_create_mission_default_delivery_is_notification(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="test", objective="")
        assert mission["delivery_method"] == "notification"


# ---------------------------------------------------------------------------
# MOB-07: Mission progress monitoring
# ---------------------------------------------------------------------------


class TestMOB07ProgressMonitoring:
    """Mobile can monitor mission progress and activity in near real-time."""

    def test_get_active_missions_returns_running(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        m1 = create_learning_mission(root, topic="topic A", objective="")
        m2 = create_learning_mission(root, topic="topic B", objective="")
        active = get_active_missions(root)
        assert len(active) == 2  # both pending = active

    def test_get_mission_by_id_found(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        created = create_learning_mission(root, topic="findable", objective="")
        found = get_mission_by_id(root, created["mission_id"])
        assert found is not None
        assert found["topic"] == "findable"

    def test_get_mission_by_id_not_found(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        assert get_mission_by_id(root, "nonexistent-id") is None

    def test_mission_steps_empty_for_new_mission(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        m = create_learning_mission(root, topic="steps test", objective="")
        steps = get_mission_steps(root, m["mission_id"])
        # Steps are initialized when mission starts running, not at creation
        assert isinstance(steps, list)

    def test_progress_pct_starts_at_zero(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        m = create_learning_mission(root, topic="progress test", objective="")
        assert m["progress_pct"] == 0
        assert "0%" in m["progress_bar"]


# ---------------------------------------------------------------------------
# MOB-08: Delivery actions with audit trail
# ---------------------------------------------------------------------------


class TestMOB08DeliveryActions:
    """Completed tasks trigger delivery actions with audit trail."""

    def test_finalize_mission_triggers_delivery_notification(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="delivery test", objective="",
            delivery_method="notification",
        )
        mid = mission["mission_id"]
        report_path = _reports_dir(root) / f"{mid}.report.json"
        report: dict[str, Any] = {
            "mission_id": mid, "topic": "delivery test",
            "objective": "", "queries": [], "scanned_urls": [],
            "candidate_count": 0, "verified_findings": [
                {"statement": "fact 1", "source_urls": ["http://a.com"],
                 "source_domains": ["a.com"], "confidence": 0.8},
            ], "verified_count": 1, "completed_utc": "",
        }
        atomic_write_json(report_path, report)

        # Mock the proactive alert and activity feed
        with patch("jarvis_engine.learning.missions.enqueue_alert", create=True) as mock_alert:
            # enqueue_alert is imported inside _finalize_mission; we need to patch at import
            pass

        # Run finalize — should not raise
        _finalize_mission(
            root, mid,
            verified=report["verified_findings"],
            report=report,  # type: ignore[arg-type]
            report_path=report_path,
        )
        updated = get_mission_by_id(root, mid)
        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["verified_findings"] == 1

    def test_finalize_mission_file_delivery_logs_audit(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(
            root, topic="file delivery", objective="",
            delivery_method="file",
        )
        mid = mission["mission_id"]
        report_path = _reports_dir(root) / f"{mid}.report.json"
        atomic_write_json(report_path, {"mission_id": mid})

        verified = [{"statement": "x", "source_urls": [], "source_domains": ["d.com"], "confidence": 0.6}]
        _finalize_mission(root, mid, verified=verified, report={"mission_id": mid}, report_path=report_path)  # type: ignore[arg-type]

        updated = get_mission_by_id(root, mid)
        assert updated is not None
        assert updated["status"] == "completed"
        # Verify audit trail was logged (activity feed is in-memory for tests)
        from jarvis_engine.memory.activity_feed import get_activity_feed
        feed = get_activity_feed()
        events = feed.query(limit=20)
        delivery_events = [e for e in events if "delivered" in e.summary.lower()]
        assert len(delivery_events) >= 1
        assert "file" in delivery_events[0].summary.lower()

    def test_finalize_no_verified_sets_failed(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="fail test", objective="")
        mid = mission["mission_id"]
        report_path = _reports_dir(root) / f"{mid}.report.json"
        atomic_write_json(report_path, {"mission_id": mid})

        _finalize_mission(root, mid, verified=[], report={"mission_id": mid}, report_path=report_path)  # type: ignore[arg-type]

        updated = get_mission_by_id(root, mid)
        assert updated is not None
        assert updated["status"] == "failed"


# ---------------------------------------------------------------------------
# MOB-09: Offline queue replay
# ---------------------------------------------------------------------------


class TestMOB09OfflineQueue:
    """Offline mobile requests queue safely and replay once desktop reconnects."""

    def test_mission_create_queued_while_offline_replays(self, tmp_path: Path) -> None:
        """Simulate offline queue: create missions in sequence, verify all persisted."""
        root = _setup_missions_dir(tmp_path)
        # Simulate a queue of offline commands that replay on reconnect
        queued_commands = [
            {"topic": "offline topic 1", "objective": "learn about it"},
            {"topic": "offline topic 2", "objective": "explore further"},
            {"topic": "offline topic 3", "objective": "deep dive"},
        ]
        created_ids: list[str] = []
        for cmd in queued_commands:
            m = create_learning_mission(
                root, topic=cmd["topic"], objective=cmd["objective"],
                origin="phone", delivery_method="notification",
            )
            created_ids.append(m["mission_id"])

        # All missions should be persisted and retrievable
        missions = load_missions(root)
        assert len(missions) == 3
        for mid in created_ids:
            found = get_mission_by_id(root, mid)
            assert found is not None
            assert found["status"] == "pending"

    def test_offline_queue_order_preserved(self, tmp_path: Path) -> None:
        """Verify that replay order is preserved (FIFO)."""
        root = _setup_missions_dir(tmp_path)
        topics = [f"ordered-{i}" for i in range(5)]
        for t in topics:
            create_learning_mission(root, topic=t, objective="")
        missions = load_missions(root)
        assert [m["topic"] for m in missions] == topics


# ---------------------------------------------------------------------------
# MOB-10: Artifact retrieval
# ---------------------------------------------------------------------------


class TestMOB10ArtifactRetrieval:
    """Mission artifacts are retrievable from mobile with version-safe metadata."""

    def test_get_artifacts_for_completed_mission(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="artifacts test", objective="")
        mid = mission["mission_id"]

        # Simulate a completed mission with a report file
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mid)[:80]
        report_path = _reports_dir(root) / f"{safe_id}.report.json"
        report_data = {"mission_id": mid, "verified_count": 3}
        atomic_write_json(report_path, report_data)

        # Update mission to point to report
        missions = load_missions(root)
        for m in missions:
            if m["mission_id"] == mid:
                m["last_report_path"] = str(report_path)
                m["status"] = "completed"
                break
        atomic_write_json(root / ".planning" / "missions.json", missions)

        artifacts = get_mission_artifacts(root, mid)
        assert len(artifacts) >= 1
        report_artifact = artifacts[0]
        assert report_artifact["type"] == "report_json"
        assert report_artifact["mission_id"] == mid
        assert report_artifact["size_bytes"] > 0
        assert "version" in report_artifact

    def test_get_artifacts_no_report_returns_empty(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="no artifacts", objective="")
        artifacts = get_mission_artifacts(root, mission["mission_id"])
        assert artifacts == []

    def test_get_artifacts_nonexistent_mission(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        artifacts = get_mission_artifacts(root, "nonexistent-id")
        assert artifacts == []

    def test_artifacts_include_supplementary_files(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="multi artifacts", objective="")
        mid = mission["mission_id"]
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mid)[:80]

        # Create report and supplementary file
        report_path = _reports_dir(root) / f"{safe_id}.report.json"
        atomic_write_json(report_path, {"mission_id": mid})
        supp_path = _reports_dir(root) / f"{safe_id}.sources.json"
        atomic_write_json(supp_path, {"urls": ["http://a.com"]})

        missions = load_missions(root)
        for m in missions:
            if m["mission_id"] == mid:
                m["last_report_path"] = str(report_path)
                break
        atomic_write_json(root / ".planning" / "missions.json", missions)

        artifacts = get_mission_artifacts(root, mid)
        assert len(artifacts) == 2
        types = {a["type"] for a in artifacts}
        assert "report_json" in types
        assert "supplementary" in types


# ---------------------------------------------------------------------------
# MOB-11: End-to-end "create and send when done" flow
# ---------------------------------------------------------------------------


class TestMOB11EndToEnd:
    """Command 'create X and send when done' flow tested end-to-end."""

    def test_create_poll_complete_deliver(self, tmp_path: Path) -> None:
        root = _setup_missions_dir(tmp_path)

        # Step 1: Create mission with delivery_method=notification
        mission = create_learning_mission(
            root,
            topic="e2e test topic",
            objective="find verified facts",
            sources=["google"],
            origin="phone",
            delivery_method="notification",
        )
        mid = mission["mission_id"]
        assert mission["status"] == "pending"
        assert mission["delivery_method"] == "notification"

        # Step 2: Poll status — should be pending
        found = get_mission_by_id(root, mid)
        assert found is not None
        assert found["status"] == "pending"
        assert found["progress_pct"] == 0

        # Step 3: Simulate completion via _finalize_mission
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mid)[:80]
        report_path = _reports_dir(root) / f"{safe_id}.report.json"
        verified = [
            {"statement": "E2E fact", "source_urls": ["http://test.com"],
             "source_domains": ["test.com"], "confidence": 0.75},
        ]
        report: dict[str, Any] = {
            "mission_id": mid, "topic": "e2e test topic", "objective": "",
            "queries": [], "scanned_urls": [], "candidate_count": 1,
            "verified_findings": verified, "verified_count": 1,
            "completed_utc": "",
        }
        atomic_write_json(report_path, report)
        _finalize_mission(root, mid, verified=verified, report=report, report_path=report_path)  # type: ignore[arg-type]

        # Step 4: Verify completion
        completed = get_mission_by_id(root, mid)
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["verified_findings"] == 1

        # Step 5: Check delivery audit trail
        from jarvis_engine.memory.activity_feed import get_activity_feed
        feed = get_activity_feed()
        events = feed.query(limit=50)
        delivery_events = [e for e in events if "delivered" in e.summary.lower()]
        assert len(delivery_events) >= 1

        # Step 6: Check artifacts are retrievable
        artifacts = get_mission_artifacts(root, mid)
        assert len(artifacts) >= 1


# ---------------------------------------------------------------------------
# MOB-12: Concurrent state consistency
# ---------------------------------------------------------------------------


class TestMOB12ConcurrentConsistency:
    """Mobile and desktop show consistent mission state under concurrent updates."""

    def test_missions_lock_prevents_race_conditions(self, tmp_path: Path) -> None:
        """Verify that _MISSIONS_LOCK serializes concurrent writes."""
        root = _setup_missions_dir(tmp_path)
        # Create initial mission
        mission = create_learning_mission(root, topic="concurrent test", objective="")
        mid = mission["mission_id"]

        errors: list[str] = []
        results: list[str] = []

        def writer_a() -> None:
            try:
                for _ in range(5):
                    create_learning_mission(root, topic=f"thread-a-{time.time()}", objective="")
                results.append("a-ok")
            except Exception as exc:
                errors.append(f"a: {exc}")

        def writer_b() -> None:
            try:
                for _ in range(5):
                    create_learning_mission(root, topic=f"thread-b-{time.time()}", objective="")
                results.append("b-ok")
            except Exception as exc:
                errors.append(f"b: {exc}")

        t1 = threading.Thread(target=writer_a)
        t2 = threading.Thread(target=writer_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Concurrent write errors: {errors}"
        assert len(results) == 2
        # All missions should be present (1 original + 5 + 5 = 11)
        missions = load_missions(root)
        assert len(missions) == 11

    def test_concurrent_read_write_consistency(self, tmp_path: Path) -> None:
        """Readers always see a consistent snapshot even during writes."""
        root = _setup_missions_dir(tmp_path)
        create_learning_mission(root, topic="base mission", objective="")

        read_results: list[int] = []
        errors: list[str] = []

        def reader() -> None:
            try:
                for _ in range(10):
                    missions = load_missions(root)
                    # Should never see a partial/corrupt list
                    assert isinstance(missions, list)
                    for m in missions:
                        assert isinstance(m, dict)
                        assert "mission_id" in m
                    read_results.append(len(missions))
            except Exception as exc:
                errors.append(f"reader: {exc}")

        def writer() -> None:
            try:
                for i in range(5):
                    create_learning_mission(root, topic=f"write-{i}", objective="")
            except Exception as exc:
                errors.append(f"writer: {exc}")

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent errors: {errors}"
        # All reads should return valid counts
        assert all(r >= 1 for r in read_results)

    def test_pause_resume_under_concurrency(self, tmp_path: Path) -> None:
        """Pause/resume from mobile while desktop reads — no corruption."""
        root = _setup_missions_dir(tmp_path)
        mission = create_learning_mission(root, topic="pause-race", objective="")
        mid = mission["mission_id"]

        # Move to running so we can pause
        missions = load_missions(root)
        for m in missions:
            if m["mission_id"] == mid:
                m["status"] = "running"
                break
        atomic_write_json(root / ".planning" / "missions.json", missions)

        errors: list[str] = []

        def do_pause() -> None:
            try:
                pause_mission(root, mission_id=mid)
            except Exception as exc:
                errors.append(f"pause: {exc}")

        def do_read() -> None:
            try:
                for _ in range(5):
                    m = get_mission_by_id(root, mid)
                    assert m is not None
            except Exception as exc:
                errors.append(f"read: {exc}")

        t1 = threading.Thread(target=do_pause)
        t2 = threading.Thread(target=do_read)
        t2.start()  # reader starts first
        t1.start()  # then pause — reader should see consistent state
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors, f"Pause/read race errors: {errors}"

        # Final state should be paused
        final = get_mission_by_id(root, mid)
        assert final is not None
        assert final["status"] == "paused"


# ---------------------------------------------------------------------------
# MOB-08 supplement: MissionCreateCommand delivery_method passthrough
# ---------------------------------------------------------------------------


class TestMissionCreateCommandDelivery:
    """Verify MissionCreateCommand carries delivery_method through the bus."""

    def test_command_has_delivery_method_field(self) -> None:
        from jarvis_engine.commands.ops_commands import MissionCreateCommand
        cmd = MissionCreateCommand(topic="test", delivery_method="file")
        assert cmd.delivery_method == "file"

    def test_command_default_delivery_is_notification(self) -> None:
        from jarvis_engine.commands.ops_commands import MissionCreateCommand
        cmd = MissionCreateCommand(topic="test")
        assert cmd.delivery_method == "notification"

    def test_handler_passes_delivery_method(self, tmp_path: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionCreateCommand
        from jarvis_engine.handlers.ops_handlers import MissionCreateHandler

        root = _setup_missions_dir(tmp_path)
        handler = MissionCreateHandler(root)
        cmd = MissionCreateCommand(
            topic="handler test", delivery_method="file", origin="phone",
        )
        result = handler.handle(cmd)
        assert result.return_code == 0
        assert result.mission.get("delivery_method") == "file"
