from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard


def test_build_intelligence_dashboard_without_history(tmp_path: Path) -> None:
    payload = build_intelligence_dashboard(tmp_path)
    assert "jarvis" in payload
    assert payload["jarvis"]["score_pct"] == 0.0
    assert isinstance(payload["ranking"], list)
    assert "memory_regression" in payload


def test_build_intelligence_dashboard_with_history(tmp_path: Path) -> None:
    history_path = tmp_path / ".planning" / "capability_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00", "model": "m1", "score_pct": 62.0, "run_sha256": "", "prev_run_sha256": ""},
        {"ts": "2026-02-21T00:00:00+00:00", "model": "m1", "score_pct": 66.0, "run_sha256": "", "prev_run_sha256": ""},
        {"ts": "2026-02-22T00:00:00+00:00", "model": "m1", "score_pct": 70.0, "run_sha256": "", "prev_run_sha256": ""},
    ]
    history_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    payload = build_intelligence_dashboard(tmp_path)
    assert payload["jarvis"]["score_pct"] == 70.0
    assert payload["methodology"]["history_runs"] == 3
    assert len(payload["etas"]) >= 1
