from __future__ import annotations

from jarvis_engine import web_research


def test_run_web_research_collects_findings(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research,
        "_search_duckduckgo",
        lambda query, limit: [
            "https://example.com/a",
            "https://docs.example.org/b",
        ],
    )
    monkeypatch.setattr(
        web_research,
        "_fetch_page_text",
        lambda url, max_bytes=250_000: (
            "Samsung Galaxy S25 supports call filtering and spam controls. "
            "You can enable smart spam blocking in settings."
            if "example.com" in url
            else "Carrier spam detection can reduce robocalls and unknown callers."
        ),
    )

    report = web_research.run_web_research("samsung galaxy s25 spam call filtering", max_results=6, max_pages=4)
    assert report["query"] == "samsung galaxy s25 spam call filtering"
    assert report["finding_count"] >= 1
    assert report["summary_lines"]


def test_run_web_research_requires_query() -> None:
    try:
        web_research.run_web_research("   ")
    except ValueError as exc:
        assert "query is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
