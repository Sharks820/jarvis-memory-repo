from __future__ import annotations

from pathlib import Path

from jarvis_engine import learning_missions


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
