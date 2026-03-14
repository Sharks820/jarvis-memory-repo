"""Tests for memory/brain.py — Brain/memory engine (legacy JSONL path)."""

from __future__ import annotations

import json

import pytest

from jarvis_engine.memory.brain import (
    BRANCH_RULES,
    BrainRecord,
    _extract_fact_candidates,
    _pick_branch,
    _summarize,
    _tokenize,
    brain_compact,
    brain_regression_report,
    brain_status,
    build_context_packet,
    ingest_brain_record,
)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:

    def test_basic_tokens(self):
        tokens = _tokenize("hello world python")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens

    def test_underscores_split(self):
        tokens = _tokenize("some_function_name")
        assert "some" in tokens
        assert "function" in tokens
        assert "name" in tokens

    def test_pure_digits_excluded(self):
        tokens = _tokenize("version 42 release")
        assert "42" not in tokens
        assert "version" in tokens
        assert "release" in tokens

    def test_short_tokens_excluded(self):
        tokens = _tokenize("I x do it")
        assert "do" in tokens
        # Single char "I" and "x" should be excluded (< 2 chars)
        assert "i" not in tokens
        assert "x" not in tokens

    def test_empty_string(self):
        assert _tokenize("") == []


# ---------------------------------------------------------------------------
# _pick_branch
# ---------------------------------------------------------------------------


class TestPickBranch:

    def test_ops_keywords(self):
        tokens = _tokenize("calendar meeting schedule")
        assert _pick_branch(tokens) == "ops"

    def test_coding_keywords(self):
        tokens = _tokenize("python code refactor test")
        assert _pick_branch(tokens) == "coding"

    def test_health_keywords(self):
        tokens = _tokenize("prescription doctor pharmacy")
        assert _pick_branch(tokens) == "health"

    def test_general_fallback(self):
        tokens = _tokenize("something random totally unrelated")
        assert _pick_branch(tokens) == "general"

    def test_empty_tokens(self):
        assert _pick_branch([]) == "general"

    def test_security_keywords(self):
        tokens = _tokenize("auth password security guard")
        assert _pick_branch(tokens) == "security"


# ---------------------------------------------------------------------------
# _summarize
# ---------------------------------------------------------------------------


class TestSummarize:

    def test_short_text_unchanged(self):
        text = "Hello world"
        assert _summarize(text) == "Hello world"

    def test_long_text_trimmed(self):
        text = "a " * 200
        result = _summarize(text, max_len=50)
        assert len(result) <= 50
        assert result.endswith("...(trimmed)")

    def test_whitespace_normalized(self):
        text = "hello   \n\n  world"
        assert _summarize(text) == "hello world"


# ---------------------------------------------------------------------------
# _extract_fact_candidates
# ---------------------------------------------------------------------------


class TestExtractFactCandidates:

    def test_safe_mode_enable(self):
        facts = _extract_fact_candidates("Enable safe mode now", "security")
        keys = [f["key"] for f in facts]
        assert "runtime.safe_mode" in keys

    def test_gaming_mode_enable(self):
        facts = _extract_fact_candidates("Enable gaming mode", "gaming")
        keys = [f["key"] for f in facts]
        assert "runtime.gaming_mode" in keys

    def test_pause_daemon(self):
        facts = _extract_fact_candidates("Pause the daemon now", "ops")
        keys = [f["key"] for f in facts]
        assert "runtime.daemon_paused" in keys

    def test_resume_daemon(self):
        facts = _extract_fact_candidates("Resume daemon operation", "ops")
        keys = [f["key"] for f in facts]
        assert "runtime.daemon_paused" in keys

    def test_no_match_general_branch(self):
        facts = _extract_fact_candidates("nothing special here", "general")
        # No facts extracted for general branch with no keywords
        assert len(facts) == 0

    def test_non_general_branch_gets_focus_fact(self):
        facts = _extract_fact_candidates("nothing special here", "coding")
        keys = [f["key"] for f in facts]
        assert "branch.last_focus.coding" in keys

    def test_max_8_facts(self):
        # Even with many matches, should be capped at 8
        text = (
            "Enable safe mode, enable gaming mode, auto enable gaming, "
            "pause daemon, resume autopilot, block spam call, "
            "enable owner guard, organize today schedule"
        )
        facts = _extract_fact_candidates(text, "ops")
        assert len(facts) <= 8


# ---------------------------------------------------------------------------
# ingest_brain_record
# ---------------------------------------------------------------------------


