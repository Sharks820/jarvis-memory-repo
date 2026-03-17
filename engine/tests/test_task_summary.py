"""Tests for task summary generation.

Tests generate_task_summary() and TaskSummary dataclass:
  - Returns TaskSummary with correct fields from a populated AgentTask
  - Handles empty plan_json gracefully
  - Handles unparseable plan_json gracefully
  - Extracts file paths from plan steps with tool_name=="file"
  - Summary text is human-readable
"""
from __future__ import annotations

import json
from dataclasses import asdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t1",
    goal: str = "Build a Unity scene",
    status: str = "done",
    plan_json: str = "[]",
    step_index: int = 0,
    tokens_used: int = 0,
    error_count: int = 0,
    last_error: str = "",
):
    from jarvis_engine.agent.state_store import AgentTask

    return AgentTask(
        task_id=task_id,
        goal=goal,
        status=status,
        plan_json=plan_json,
        step_index=step_index,
        tokens_used=tokens_used,
        error_count=error_count,
        last_error=last_error,
    )


# ---------------------------------------------------------------------------
# TaskSummary dataclass tests
# ---------------------------------------------------------------------------


class TestTaskSummaryDataclass:
    def test_fields_present(self):
        from jarvis_engine.agent.task_summary import TaskSummary

        summary = TaskSummary(
            task_id="t1",
            goal="test goal",
            status="done",
            steps_completed=3,
            tokens_used=1500,
            error_count=0,
            files_touched=[],
            summary_text="All done.",
        )
        assert summary.task_id == "t1"
        assert summary.goal == "test goal"
        assert summary.status == "done"
        assert summary.steps_completed == 3
        assert summary.tokens_used == 1500
        assert summary.error_count == 0
        assert summary.files_touched == []
        assert summary.summary_text == "All done."

    def test_dataclass_asdict(self):
        from jarvis_engine.agent.task_summary import TaskSummary

        summary = TaskSummary(
            task_id="t2",
            goal="g",
            status="failed",
            steps_completed=1,
            tokens_used=200,
            error_count=2,
            files_touched=["a.cs"],
            summary_text="Failed after 2 errors.",
        )
        d = asdict(summary)
        assert d["task_id"] == "t2"
        assert d["files_touched"] == ["a.cs"]


# ---------------------------------------------------------------------------
# generate_task_summary() tests
# ---------------------------------------------------------------------------


class TestGenerateTaskSummary:
    def test_basic_done_task(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(
            task_id="abc",
            goal="rotate cube",
            status="done",
            step_index=5,
            tokens_used=3000,
            error_count=0,
        )
        summary = generate_task_summary(task)

        assert summary.task_id == "abc"
        assert summary.goal == "rotate cube"
        assert summary.status == "done"
        assert summary.steps_completed == 5
        assert summary.tokens_used == 3000
        assert summary.error_count == 0

    def test_empty_plan_json(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(plan_json="[]")
        summary = generate_task_summary(task)

        assert summary.files_touched == []
        assert isinstance(summary.summary_text, str)
        assert len(summary.summary_text) > 0

    def test_invalid_plan_json_does_not_raise(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(plan_json="{not valid json[}")
        summary = generate_task_summary(task)

        # Should return sensible defaults
        assert summary.files_touched == []
        assert summary.steps_completed == task.step_index

    def test_extracts_files_from_file_tool_steps(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        plan = [
            {
                "step_index": 0,
                "tool_name": "file",
                "description": "Create cube script",
                "params": {"path": "Assets/Cube.cs", "content": "..."},
                "depends_on": [],
            },
            {
                "step_index": 1,
                "tool_name": "shell",
                "description": "Run build",
                "params": {"command": "make"},
                "depends_on": [],
            },
            {
                "step_index": 2,
                "tool_name": "file",
                "description": "Create scene file",
                "params": {"path": "Assets/Scene.unity"},
                "depends_on": [],
            },
        ]
        task = _make_task(plan_json=json.dumps(plan), step_index=3)
        summary = generate_task_summary(task)

        assert "Assets/Cube.cs" in summary.files_touched
        assert "Assets/Scene.unity" in summary.files_touched
        # Shell step should not add to files_touched
        assert len(summary.files_touched) == 2

    def test_file_tool_step_without_path_skipped(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        plan = [
            {
                "step_index": 0,
                "tool_name": "file",
                "description": "Write something",
                "params": {},  # no 'path' key
                "depends_on": [],
            },
        ]
        task = _make_task(plan_json=json.dumps(plan))
        summary = generate_task_summary(task)

        assert summary.files_touched == []

    def test_summary_text_contains_key_info(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(
            goal="Create a rotating cube scene",
            status="done",
            step_index=4,
            tokens_used=2500,
            error_count=1,
        )
        summary = generate_task_summary(task)

        text = summary.summary_text.lower()
        # Summary should mention steps or status or goal-related info
        assert any(word in text for word in ("step", "done", "complet", "token", "error", "task"))

    def test_failed_task_summary(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(
            status="failed",
            error_count=3,
            last_error="Build failed: NullReferenceException",
        )
        summary = generate_task_summary(task)

        assert summary.status == "failed"
        assert summary.error_count == 3

    def test_tokens_used_reflected(self):
        from jarvis_engine.agent.task_summary import generate_task_summary

        task = _make_task(tokens_used=12345)
        summary = generate_task_summary(task)
        assert summary.tokens_used == 12345

    def test_duplicate_file_paths_deduplicated(self):
        """If the same file is touched multiple times in the plan, report it once."""
        from jarvis_engine.agent.task_summary import generate_task_summary

        plan = [
            {
                "step_index": 0,
                "tool_name": "file",
                "description": "Write file",
                "params": {"path": "Assets/Shared.cs"},
                "depends_on": [],
            },
            {
                "step_index": 1,
                "tool_name": "file",
                "description": "Overwrite file",
                "params": {"path": "Assets/Shared.cs"},
                "depends_on": [],
            },
        ]
        task = _make_task(plan_json=json.dumps(plan), step_index=2)
        summary = generate_task_summary(task)

        assert summary.files_touched.count("Assets/Shared.cs") == 1
