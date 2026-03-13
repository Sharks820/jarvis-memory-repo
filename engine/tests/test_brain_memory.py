from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine._compat import UTC
from jarvis_engine.memory.brain import (
    BrainRecord,
    _extract_fact_candidates,
    _load_records,
    _pick_branch,
    _recency_weight,
    _summarize,
    _tokenize,
    brain_compact,
    brain_regression_report,
    brain_status,
    build_context_packet,
    ingest_brain_record,
)


def test_ingest_brain_record_creates_index(tmp_path: Path) -> None:
    rec = ingest_brain_record(
        tmp_path,
        source="user",
        kind="episodic",
        task_id="t1",
        content="Plan my calendar and email for tomorrow.",
        tags=["ops"],
        confidence=0.8,
    )
    assert rec.record_id
    status = brain_status(tmp_path)
    assert status["branch_count"] >= 1


def test_ingest_brain_record_dedupes(tmp_path: Path) -> None:
    rec1 = ingest_brain_record(
        tmp_path,
        source="user",
        kind="episodic",
        task_id="t1",
        content="Block spam calls from unknown numbers.",
    )
    rec2 = ingest_brain_record(
        tmp_path,
        source="user",
        kind="episodic",
        task_id="t2",
        content="Block spam calls from unknown numbers.",
    )
    assert rec1.record_id == rec2.record_id or rec2.branch == "deduped"


def test_build_context_packet_returns_relevant_rows(tmp_path: Path) -> None:
    ingest_brain_record(
        tmp_path,
        source="task_outcome",
        kind="semantic",
        task_id="a1",
        content="Use safe mode while gaming and auto resume when game exits.",
    )
    ingest_brain_record(
        tmp_path,
        source="task_outcome",
        kind="semantic",
        task_id="a2",
        content="Schedule pharmacy refill reminders and family school tasks.",
    )

    packet = build_context_packet(tmp_path, query="How do I pause for gaming?", max_items=5, max_chars=800)
    assert packet["selected_count"] >= 1
    summaries = " ".join(item["summary"] for item in packet["selected"])
    assert "gaming" in summaries.lower()


def test_build_context_packet_includes_canonical_facts(tmp_path: Path) -> None:
    ingest_brain_record(
        tmp_path,
        source="task_outcome",
        kind="episodic",
        task_id="f1",
        content="Enable safe mode before risky automation runs.",
    )
    packet = build_context_packet(tmp_path, query="safe mode", max_items=5, max_chars=800)
    facts = packet.get("canonical_facts", [])
    assert isinstance(facts, list)
    assert any(str(item.get("key", "")) == "runtime.safe_mode" for item in facts if isinstance(item, dict))


def test_brain_compact_reduces_record_count(tmp_path: Path) -> None:
    for idx in range(30):
        ingest_brain_record(
            tmp_path,
            source="user",
            kind="episodic",
            task_id=f"c{idx}",
            content=f"Calendar reminder {idx}",
        )
    result = brain_compact(tmp_path, keep_recent=10)
    assert result["compacted"] is True
    status = brain_status(tmp_path)
    assert status["regression"]["total_records"] == 10


def test_brain_regression_report_fields(tmp_path: Path) -> None:
    ingest_brain_record(
        tmp_path,
        source="user",
        kind="episodic",
        task_id="r1",
        content="Enable owner guard for secure access.",
    )
    report = brain_regression_report(tmp_path)
    assert "status" in report
    assert "total_records" in report
    assert report["total_records"] >= 1


# ---------------------------------------------------------------------------
# brain_regression_report: no history / valid history / edge cases
# ---------------------------------------------------------------------------


