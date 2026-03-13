from __future__ import annotations

from pathlib import Path

from jarvis_engine.ops import sync as ops_sync


def test_calendar_remote_url_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CALENDAR_ICS_URL", "https://example.com/calendar.ics")
    monkeypatch.delenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", raising=False)
    monkeypatch.delenv("JARVIS_CALENDAR_JSON", raising=False)
    monkeypatch.delenv("JARVIS_CALENDAR_ICS_FILE", raising=False)

    called = {"opener": False}

    def fake_build_opener():  # pragma: no cover - guard only
        called["opener"] = True
        raise AssertionError("opener should not be built when remote calendar URLs are disabled")

    monkeypatch.setattr(ops_sync, "_build_no_redirect_opener", fake_build_opener)
    events = ops_sync.load_calendar_events()
    assert events == []
    assert called["opener"] is False


def test_feed_loader_blocks_unc_paths_even_when_external_allowed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_EXTERNAL_FEEDS", "true")
    monkeypatch.setenv("JARVIS_MEDICATIONS_JSON", r"\\malicious\share\feed.json")
    result = ops_sync._load_feed_json_list(tmp_path, "JARVIS_MEDICATIONS_JSON", tmp_path / "default.json")
    assert result == []


def test_feed_loader_rejects_external_path_when_not_allowed(monkeypatch, tmp_path: Path) -> None:
    external = tmp_path.parent / "outside.json"
    external.write_text("[]\n", encoding="utf-8")
    monkeypatch.delenv("JARVIS_ALLOW_EXTERNAL_FEEDS", raising=False)
    monkeypatch.setenv("JARVIS_PROJECTS_JSON", str(external))
    result = ops_sync._load_feed_json_list(tmp_path, "JARVIS_PROJECTS_JSON", tmp_path / "default.json")
    assert result == []
