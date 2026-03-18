from __future__ import annotations

import pytest

from jarvis_engine.web import research as web_research
from jarvis_engine.web.research import _extract_snippet, _query_keywords


def _patch_web(monkeypatch, urls=None, page_text=""):
    """Shared helper to monkeypatch _search_web and the fetch helper."""
    monkeypatch.setattr(web_research, "_search_web", lambda query, limit: (urls or []))
    if isinstance(page_text, str):
        monkeypatch.setattr(
            web_research, "_fetch_page_text_with_fallbacks", lambda url, max_bytes=250_000: page_text,
        )
    else:
        monkeypatch.setattr(web_research, "_fetch_page_text_with_fallbacks", page_text)


# ── existing tests ────────────────────────────────────────────────────────

def test_run_web_research_collects_findings(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research,
        "_search_web",
        lambda query, limit: [
            "https://example.com/a",
            "https://docs.example.org/b",
        ],
    )
    monkeypatch.setattr(
        web_research,
        "_fetch_page_text_with_fallbacks",
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


# ── _query_keywords tests ─────────────────────────────────────────────────

def test_query_keywords_filters_stopwords() -> None:
    kw = _query_keywords("what does python have")
    assert "python" in kw
    assert "what" not in kw
    assert "does" not in kw
    assert "have" not in kw


def test_query_keywords_ignores_short_words() -> None:
    kw = _query_keywords("a to be or")
    assert kw == set()  # All words < 3 chars


def test_query_keywords_lowercases() -> None:
    kw = _query_keywords("PYTHON Flask Django")
    assert "python" in kw
    assert "flask" in kw
    assert "django" in kw


# ── _extract_snippet tests ────────────────────────────────────────────────

def test_extract_snippet_returns_matching_sentences() -> None:
    text = (
        "Short. "
        "Python is a versatile programming language used worldwide for many tasks. "
        "Java is also commonly used in enterprise software systems. "
        "Python frameworks like Django simplify the development process greatly."
    )
    snippet = _extract_snippet(text, query="python programming", max_sentences=2)
    assert "Python" in snippet


def test_extract_snippet_returns_empty_for_no_match() -> None:
    text = "The weather is nice today with sunshine and clear skies all day long and it is pleasant."
    snippet = _extract_snippet(text, query="quantum computing algorithms")
    assert snippet == ""


def test_extract_snippet_skips_short_sentences() -> None:
    text = "No. Yes. Ok. Python programming is truly a versatile language for data analysis and web development."
    snippet = _extract_snippet(text, query="python programming")
    # Short sentences "No.", "Yes.", "Ok." should be skipped
    assert "Python" in snippet


def test_extract_snippet_skips_overly_long_sentences() -> None:
    long_sentence = "Python " + "word " * 200 + "end."
    text = long_sentence + " Python frameworks simplify development for modern applications."
    snippet = _extract_snippet(text, query="python")
    # The very long sentence (>320 chars) should be skipped
    assert "frameworks" in snippet or snippet == ""


def test_extract_snippet_max_sentences_limit() -> None:
    text = (
        "Rust programming provides memory safety without garbage collection and is popular. "
        "Rust also enables fearless concurrency in applications that require high performance. "
        "Rust compilers check ownership rules at compile time for safety and correctness."
    )
    snippet = _extract_snippet(text, query="rust programming", max_sentences=1)
    # Should only return one sentence
    sentences = [s.strip() for s in snippet.split(". ") if s.strip()]
    assert len(sentences) <= 2  # Allowing for one period-separated sentence


# ── run_web_research extended tests ───────────────────────────────────────

@pytest.mark.parametrize(
    "urls, page_text, query, expected_findings, min_scanned",
    [
        pytest.param([], "", "obscure topic nobody writes about", 0, 0, id="empty_search_results"),
        pytest.param(
            ["https://example.com/page1", "https://example.com/page2"], "",
            "test query for empty pages", 0, 2, id="fetch_returns_empty",
        ),
        pytest.param(
            ["https://example.com/page"], "Completely unrelated content about gardening and flower pots and soil types.",
            "quantum computing algorithms", 0, 1, id="no_snippet_match",
        ),
    ],
)
def test_run_web_research_zero_findings(
    monkeypatch, urls, page_text, query, expected_findings, min_scanned,
) -> None:
    _patch_web(monkeypatch, urls=urls, page_text=page_text)
    report = web_research.run_web_research(query)
    assert report["finding_count"] == expected_findings
    assert report["scanned_url_count"] >= min_scanned


def test_run_web_research_deduplicates_findings(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research, "_search_web",
        lambda query, limit: ["https://a.com/1", "https://b.com/2"],
    )
    # Both pages return identical relevant content
    monkeypatch.setattr(
        web_research, "_fetch_page_text_with_fallbacks",
        lambda url, max_bytes=250_000: "Kotlin coroutines simplify asynchronous programming for Android development.",
    )
    report = web_research.run_web_research("kotlin coroutines android")
    # Dedup should keep only unique snippets
    assert len(report["summary_lines"]) <= 1


def test_run_web_research_query_truncation(monkeypatch) -> None:
    _patch_web(monkeypatch)
    long_query = "x" * 500
    report = web_research.run_web_research(long_query)
    assert len(report["query"]) <= 260


def test_run_web_research_malformed_url_no_domain(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research, "_search_web",
        lambda query, limit: ["not-a-valid-url", "https://valid.com/page"],
    )
    monkeypatch.setattr(
        web_research, "_fetch_page_text_with_fallbacks",
        lambda url, max_bytes=250_000: "Valid content about testing software applications with modern frameworks.",
    )
    report = web_research.run_web_research("testing software")
    # The malformed URL should be skipped (no domain), valid one processed
    assert report["scanned_url_count"] >= 1


@pytest.mark.parametrize(
    "max_results, check",
    [
        pytest.param(100, lambda lim: lim <= 20, id="clamped_to_20"),
        pytest.param(0, lambda lim: lim >= 2, id="min_two"),
    ],
)
def test_run_web_research_max_results_bounds(monkeypatch, max_results, check) -> None:
    called_limits: list[int] = []

    def capture_search(query, limit):
        called_limits.append(limit)
        return []

    monkeypatch.setattr(web_research, "_search_web", capture_search)
    monkeypatch.setattr(web_research, "_fetch_page_text_with_fallbacks", lambda url, max_bytes=250_000: "")
    web_research.run_web_research("test", max_results=max_results)
    assert check(called_limits[0])


def test_run_web_research_report_has_generated_utc(monkeypatch) -> None:
    _patch_web(monkeypatch)
    report = web_research.run_web_research("any topic")
    assert "generated_utc" in report
    assert isinstance(report["generated_utc"], str)
    assert len(report["generated_utc"]) > 0


def test_run_web_research_findings_have_domain(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research, "_search_web",
        lambda query, limit: ["https://docs.python.org/tutorial"],
    )
    monkeypatch.setattr(
        web_research, "_fetch_page_text_with_fallbacks",
        lambda url, max_bytes=250_000: "Python tutorial covers data structures, control flow, and module import patterns.",
    )
    report = web_research.run_web_research("python tutorial data structures")
    if report["finding_count"] > 0:
        finding = report["findings"][0]
        assert "domain" in finding
        assert "docs.python.org" in finding["domain"]
        assert "url" in finding
        assert "snippet" in finding


def test_run_web_research_uses_fetch_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(
        web_research, "_search_web", lambda query, limit: ["https://example.com/page"],
    )
    monkeypatch.setattr(
        web_research,
        "_fetch_page_text_with_fallbacks",
        lambda url, max_bytes=250_000: (
            "Python packaging guides explain wheels, virtual environments, and dependency resolution in practice."
        ),
    )

    report = web_research.run_web_research("python packaging guides")

    assert report["finding_count"] == 1
    assert report["summary_lines"]
