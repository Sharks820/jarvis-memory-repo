"""Tests for ActionAuditor — bot governance audit trail."""
from __future__ import annotations

import json
import threading
from pathlib import Path


from jarvis_engine.security.action_auditor import ActionAuditor


class TestActionAuditor:
    """Test suite for ActionAuditor."""

    def test_log_action(self, tmp_path: Path) -> None:
        """Log one action, verify count == 1 and entry structure."""
        auditor = ActionAuditor(tmp_path / "audit")
        auditor.log_action(
            action_type="command",
            detail="user asked about weather",
            trigger="user_command",
        )
        assert auditor.action_count() == 1

        recent = auditor.recent_actions(limit=1)
        assert len(recent) == 1
        entry = recent[0]
        assert entry["action_type"] == "command"
        assert entry["detail"] == "user asked about weather"
        assert entry["trigger"] == "user_command"
        assert "timestamp" in entry
        assert "input_hash" in entry
        assert len(entry["input_hash"]) == 16  # first 16 hex chars of SHA-256

    def test_log_action_with_resource_usage(self, tmp_path: Path) -> None:
        """Log an action with resource_usage dict and verify it is stored."""
        auditor = ActionAuditor(tmp_path / "audit")
        usage = {"tokens": 150, "cpu_time": 0.32, "memory": 4096}
        auditor.log_action(
            action_type="api_call",
            detail="called LLM gateway",
            trigger="internal",
            resource_usage=usage,
        )
        entry = auditor.recent_actions(limit=1)[0]
        assert entry["resource_usage"] == usage

    def test_recent_actions(self, tmp_path: Path) -> None:
        """Log 5 actions, retrieve last 3."""
        auditor = ActionAuditor(tmp_path / "audit")
        for i in range(5):
            auditor.log_action(
                action_type="command",
                detail=f"action {i}",
                trigger="user_command",
            )
        recent = auditor.recent_actions(limit=3)
        assert len(recent) == 3
        # Most recent should be last (action 4, 3, 2)
        assert recent[0]["detail"] == "action 2"
        assert recent[1]["detail"] == "action 3"
        assert recent[2]["detail"] == "action 4"

    def test_action_log_persists(self, tmp_path: Path) -> None:
        """Verify JSONL file exists and has valid content after logging."""
        log_dir = tmp_path / "audit"
        auditor = ActionAuditor(log_dir)
        auditor.log_action(
            action_type="file_access",
            detail="read config.json",
            trigger="scheduled",
        )
        auditor.log_action(
            action_type="learning",
            detail="learned user preference",
            trigger="internal",
        )

        jsonl_path = log_dir / "action_audit.jsonl"
        assert jsonl_path.exists()

        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "action_type" in entry
            assert "input_hash" in entry

    def test_daily_summary(self, tmp_path: Path) -> None:
        """Log mixed types and triggers, verify by_type and by_trigger counts."""
        auditor = ActionAuditor(tmp_path / "audit")
        actions = [
            ("command", "do X", "user_command"),
            ("command", "do Y", "user_command"),
            ("api_call", "call API", "internal"),
            ("proactive", "check schedule", "proactive_engine"),
            ("learning", "learn pref", "internal"),
            ("proactive", "nudge user", "scheduled"),
        ]
        for atype, detail, trigger in actions:
            auditor.log_action(action_type=atype, detail=detail, trigger=trigger)

        summary = auditor.daily_summary()
        assert summary["total_actions"] == 6
        assert summary["by_type"]["command"] == 2
        assert summary["by_type"]["api_call"] == 1
        assert summary["by_type"]["proactive"] == 2
        assert summary["by_type"]["learning"] == 1
        assert summary["by_trigger"]["user_command"] == 2
        assert summary["by_trigger"]["internal"] == 2
        assert summary["by_trigger"]["proactive_engine"] == 1
        assert summary["by_trigger"]["scheduled"] == 1

    def test_detail_truncation(self, tmp_path: Path) -> None:
        """Details longer than 500 chars are truncated."""
        auditor = ActionAuditor(tmp_path / "audit")
        long_detail = "x" * 1000
        auditor.log_action(
            action_type="command",
            detail=long_detail,
            trigger="user_command",
        )
        entry = auditor.recent_actions(limit=1)[0]
        assert len(entry["detail"]) == 500

    def test_thread_safety(self, tmp_path: Path) -> None:
        """Concurrent logging from multiple threads should not lose entries."""
        auditor = ActionAuditor(tmp_path / "audit")
        num_threads = 10
        actions_per_thread = 50

        def worker(thread_id: int) -> None:
            for i in range(actions_per_thread):
                auditor.log_action(
                    action_type="command",
                    detail=f"thread {thread_id} action {i}",
                    trigger="internal",
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert auditor.action_count() == num_threads * actions_per_thread

    def test_ring_buffer_overflow(self, tmp_path: Path) -> None:
        """In-memory buffer caps at 500 entries; file still has all."""
        auditor = ActionAuditor(tmp_path / "audit")
        for i in range(600):
            auditor.log_action(
                action_type="command",
                detail=f"action {i}",
                trigger="user_command",
            )
        # In-memory ring buffer holds max 500
        recent = auditor.recent_actions(limit=600)
        assert len(recent) == 500
        # But total count tracks all
        assert auditor.action_count() == 600
        # File has all 600
        jsonl_path = tmp_path / "audit" / "action_audit.jsonl"
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 600
