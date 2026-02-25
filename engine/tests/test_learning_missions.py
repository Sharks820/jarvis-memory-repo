from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine import learning_missions
from jarvis_engine.learning_missions import (
    _extract_candidates,
    _keywords,
    _mission_queries,
    _topic_keywords,
    _verify_candidates,
    create_learning_mission,
    load_missions,
    run_learning_mission,
)


# ── existing test ─────────────────────────────────────────────────────────

def test_learning_mission_create_and_run_with_verification(tmp_path: Path, monkeypatch) -> None:
    mission = learning_missions.create_learning_mission(
        tmp_path,
        topic="Unity 6.3 game architecture",
        objective="Learn reliable patterns",
        sources=["google", "reddit", "official_docs"],
    )
    mission_id = str(mission["mission_id"])

    urls = [
        "https://docs.unity.com/tutorial-a",
        "https://www.reddit.com/r/Unity3D/post123",
    ]

    def fake_search(query: str, *, limit: int) -> list[str]:
        return urls[:limit]

    def fake_fetch(url: str, *, max_bytes: int) -> str:
        if "unity.com" in url:
            return (
                "Unity 6.3 projects should use version control with small commits. "
                "Use separate scenes for test harnesses and gameplay."
            )
        return (
            "For Unity 6.3, use version control with small commits to avoid regressions. "
            "Separate scenes can help testing."
        )

    monkeypatch.setattr(learning_missions, "_search_duckduckgo", fake_search)
    monkeypatch.setattr(learning_missions, "_fetch_page_text", fake_fetch)

    report = learning_missions.run_learning_mission(tmp_path, mission_id=mission_id, max_search_results=4, max_pages=4)
    assert report["mission_id"] == mission_id
    assert report["verified_count"] >= 1
    assert (tmp_path / ".planning" / "missions" / f"{mission_id}.report.json").exists()

    missions = learning_missions.load_missions(tmp_path)
    current = [m for m in missions if m.get("mission_id") == mission_id][0]
    assert current["status"] == "completed"


# ── create_learning_mission tests ─────────────────────────────────────────

def test_create_mission_empty_topic_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic is required"):
        create_learning_mission(tmp_path, topic="", objective="learn stuff")


def test_create_mission_whitespace_topic_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic is required"):
        create_learning_mission(tmp_path, topic="   ", objective="learn stuff")


def test_create_mission_defaults_sources(tmp_path: Path) -> None:
    mission = create_learning_mission(tmp_path, topic="Rust async", objective="understand async")
    assert mission["sources"] == ["google", "reddit", "official_docs"]


def test_create_mission_custom_sources(tmp_path: Path) -> None:
    mission = create_learning_mission(
        tmp_path, topic="Rust async", objective="understand async", sources=["arxiv"]
    )
    assert mission["sources"] == ["arxiv"]


def test_create_mission_truncates_long_topic(tmp_path: Path) -> None:
    long_topic = "x" * 500
    mission = create_learning_mission(tmp_path, topic=long_topic, objective="test")
    assert len(mission["topic"]) <= 200


def test_create_mission_truncates_long_objective(tmp_path: Path) -> None:
    long_obj = "y" * 800
    mission = create_learning_mission(tmp_path, topic="test", objective=long_obj)
    assert len(mission["objective"]) <= 400


def test_create_mission_persists_to_disk(tmp_path: Path) -> None:
    create_learning_mission(tmp_path, topic="Go concurrency", objective="learn goroutines")
    missions = load_missions(tmp_path)
    assert len(missions) == 1
    assert missions[0]["topic"] == "Go concurrency"
    assert missions[0]["status"] == "pending"


def test_create_multiple_missions(tmp_path: Path) -> None:
    create_learning_mission(tmp_path, topic="Topic A", objective="Obj A")
    create_learning_mission(tmp_path, topic="Topic B", objective="Obj B")
    missions = load_missions(tmp_path)
    assert len(missions) == 2
    topics = {m["topic"] for m in missions}
    assert topics == {"Topic A", "Topic B"}


# ── load_missions tests ──────────────────────────────────────────────────

def test_load_missions_no_file(tmp_path: Path) -> None:
    result = load_missions(tmp_path)
    assert result == []


def test_load_missions_corrupt_json(tmp_path: Path) -> None:
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.parent.mkdir(parents=True, exist_ok=True)
    missions_path.write_text("{not valid json!!!", encoding="utf-8")
    result = load_missions(tmp_path)
    assert result == []


def test_load_missions_non_list_json(tmp_path: Path) -> None:
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.parent.mkdir(parents=True, exist_ok=True)
    missions_path.write_text('{"not": "a list"}', encoding="utf-8")
    result = load_missions(tmp_path)
    assert result == []


def test_load_missions_filters_non_dict_items(tmp_path: Path) -> None:
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.parent.mkdir(parents=True, exist_ok=True)
    missions_path.write_text(
        json.dumps([{"mission_id": "m-1", "topic": "ok"}, "bad item", 42]),
        encoding="utf-8",
    )
    result = load_missions(tmp_path)
    assert len(result) == 1
    assert result[0]["mission_id"] == "m-1"


