from __future__ import annotations

from pathlib import Path

from jarvis_engine.brain_memory import (
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