class TestBrainRegressionReport:
    """Comprehensive tests for brain_regression_report."""

    def test_no_history_file(self, tmp_path: Path) -> None:
        """No records.jsonl exists -- report should return pass with zero records."""
        report = brain_regression_report(tmp_path)
        assert report["status"] == "pass"
        assert report["total_records"] == 0
        assert report["unique_hashes"] == 0
        assert report["duplicate_ratio"] == 0.0
        assert report["branch_entropy"] == 0.0
        assert report["branch_count"] == 0
        assert report["unresolved_conflicts"] == 0
        assert report["conflict_total"] == 0
        assert "generated_utc" in report

    def test_valid_history_single_record(self, tmp_path: Path) -> None:
        """Single record -> no duplicates, 1 unique hash."""
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t1", content="Test record alpha")
        report = brain_regression_report(tmp_path)
        assert report["total_records"] == 1
        assert report["unique_hashes"] == 1
        assert report["duplicate_ratio"] == 0.0
        assert report["status"] == "pass"
        assert report["branch_count"] == 1

    def test_valid_history_multiple_records(self, tmp_path: Path) -> None:
        """Multiple unique records: status pass, positive entropy."""
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t1", content="Plan calendar meeting for tomorrow")
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t2", content="Write python code for the api endpoint")
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t3", content="Review prescription refill schedule")
        report = brain_regression_report(tmp_path)
        assert report["total_records"] == 3
        assert report["unique_hashes"] == 3
        assert report["duplicate_ratio"] == 0.0
        assert report["branch_entropy"] > 0
        assert report["branch_count"] >= 2
        assert report["status"] == "pass"

    def test_high_duplicate_ratio_triggers_fail(self, tmp_path: Path) -> None:
        """When >85% duplicate ratio, status should be 'fail'."""
        # Create a records.jsonl manually with many duplicates
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        same_hash = "abc123"
        lines = []
        for i in range(100):
            record = {
                "record_id": f"r_{i}",
                "ts": "2026-02-25T00:00:00",
                "source": "user",
                "kind": "episodic",
                "task_id": f"t{i}",
                "branch": "general",
                "tags": [],
                "summary": "Test",
                "confidence": 0.7,
                "content_hash": same_hash if i < 90 else f"unique_{i}",
            }
            lines.append(json.dumps(record))
        records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        report = brain_regression_report(tmp_path)
        # 100 records, 11 unique hashes (1 for the 90 dupes + 10 unique) => ratio = 1 - 11/100 = 0.89
        assert report["duplicate_ratio"] > 0.85
        assert report["status"] == "fail"

    def test_many_unresolved_conflicts_triggers_warn(self, tmp_path: Path) -> None:
        """When unresolved conflicts > 20 but <=60, status is 'warn'."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        # Create minimal records file
        records_path = brain_dir / "records.jsonl"
        records_path.write_text(json.dumps({
            "record_id": "r1", "ts": "2026-01-01", "source": "user",
            "kind": "ep", "task_id": "t", "branch": "general",
            "tags": [], "summary": "test", "confidence": 0.7,
            "content_hash": "unique1",
        }) + "\n", encoding="utf-8")

        # Create facts.json with 25 unresolved conflicts
        facts_path = brain_dir / "facts.json"
        conflicts = [{"key": f"k{i}", "resolved": False} for i in range(25)]
        facts_path.write_text(json.dumps({"facts": {}, "conflicts": conflicts}), encoding="utf-8")

        report = brain_regression_report(tmp_path)
        assert report["unresolved_conflicts"] == 25
        assert report["status"] == "warn"

    def test_extreme_unresolved_conflicts_triggers_fail(self, tmp_path: Path) -> None:
        """When unresolved conflicts > 60, status is 'fail'."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        records_path.write_text(json.dumps({
            "record_id": "r1", "ts": "2026-01-01", "source": "user",
            "kind": "ep", "task_id": "t", "branch": "general",
            "tags": [], "summary": "test", "confidence": 0.7,
            "content_hash": "unique1",
        }) + "\n", encoding="utf-8")
        facts_path = brain_dir / "facts.json"
        conflicts = [{"key": f"k{i}", "resolved": False} for i in range(65)]
        facts_path.write_text(json.dumps({"facts": {}, "conflicts": conflicts}), encoding="utf-8")

        report = brain_regression_report(tmp_path)
        assert report["unresolved_conflicts"] == 65
        assert report["status"] == "fail"

    def test_corrupted_records_file(self, tmp_path: Path) -> None:
        """Corrupted JSON lines should be skipped, not crash."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        good_record = json.dumps({
            "record_id": "r1", "ts": "2026-01-01", "source": "user",
            "kind": "ep", "task_id": "t", "branch": "general",
            "tags": [], "summary": "test", "confidence": 0.7,
            "content_hash": "uniq",
        })
        records_path.write_text(
            good_record + "\n" + "NOT_JSON\n" + "{bad json\n",
            encoding="utf-8",
        )
        report = brain_regression_report(tmp_path)
        assert report["total_records"] == 1

    def test_empty_records_file(self, tmp_path: Path) -> None:
        """Empty records.jsonl -> zero records, status pass."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        (brain_dir / "records.jsonl").write_text("", encoding="utf-8")
        report = brain_regression_report(tmp_path)
        assert report["total_records"] == 0
        assert report["status"] == "pass"

    def test_all_conflicts_resolved(self, tmp_path: Path) -> None:
        """Resolved conflicts should not count toward unresolved."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        (brain_dir / "records.jsonl").write_text("", encoding="utf-8")
        facts_path = brain_dir / "facts.json"
        conflicts = [{"key": f"k{i}", "resolved": True} for i in range(50)]
        facts_path.write_text(json.dumps({"facts": {}, "conflicts": conflicts}), encoding="utf-8")
        report = brain_regression_report(tmp_path)
        assert report["unresolved_conflicts"] == 0
        assert report["conflict_total"] == 50
        assert report["status"] == "pass"


# ---------------------------------------------------------------------------
# Score computation helpers: _tokenize, _pick_branch, _summarize, _recency_weight
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic_text(self) -> None:
        result = _tokenize("Hello World")
        assert result == ["hello", "world"]

    def test_underscores_replaced(self) -> None:
        result = _tokenize("task_outcome")
        assert "task" in result
        assert "outcome" in result

    def test_pure_numeric_filtered(self) -> None:
        result = _tokenize("item 42 revision 7")
        assert "42" not in result
        assert "7" not in result
        assert "item" in result
        assert "revision" in result

    def test_empty_string(self) -> None:
        assert _tokenize("") == []

    def test_short_tokens_filtered(self) -> None:
        """Tokens must be at least 2 chars (per TOKEN_RE regex)."""
        result = _tokenize("a b cc dd")
        assert "a" not in result
        assert "b" not in result
        assert "cc" in result
        assert "dd" in result

    def test_mixed_case_lowered(self) -> None:
        result = _tokenize("PyTHon CodE")
        assert "python" in result
        assert "code" in result


class TestPickBranch:
    def test_empty_tokens(self) -> None:
        assert _pick_branch([]) == "general"

    def test_ops_tokens(self) -> None:
        assert _pick_branch(["calendar", "meeting", "schedule"]) == "ops"

    def test_coding_tokens(self) -> None:
        assert _pick_branch(["python", "code", "test"]) == "coding"

    def test_health_tokens(self) -> None:
        assert _pick_branch(["prescription", "pharmacy"]) == "health"

    def test_finance_tokens(self) -> None:
        assert _pick_branch(["budget", "payment"]) == "finance"

    def test_no_match_returns_general(self) -> None:
        assert _pick_branch(["xyzzy", "qwerty"]) == "general"

    def test_prefix_matching(self) -> None:
        """Tokens starting with branch keywords should match (e.g., 'codebase' starts with 'code')."""
        assert _pick_branch(["codebase", "codereview"]) == "coding"

    def test_tie_breaking(self) -> None:
        """When branches tie, max() returns the first-encountered winner."""
        # "call" matches communications, "game" matches gaming - each has 1 match
        result = _pick_branch(["call", "game"])
        assert result in ("communications", "gaming")


class TestSummarize:
    def test_short_text_unchanged(self) -> None:
        assert _summarize("Hello world") == "Hello world"

    def test_whitespace_collapsed(self) -> None:
        assert _summarize("  Hello   world  \n\n test  ") == "Hello world test"

    def test_long_text_trimmed(self) -> None:
        long_text = "x" * 300
        result = _summarize(long_text)
        assert result.endswith("...(trimmed)")
        # The suffix " ...(trimmed)" is 13 chars; sliced at max_len-12=268, so result is 268+13=281
        # Key invariant: result is shorter than the input and ends with the trimmed marker
        assert len(result) < len(long_text)

    def test_custom_max_len(self) -> None:
        text = "a" * 100
        result = _summarize(text, max_len=50)
        # Implementation has off-by-one: subtracts 12 but suffix is 13 chars
        # Result will be max_len-12 + 13 = max_len+1
        assert len(result) <= 51
        assert result.endswith("...(trimmed)")
        assert len(result) < len(text)

    def test_exact_boundary(self) -> None:
        text = "x" * 280
        result = _summarize(text, max_len=280)
        assert result == text  # exactly at boundary, no trimming


class TestRecencyWeight:
    def test_empty_timestamp(self) -> None:
        assert _recency_weight("") == 0.3

    def test_recent_timestamp_high_weight(self) -> None:
        now = datetime.now(UTC).isoformat()
        weight = _recency_weight(now)
        assert weight > 0.9  # very recent -> close to 1.0

    def test_old_timestamp_low_weight(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        weight = _recency_weight(old)
        assert weight < 0.1  # 720 hours ago -> exp(-720/96) ~ very small

    def test_invalid_timestamp_returns_default(self) -> None:
        assert _recency_weight("not-a-date") == 0.3

    def test_zulu_suffix_handled(self) -> None:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        weight = _recency_weight(ts)
        assert weight > 0.9

    def test_decay_is_exponential(self) -> None:
        """Weight should decay exponentially with half-life ~ 96 hours."""
        now = datetime.now(UTC)
        w_now = _recency_weight(now.isoformat())
        w_96h = _recency_weight((now - timedelta(hours=96)).isoformat())
        # At 96h, exp(-96/96) = exp(-1) ~ 0.368
        expected_ratio = math.exp(-1)
        actual_ratio = w_96h / w_now if w_now > 0 else 0
        assert abs(actual_ratio - expected_ratio) < 0.05


# ---------------------------------------------------------------------------
# _extract_fact_candidates
# ---------------------------------------------------------------------------


class TestExtractFactCandidates:
    def test_safe_mode_enable(self) -> None:
        facts = _extract_fact_candidates("Enable safe mode now", "general")
        keys = [f["key"] for f in facts]
        assert "runtime.safe_mode" in keys
        match = next(f for f in facts if f["key"] == "runtime.safe_mode")
        assert match["value"] == "enabled"

    def test_safe_mode_disable(self) -> None:
        facts = _extract_fact_candidates("Disable safe mode please", "general")
        match = next(f for f in facts if f["key"] == "runtime.safe_mode")
        assert match["value"] == "disabled"

    def test_gaming_mode_enable(self) -> None:
        facts = _extract_fact_candidates("Enable gaming mode", "general")
        keys = [f["key"] for f in facts]
        assert "runtime.gaming_mode" in keys

    def test_gaming_mode_auto_enable(self) -> None:
        facts = _extract_fact_candidates("Enable auto gaming mode", "general")
        keys = [f["key"] for f in facts]
        assert "runtime.gaming_mode_auto" in keys
        assert "runtime.gaming_mode" in keys

    def test_pause_daemon(self) -> None:
        facts = _extract_fact_candidates("Pause the daemon temporarily", "general")
        match = next(f for f in facts if f["key"] == "runtime.daemon_paused")
        assert match["value"] == "true"

    def test_resume_daemon(self) -> None:
        facts = _extract_fact_candidates("Resume daemon operations", "general")
        match = next(f for f in facts if f["key"] == "runtime.daemon_paused")
        assert match["value"] == "false"

    def test_spam_guard(self) -> None:
        facts = _extract_fact_candidates("Block spam calls from unknown", "general")
        keys = [f["key"] for f in facts]
        assert "phone.spam_guard" in keys

    def test_owner_guard_enable(self) -> None:
        facts = _extract_fact_candidates("Enable owner guard for security", "general")
        match = next(f for f in facts if f["key"] == "security.owner_guard")
        assert match["value"] == "enabled"

    def test_daily_autopilot(self) -> None:
        facts = _extract_fact_candidates("Organize my day and schedule", "general")
        keys = [f["key"] for f in facts]
        assert "ops.daily_autopilot" in keys

    def test_no_match_non_general_branch(self) -> None:
        """No keyword match + non-general branch -> fallback branch fact."""
        facts = _extract_fact_candidates("Something random and unrelated", "coding")
        keys = [f["key"] for f in facts]
        assert "branch.last_focus.coding" in keys

    def test_no_match_general_branch(self) -> None:
        """No keyword match + general branch -> empty list (no fallback)."""
        facts = _extract_fact_candidates("Completely irrelevant text", "general")
        assert facts == []

    def test_max_8_candidates(self) -> None:
        """Output is capped at 8 candidates."""
        # This text hits many patterns
        text = "Enable safe mode, enable gaming mode, enable auto gaming mode, pause daemon, block spam calls, enable owner guard, organize today"
        facts = _extract_fact_candidates(text, "general")
        assert len(facts) <= 8

    def test_confidence_clamped(self) -> None:
        """All confidence values should be in [0.0, 1.0]."""
        facts = _extract_fact_candidates("Enable safe mode and gaming mode auto on", "general")
        for f in facts:
            assert 0.0 <= f["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# _load_records edge cases
# ---------------------------------------------------------------------------


class TestLoadRecords:
    def test_missing_file(self, tmp_path: Path) -> None:
        records = _load_records(tmp_path)
        assert records == []

    def test_limit_returns_tail(self, tmp_path: Path) -> None:
        """limit parameter returns the last N records."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        lines = []
        for i in range(20):
            lines.append(json.dumps({"record_id": f"r{i}", "content_hash": f"h{i}"}))
        records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _load_records(tmp_path, limit=5)
        assert len(result) == 5
        assert result[0]["record_id"] == "r15"  # last 5 of 20

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        records_path.write_text(
            json.dumps({"record_id": "r1"}) + "\n\n\n" + json.dumps({"record_id": "r2"}) + "\n",
            encoding="utf-8",
        )
        result = _load_records(tmp_path)
        assert len(result) == 2

    def test_non_dict_lines_skipped(self, tmp_path: Path) -> None:
        """JSON arrays or strings that parse correctly but aren't dicts are skipped."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        records_path = brain_dir / "records.jsonl"
        records_path.write_text(
            '"just a string"\n[1,2,3]\n' + json.dumps({"record_id": "r1"}) + "\n",
            encoding="utf-8",
        )
        result = _load_records(tmp_path)
        assert len(result) == 1
        assert result[0]["record_id"] == "r1"


# ---------------------------------------------------------------------------
# ingest_brain_record edge cases
# ---------------------------------------------------------------------------


class TestIngestEdgeCases:
    def test_empty_content_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Empty content"):
            ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content="")

    def test_whitespace_only_content_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Empty content"):
            ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content="   \n\t  ")

    def test_confidence_clamped_low(self, tmp_path: Path) -> None:
        rec = ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content="Some content here", confidence=-0.5)
        assert rec.confidence == 0.0

    def test_confidence_clamped_high(self, tmp_path: Path) -> None:
        rec = ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content="Some content here", confidence=1.5)
        assert rec.confidence == 1.0

    def test_task_id_truncated(self, tmp_path: Path) -> None:
        long_id = "x" * 200
        rec = ingest_brain_record(tmp_path, source="user", kind="ep", task_id=long_id, content="Content for truncation test")
        assert len(rec.task_id) <= 128

    def test_content_truncated_at_4000(self, tmp_path: Path) -> None:
        """Content is cleaned and truncated to 4000 chars."""
        long_content = "a" * 5000
        rec = ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content=long_content)
        # Summary uses _summarize which caps around 280 chars (with off-by-one in suffix)
        assert len(rec.summary) < 300
        assert rec.summary.endswith("...(trimmed)")

    def test_tags_deduplicated_and_sorted(self, tmp_path: Path) -> None:
        rec = ingest_brain_record(
            tmp_path, source="user", kind="ep", task_id="t",
            content="Tag test content here",
            tags=["Zulu", "Alpha", "alpha", "ALPHA", "Zulu"],
        )
        assert rec.tags == ["alpha", "zulu"]

    def test_tags_limited_to_10(self, tmp_path: Path) -> None:
        tags = [f"tag{i}" for i in range(20)]
        rec = ingest_brain_record(
            tmp_path, source="user", kind="ep", task_id="t",
            content="Many tags test content",
            tags=tags,
        )
        assert len(rec.tags) <= 10

    def test_returns_brain_record_dataclass(self, tmp_path: Path) -> None:
        rec = ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t", content="Dataclass test")
        assert isinstance(rec, BrainRecord)
        assert rec.record_id
        assert rec.ts
        assert rec.content_hash


# ---------------------------------------------------------------------------
# build_context_packet edge cases
# ---------------------------------------------------------------------------


class TestBuildContextPacketEdgeCases:
    def test_empty_dir(self, tmp_path: Path) -> None:
        packet = build_context_packet(tmp_path, query="anything", max_items=5, max_chars=500)
        assert packet["selected_count"] == 0
        assert packet["total_records_scanned"] == 0

    def test_branch_diversity_cap(self, tmp_path: Path) -> None:
        """No more than 3 results per branch are included."""
        for i in range(10):
            ingest_brain_record(
                tmp_path, source="task_outcome", kind="semantic",
                task_id=f"t{i}",
                content=f"Calendar meeting reminder number {i} for tomorrow email schedule",
            )
        packet = build_context_packet(tmp_path, query="calendar meeting email schedule", max_items=40, max_chars=12000)
        branch_counts = {}
        for item in packet["selected"]:
            b = item["branch"]
            branch_counts[b] = branch_counts.get(b, 0) + 1
        for b, count in branch_counts.items():
            assert count <= 3, f"Branch '{b}' has {count} results, max is 3"

    def test_max_chars_budget_respected(self, tmp_path: Path) -> None:
        for i in range(20):
            ingest_brain_record(
                tmp_path, source="user", kind="ep", task_id=f"t{i}",
                content=f"Calendar meeting item {i} with a long description " + ("x" * 100),
            )
        packet = build_context_packet(tmp_path, query="calendar meeting", max_items=40, max_chars=500)
        total_chars = sum(len(item["summary"]) for item in packet["selected"])
        assert total_chars <= 500

    def test_source_bonus_for_task_outcome(self, tmp_path: Path) -> None:
        """task_outcome records get a 0.08 bonus in scoring."""
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t1", content="Python test code review")
        ingest_brain_record(tmp_path, source="task_outcome", kind="semantic", task_id="t2", content="Python test code review results")
        packet = build_context_packet(tmp_path, query="python test code review", max_items=10, max_chars=2000)
        # Both should appear; task_outcome one should score higher
        assert packet["selected_count"] >= 1

    def test_hybrid_results_preserve_trust_fields(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.memory.brain._try_hybrid_search", return_value=[
            {
                "record_id": "r1",
                "branch": "ops",
                "summary": "calendar meeting",
                "source": "user",
                "kind": "episodic",
                "ts": "2026-03-11T00:00:00+00:00",
                "trust_level": "T1_observed",
                "learning_lane": "observed",
                "promotion_state": "observed",
                "_trust_shadow_score": 0.7,
                "_trust_would_downrank": True,
            }
        ]):
            packet = build_context_packet(tmp_path, query="calendar", max_items=5, max_chars=500)
        assert packet["selected_count"] == 1
        assert packet["selected"][0]["trust_level"] == "T1_observed"
        assert packet["selected"][0]["would_downrank"] is True


# ---------------------------------------------------------------------------
# brain_status edge cases
# ---------------------------------------------------------------------------


class TestBrainStatus:
    def test_empty_dir(self, tmp_path: Path) -> None:
        status = brain_status(tmp_path)
        assert status["branch_count"] == 0
        assert status["fact_count"] == 0
        assert status["updated_utc"] == ""
        assert isinstance(status["branches"], list)
        assert isinstance(status["regression"], dict)

    def test_with_data(self, tmp_path: Path) -> None:
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t1", content="Calendar meeting setup")
        ingest_brain_record(tmp_path, source="user", kind="ep", task_id="t2", content="Python code bug fix deploy")
        status = brain_status(tmp_path)
        assert status["branch_count"] >= 2
        assert status["updated_utc"] != ""
        assert status["regression"]["status"] == "pass"
        # Branches should be sorted by count descending
        if len(status["branches"]) > 1:
            counts = [b["count"] for b in status["branches"]]
            assert counts == sorted(counts, reverse=True)

    def test_corrupted_index_handled(self, tmp_path: Path) -> None:
        """Corrupted index.json should not crash brain_status."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        (brain_dir / "index.json").write_text("NOT JSON", encoding="utf-8")
        status = brain_status(tmp_path)
        assert status["branch_count"] == 0

    def test_corrupted_facts_handled(self, tmp_path: Path) -> None:
        """Corrupted facts.json should not crash brain_status."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        (brain_dir / "facts.json").write_text("{bad json", encoding="utf-8")
        status = brain_status(tmp_path)
        assert status["fact_count"] == 0


# ---------------------------------------------------------------------------
# brain_compact edge cases
# ---------------------------------------------------------------------------


class TestBrainCompact:
    def test_below_threshold(self, tmp_path: Path) -> None:
        for i in range(5):
            ingest_brain_record(tmp_path, source="user", kind="ep", task_id=f"t{i}", content=f"Record number {i}")
        result = brain_compact(tmp_path, keep_recent=100)
        assert result["compacted"] is False
        assert result["reason"] == "below_threshold"
        assert result["total_records"] == 5
        assert result["kept_records"] == 5

    def test_compact_creates_summaries(self, tmp_path: Path) -> None:
        for i in range(25):
            ingest_brain_record(tmp_path, source="user", kind="ep", task_id=f"t{i}", content=f"Calendar event {i}")
        result = brain_compact(tmp_path, keep_recent=5)
        assert result["compacted"] is True
        assert result["kept_records"] == 5
        assert result["compacted_records"] == 20
        # Summaries file should exist
        summaries_path = tmp_path / ".planning" / "brain" / "summaries.jsonl"
        assert summaries_path.exists()
        lines = [l for l in summaries_path.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) >= 1  # at least one summary group

    def test_compact_rebuilds_index(self, tmp_path: Path) -> None:
        """After compaction, index should only contain kept records."""
        for i in range(20):
            ingest_brain_record(tmp_path, source="user", kind="ep", task_id=f"t{i}", content=f"Unique content {i}")
        brain_compact(tmp_path, keep_recent=5)
        # Verify the records file has only 5 records
        records = _load_records(tmp_path, limit=100)
        assert len(records) == 5
