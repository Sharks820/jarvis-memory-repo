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
    auto_generate_missions,
    create_learning_mission,
    load_missions,
    retry_failed_missions,
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

    monkeypatch.setattr(learning_missions, "_search_web", fake_search)
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

def test_verify_candidates_single_domain_low_confidence() -> None:
    candidates = [
        {"statement": "Python async programming uses coroutines efficiently and effectively", "url": "https://a.com/1", "domain": "a.com"},
    ]
    # Single domain with 4+ keywords → accepted at low confidence (0.30)
    verified = _verify_candidates(candidates)
    assert len(verified) == 1
    assert verified[0]["confidence"] == 0.30


def test_verify_candidates_single_domain_too_few_keywords() -> None:
    candidates = [
        {"statement": "A new app is ok to use now", "url": "https://a.com/1", "domain": "a.com"},
    ]
    # Single domain with < 4 keywords (most words are < 4 chars) → rejected
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
    monkeypatch.setattr(learning_missions, "_search_web", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    assert report["verified_count"] == 0
    assert report["candidate_count"] == 0


def test_run_mission_fetch_returns_empty(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Some topic", objective="learn")
    monkeypatch.setattr(
        learning_missions, "_search_web",
        lambda query, limit: ["https://example.com/page"],
    )
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    assert report["candidate_count"] == 0


def test_run_mission_zero_results_marks_failed(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Quick topic", objective="learn fast")
    monkeypatch.setattr(learning_missions, "_search_web", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    missions = load_missions(tmp_path)
    target = [m for m in missions if m["mission_id"] == mission["mission_id"]][0]
    assert target["status"] == "failed"
    assert target["last_report_path"] != ""


def test_run_mission_writes_report_file(tmp_path: Path, monkeypatch) -> None:
    mission = create_learning_mission(tmp_path, topic="Report test", objective="verify file")
    monkeypatch.setattr(learning_missions, "_search_web", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")
    report = run_learning_mission(tmp_path, mission_id=mission["mission_id"])
    mid = mission["mission_id"]
    report_path = tmp_path / ".planning" / "missions" / f"{mid}.report.json"
    assert report_path.exists()
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    assert raw["mission_id"] == mid


# ── retry_failed_missions tests ──────────────────────────────────────────

def test_retry_failed_missions_requeues(tmp_path: Path, monkeypatch) -> None:
    """Failed missions get re-queued as pending with incremented retry count."""
    mission = create_learning_mission(tmp_path, topic="Retry topic", objective="test retry")
    # Manually mark as failed
    missions = load_missions(tmp_path)
    missions[0]["status"] = "failed"
    missions[0]["retries"] = 0
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.write_text(json.dumps(missions), encoding="utf-8")

    retried = retry_failed_missions(tmp_path)
    assert retried == 1

    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "pending"
    assert missions[0]["retries"] == 1
    assert "wikipedia" in [s.lower() for s in missions[0]["sources"]]


def test_retry_failed_missions_exhausts_after_two(tmp_path: Path) -> None:
    """Missions with 2+ retries get marked exhausted, not re-queued."""
    mission = create_learning_mission(tmp_path, topic="Exhausted topic", objective="test")
    missions = load_missions(tmp_path)
    missions[0]["status"] = "failed"
    missions[0]["retries"] = 2
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.write_text(json.dumps(missions), encoding="utf-8")

    retried = retry_failed_missions(tmp_path)
    assert retried == 0

    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "exhausted"


def test_retry_skips_non_failed(tmp_path: Path) -> None:
    """Only failed missions get retried."""
    create_learning_mission(tmp_path, topic="Pending topic", objective="test")
    retried = retry_failed_missions(tmp_path)
    assert retried == 0


# ── auto_generate_missions tests ──────────────────────────────────────────

def test_auto_generate_skips_when_pending_exist(tmp_path: Path) -> None:
    """Auto-generate does nothing if pending missions already exist."""
    create_learning_mission(tmp_path, topic="Existing pending", objective="test")
    created = auto_generate_missions(tmp_path)
    assert created == []


def test_auto_generate_creates_from_db(tmp_path: Path) -> None:
    """Auto-generate creates missions from conversation records in DB."""
    import sqlite3

    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE records (
        id INTEGER PRIMARY KEY, summary TEXT, ts TEXT, source TEXT
    )""")
    from datetime import datetime, timedelta
    from jarvis_engine._compat import UTC
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO records (summary, ts, source) VALUES (?, ?, ?)",
        ("Python asyncio event loops and coroutines", recent, "user"),
    )
    conn.execute(
        "INSERT INTO records (summary, ts, source) VALUES (?, ?, ?)",
        ("Kubernetes cluster management techniques", recent, "user"),
    )
    conn.execute(
        "INSERT INTO records (summary, ts, source) VALUES (?, ?, ?)",
        ("Machine learning model training optimization", recent, "user"),
    )
    conn.commit()
    conn.close()

    created = auto_generate_missions(tmp_path, max_new=3, db_path=db_path)
    assert len(created) >= 1
    assert all(m["status"] == "pending" for m in created)
    # Topics should be multi-word phrases from conversation records
    for m in created:
        assert len(m["topic"].split()) >= 2


def test_auto_generate_no_db(tmp_path: Path) -> None:
    """Auto-generate returns empty list when no DB exists."""
    created = auto_generate_missions(tmp_path, db_path=tmp_path / "nonexistent.db")
    assert created == []


def test_auto_generate_deduplicates_existing(tmp_path: Path) -> None:
    """Auto-generate skips topics that exactly match existing missions."""
    import sqlite3
    from datetime import datetime, timedelta
    from jarvis_engine._compat import UTC

    # Create DB with a single topic phrase
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, summary TEXT, ts TEXT, source TEXT)")
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO records (summary, ts, source) VALUES (?, ?, ?)",
        ("Kubernetes cluster management", recent, "user"),
    )
    conn.commit()
    conn.close()

    # First generation should create a mission
    created1 = auto_generate_missions(tmp_path, max_new=1, db_path=db_path)
    assert len(created1) >= 1
    topic1 = created1[0]["topic"].lower()

    # Mark it completed so it's not "pending" (which would block generation)
    missions = load_missions(tmp_path)
    missions[0]["status"] = "completed"
    missions_path = tmp_path / ".planning" / "missions.json"
    missions_path.write_text(json.dumps(missions), encoding="utf-8")

    # Second generation should not create the same exact topic
    created2 = auto_generate_missions(tmp_path, max_new=1, db_path=db_path)
    for m in created2:
        assert m["topic"].lower() != topic1


# ── _mission_queries with wikipedia ──────────────────────────────────────

def test_mission_queries_wikipedia_source() -> None:
    queries = _mission_queries("Neural networks", ["wikipedia"])
    assert any("site:en.wikipedia.org" in q for q in queries)
    assert any("explained" in q for q in queries)


# ── Verification tier confidence levels ──────────────────────────────────

def test_verify_cross_domain_higher_confidence() -> None:
    """Cross-domain verified candidates get higher confidence than single-source."""
    candidates = [
        {"statement": "Python async programming uses coroutines efficiently and effectively", "url": "https://a.com/1", "domain": "a.com"},
        {"statement": "Python async programming with coroutines efficiently simplifies code paths", "url": "https://b.com/2", "domain": "b.com"},
    ]
    verified = _verify_candidates(candidates)
    assert len(verified) >= 1
    # Cross-domain should have higher confidence than the 0.30 single-source tier
    assert verified[0]["confidence"] > 0.30


# ── Full retry cycle integration ──────────────────────────────────────────

def test_full_retry_cycle(tmp_path: Path, monkeypatch) -> None:
    """Mission fails → retry → fails again → retry → fails → exhausted."""
    monkeypatch.setattr(learning_missions, "_search_web", lambda query, limit: [])
    monkeypatch.setattr(learning_missions, "_fetch_page_text", lambda url, max_bytes: "")

    mission = create_learning_mission(tmp_path, topic="Hard topic", objective="learn")
    mid = mission["mission_id"]

    # Run 1: should fail
    run_learning_mission(tmp_path, mission_id=mid)
    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "failed"

    # Retry 1: re-queue
    retried = retry_failed_missions(tmp_path)
    assert retried == 1
    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "pending"
    assert missions[0]["retries"] == 1

    # Run 2: should fail again
    run_learning_mission(tmp_path, mission_id=mid)
    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "failed"

    # Retry 2: re-queue
    retried = retry_failed_missions(tmp_path)
    assert retried == 1
    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "pending"
    assert missions[0]["retries"] == 2

    # Run 3: should be exhausted now (retries=2, so status="exhausted")
    run_learning_mission(tmp_path, mission_id=mid)
    missions = load_missions(tmp_path)
    assert missions[0]["status"] == "exhausted"

    # No more retries
    retried = retry_failed_missions(tmp_path)
    assert retried == 0
