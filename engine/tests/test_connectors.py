from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.connectors import (
    build_connector_prompts,
    evaluate_connector_statuses,
    grant_connector_permission,
)


def test_connector_prompts_include_voice_and_tap_options(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "JARVIS_CALENDAR_JSON",
        "JARVIS_CALENDAR_ICS_FILE",
        "JARVIS_CALENDAR_ICS_URL",
        "JARVIS_EMAIL_JSON",
        "JARVIS_IMAP_HOST",
        "JARVIS_IMAP_USER",
        "JARVIS_IMAP_PASS",
        "JARVIS_MOBILE_TOKEN",
        "JARVIS_MOBILE_SIGNING_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    statuses = evaluate_connector_statuses(tmp_path)
    prompts = build_connector_prompts(statuses)
    assert prompts
    for prompt in prompts:
        assert prompt.get("option_voice", "")
        assert prompt.get("option_tap_url", "")


def test_connector_ready_after_permission_and_configuration(tmp_path: Path, monkeypatch) -> None:
    calendar_json = tmp_path / "calendar.json"
    calendar_json.write_text(json.dumps([{"title": "x"}]), encoding="utf-8")
    monkeypatch.setenv("JARVIS_CALENDAR_JSON", str(calendar_json))
    grant_connector_permission(tmp_path, connector_id="calendar", scopes=["read_calendar"])

    statuses = evaluate_connector_statuses(tmp_path)
    calendar = next(s for s in statuses if s.connector_id == "calendar")
    assert calendar.permission_granted is True
    assert calendar.configured is True
    assert calendar.ready is True