class TestIngestBrainRecord:

    def test_basic_ingest(self, tmp_path):
        record = ingest_brain_record(
            tmp_path,
            source="cli",
            kind="episodic",
            task_id="t1",
            content="User discussed Python code refactoring strategy",
        )
        assert isinstance(record, BrainRecord)
        assert record.source == "cli"
        assert record.kind == "episodic"
        assert record.record_id
        assert record.content_hash
        assert record.branch in list(BRANCH_RULES.keys()) + ["general"]

    def test_empty_content_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Empty content"):
            ingest_brain_record(
                tmp_path, source="cli", kind="episodic",
                task_id="t1", content="   ",
            )

    def test_dedup_returns_existing(self, tmp_path):
        r1 = ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Unique test content for dedup",
        )
        r2 = ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Unique test content for dedup",
        )
        assert r2.branch == "deduped"
        assert r2.summary == "deduped"

    def test_tags_stored(self, tmp_path):
        record = ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Meeting with team about the project plan",
            tags=["meeting", "team", "Meeting"],
        )
        assert "meeting" in record.tags
        assert "team" in record.tags
        # Duplicates removed (case-insensitive)
        assert len(record.tags) == len(set(record.tags))

    def test_confidence_clamped(self, tmp_path):
        record = ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Some content for confidence test xxx",
            confidence=5.0,
        )
        assert record.confidence <= 1.0

    def test_records_file_created(self, tmp_path):
        ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="File creation test content yyy",
        )
        records_path = tmp_path / ".planning" / "brain" / "records.jsonl"
        assert records_path.exists()
        lines = records_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["source"] == "cli"

    def test_index_file_created(self, tmp_path):
        ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Index creation test content zzz",
        )
        index_path = tmp_path / ".planning" / "brain" / "index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert "branches" in data
        assert "hash_to_record_id" in data


# ---------------------------------------------------------------------------
# build_context_packet
# ---------------------------------------------------------------------------


class TestBuildContextPacket:

    def test_basic_packet(self, tmp_path):
        ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Meeting about Python code refactoring plan",
        )
        packet = build_context_packet(tmp_path, query="python refactoring")
        assert packet["query"] == "python refactoring"
        assert isinstance(packet["selected"], list)
        assert isinstance(packet["canonical_facts"], list)
        assert packet["max_items"] == 10
        assert packet["max_chars"] == 2400

    def test_empty_brain_returns_empty_selected(self, tmp_path):
        packet = build_context_packet(tmp_path, query="anything")
        assert packet["selected"] == []
        assert packet["selected_count"] == 0

    def test_max_items_respected(self, tmp_path):
        for i in range(20):
            ingest_brain_record(
                tmp_path, source="cli", kind="episodic",
                task_id=f"t{i}", content=f"Python code item number {i} with unique hash {i * 1000}",
            )
        packet = build_context_packet(tmp_path, query="python code", max_items=3)
        assert len(packet["selected"]) <= 3


# ---------------------------------------------------------------------------
# brain_compact
# ---------------------------------------------------------------------------


class TestBrainCompact:

    def test_below_threshold_no_compact(self, tmp_path):
        ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Small brain content for compact test",
        )
        result = brain_compact(tmp_path, keep_recent=100)
        assert result["compacted"] is False
        assert result["reason"] == "below_threshold"

    def test_compact_trims_old_records(self, tmp_path):
        for i in range(15):
            ingest_brain_record(
                tmp_path, source="cli", kind="episodic",
                task_id=f"t{i}", content=f"Record number {i} for compact trimming test {i * 999}",
            )
        result = brain_compact(tmp_path, keep_recent=5)
        assert result["compacted"] is True
        assert result["kept_records"] == 5
        assert result["compacted_records"] == 10


# ---------------------------------------------------------------------------
# brain_regression_report
# ---------------------------------------------------------------------------


class TestBrainRegressionReport:

    def test_empty_brain(self, tmp_path):
        report = brain_regression_report(tmp_path)
        assert report["status"] == "pass"
        assert report["total_records"] == 0
        assert report["duplicate_ratio"] == 0.0

    def test_with_records(self, tmp_path):
        for i in range(5):
            ingest_brain_record(
                tmp_path, source="cli", kind="episodic",
                task_id=f"t{i}", content=f"Regression report test item {i} unique hash {i * 777}",
            )
        report = brain_regression_report(tmp_path)
        assert report["total_records"] == 5
        assert report["duplicate_ratio"] == 0.0
        assert report["branch_count"] >= 1
        assert report["generated_utc"]


# ---------------------------------------------------------------------------
# brain_status
# ---------------------------------------------------------------------------


class TestBrainStatus:

    def test_empty_brain_status(self, tmp_path):
        status = brain_status(tmp_path)
        assert status["branch_count"] == 0
        assert status["fact_count"] == 0
        assert "regression" in status

    def test_status_after_ingest(self, tmp_path):
        ingest_brain_record(
            tmp_path, source="cli", kind="episodic",
            task_id="t1", content="Python code refactoring discussion for status test",
        )
        status = brain_status(tmp_path)
        assert status["branch_count"] >= 1
        assert len(status["branches"]) >= 1
        assert status["updated_utc"]