# ── _mission_queries tests ───────────────────────────────────────────────

def test_mission_queries_basic() -> None:
    queries = _mission_queries("Python async", ["google", "reddit", "official_docs"])
    assert "Python async" in queries
    assert "Python async tutorial" in queries
    assert "Python async best practices" in queries
    assert any("site:reddit.com" in q for q in queries)
    assert any("official documentation" in q for q in queries)


def test_mission_queries_no_sources() -> None:
    queries = _mission_queries("ML basics", [])
    assert "ML basics" in queries
    assert "ML basics tutorial" in queries


def test_mission_queries_deduplicates() -> None:
    queries = _mission_queries("test", ["google", "google"])
    # No duplicates
    assert len(queries) == len(set(queries))


# ── _topic_keywords / _keywords tests ────────────────────────────────────

def test_topic_keywords_filters_stopwords() -> None:
    kw = _topic_keywords("what does python have")
    assert "python" in kw
    assert "what" not in kw
    assert "have" not in kw


def test_keywords_basic() -> None:
    kw = _keywords("Python async programming")
    assert "python" in kw
    assert "async" in kw
    assert "programming" in kw


# ── _extract_candidates tests ─────────────────────────────────────────────

def test_extract_candidates_filters_short() -> None:
    text = "No. Yes. Python coroutines enable efficient asynchronous code execution patterns."
    candidates = _extract_candidates(text, topic="python coroutines", max_candidates=5)
    assert all(len(c) >= 30 for c in candidates)


def test_extract_candidates_filters_long() -> None:
    long_sentence = "Python " + "word " * 200 + "end."
    text = long_sentence + " Python patterns simplify development across many domains."
    candidates = _extract_candidates(text, topic="python patterns", max_candidates=5)
    assert all(len(c) <= 320 for c in candidates)


def test_extract_candidates_empty_text() -> None:
    candidates = _extract_candidates("", topic="anything", max_candidates=5)
    assert candidates == []


def test_extract_candidates_max_limit() -> None:
    sentences = " ".join(
        f"Python framework number {i} provides useful tools for building applications."
        for i in range(20)
    )
    candidates = _extract_candidates(sentences, topic="python framework", max_candidates=3)
    assert len(candidates) <= 3


# ── _verify_candidates tests ─────────────────────────────────────────────

def test_verify_candidates_needs_multiple_domains() -> None:
    candidates = [
        {"statement": "Python async programming uses coroutines efficiently and effectively", "url": "https://a.com/1", "domain": "a.com"},
    ]
    # Single domain, no cross-domain verification possible
    verified = _verify_candidates(candidates)
    assert len(verified) == 0


def test_verify_candidates_cross_domain_verification() -> None:
    candidates = [
        {"statement": "Python async programming uses coroutines efficiently and effectively", "url": "https://a.com/1", "domain": "a.com"},
        {"statement": "Python async programming with coroutines efficiently simplifies code paths", "url": "https://b.com/2", "domain": "b.com"},
    ]
    verified = _verify_candidates(candidates)
    assert len(verified) >= 1
    assert verified[0]["confidence"] > 0


def test_verify_candidates_empty_input() -> None:
    verified = _verify_candidates([])
    assert verified == []


def test_verify_candidates_skips_empty_statements() -> None:
    candidates = [
        {"statement": "", "url": "https://a.com/1", "domain": "a.com"},
        {"statement": "   ", "url": "https://b.com/2", "domain": "b.com"},
    ]
    verified = _verify_candidates(candidates)
    assert verified == []


# ── run_learning_mission edge cases ──────────────────────────────────────

def test_run_mission_not_found(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mission not found"):
        run_learning_mission(tmp_path, mission_id="m-nonexistent")


def test_run_mission_empty_search_results(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Obscure topic", objective="learn")
    monkeypatch.setattr(learning_missions, "_search_duckduckgo", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    assert report["verified_count"] == 0
    assert report["candidate_count"] == 0


def test_run_mission_fetch_returns_empty(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Some topic", objective="learn")
    monkeypatch.setattr(
        learning_missions, "_search_duckduckgo",
        lambda query, limit: ["https://example.com/page"],
    )
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    assert report["candidate_count"] == 0


def test_run_mission_updates_status_to_completed(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Quick topic", objective="learn fast")
    monkeypatch.setattr(learning_missions, "_search_duckduckgo", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    missions = load_missions(tmp_path)
    target = [m for m in missions if m["mission_id"] == mission["mission_id"]][0]
    assert target["status"] == "completed"
    assert target["last_report_path"] != ""


def test_run_mission_writes_report_file(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Report test", objective="verify file")
    monkeypatch.setattr(learning_missions, "_search_duckduckgo", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    mid = mission["mission_id"]
    report_path = tmp_path / ".planning" / "missions" / f"{mid}.report.json"
    assert report_path.exists()
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    assert raw["mission_id"] == mid
