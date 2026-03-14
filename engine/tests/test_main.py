"""Core CLI command tests for test_main.py.

This is the 'core' file with the most fundamental integration tests.

Covers: status, log, route, persona-config, brain-status/context (integration),
memory-snapshot/maintenance (integration), mobile-desktop-sync/self-heal (integration),
web-research, weather, open-web.

Additional test modules split from this file:
  - test_main_helpers.py: Utility/helper function tests
  - test_main_voice.py: Voice-related command tests
  - test_main_ops.py: Ops/daemon/mission/growth commands
  - test_main_memory.py: Memory/brain/knowledge/harvesting/learning commands (mock bus)
  - test_main_security.py: Security-related command tests
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jarvis_engine import main as main_mod
from jarvis_engine.voice import pipeline as voice_pipeline_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine import _bus as bus_mod


# ===========================================================================
# Integration tests that exercise the real command bus
# ===========================================================================


def test_cmd_brain_status_and_context(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import MagicMock
    import jarvis_engine.memory.auto_ingest as _auto_ingest_mod
    import jarvis_engine.memory.auto_ingest as _mem_auto_ingest_mod

    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(_auto_ingest_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(_mem_auto_ingest_mod, "repo_root", lambda: tmp_path)

    # Mock EmbeddingService to avoid loading real nomic-bert model
    fake_embed = MagicMock()
    fake_embed.embed.return_value = [0.1] * 384
    fake_embed.embed_query.return_value = [0.1] * 384
    fake_embed.embed_batch.return_value = [[0.1] * 384]
    monkeypatch.setattr(
        "jarvis_engine.memory.embeddings.EmbeddingService",
        lambda *a, **kw: fake_embed,
    )
    # Clear cached bus so it rebuilds with mock
    bus_mod._bus_cache["bus"] = None
    bus_mod._bus_cache["root"] = None
    _auto_ingest_mod._auto_ingest_state["store"] = None

    rid = _auto_ingest_mod.auto_ingest_memory_sync(
        source="user",
        kind="semantic",
        task_id="brain-seed",
        content="Remember that gaming mode should pause heavy workloads.",
    )
    assert rid

    rc_status = main_mod.cmd_brain_status(as_json=False)
    assert rc_status == 0

    rc_context = main_mod.cmd_brain_context(
        query="How do we handle gaming mode?",
        max_items=5,
        max_chars=1200,
        as_json=True,
    )
    assert rc_context == 0


def test_cmd_memory_snapshot_create_and_verify(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_create = main_mod.cmd_memory_snapshot(create=True, verify_path=None, note="test")
    assert rc_create == 0

    snap_dir = tmp_path / ".planning" / "brain" / "snapshots"
    snaps = list(snap_dir.glob("*.zip"))
    assert snaps

    rc_verify = main_mod.cmd_memory_snapshot(create=False, verify_path=str(snaps[0]), note="")
    assert rc_verify == 0


def test_cmd_memory_maintenance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_memory_maintenance(keep_recent=500, snapshot_note="nightly")
    assert rc == 0


def test_cmd_persona_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_persona_config(
        enable=True,
        disable=False,
        humor_level=3,
        mode="jarvis_british",
        style="brilliant_secret_agent",
    )
    assert rc == 0


def test_cmd_mobile_desktop_sync_and_self_heal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")

    rc_sync = main_mod.cmd_mobile_desktop_sync(auto_ingest=False, as_json=False)
    assert rc_sync == 0

    sync_report = tmp_path / ".planning" / "runtime" / "mobile_desktop_sync.json"
    assert sync_report.exists()

    rc_heal = main_mod.cmd_self_heal(
        force_maintenance=False,
        keep_recent=500,
        snapshot_note="test",
        as_json=False,
    )
    assert rc_heal == 0

    heal_report = tmp_path / ".planning" / "runtime" / "self_heal_report.json"
    assert heal_report.exists()


# ===========================================================================
# Status, log, route commands via mock bus
# ===========================================================================


class TestStatusCommand:
    """Tests for cmd_status."""

    def test_status_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import StatusResult
        result = StatusResult(
            profile="personal", primary_runtime="python3.12",
            secondary_runtime="ollama", security_strictness="high",
            operation_mode="hybrid", cloud_burst_enabled=True, events=[],
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "profile=personal" in out
        assert "cloud_burst_enabled=True" in out

    def test_status_with_events(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import StatusResult
        event = MagicMock()
        event.ts = "2026-02-25T10:00:00"
        event.event_type = "startup"
        event.message = "Engine started"
        result = StatusResult(events=[event])
        bus = mock_bus(result)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "startup" in out
        assert "Engine started" in out

    def test_status_no_events(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import StatusResult
        result = StatusResult(events=[])
        bus = mock_bus(result)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "- none" in out


class TestLogCommand:
    """Tests for cmd_log."""

    def test_log_event(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import LogResult
        result = LogResult(ts="2026-02-25T10:00:00", event_type="test", message="hello")
        bus = mock_bus(result)
        rc = main_mod.cmd_log(event_type="test", message="hello")
        assert rc == 0
        out = capsys.readouterr().out
        assert "test" in out
        assert "hello" in out


class TestRouteCommand:
    """Tests for cmd_route."""

    def test_route_low_easy(self, capsys, mock_bus):
        from jarvis_engine.commands.task_commands import RouteResult
        result = RouteResult(provider="ollama", reason="low risk local model")
        bus = mock_bus(result)
        rc = main_mod.cmd_route(risk="low", complexity="easy")
        assert rc == 0
        out = capsys.readouterr().out
        assert "provider=ollama" in out


# ===========================================================================
# Web research
# ===========================================================================


class TestWebResearch:
    """Tests for cmd_web_research."""

    def test_web_research_empty_query(self, capsys, monkeypatch):
        rc = main_mod.cmd_web_research(query="   ", max_results=8, max_pages=6, auto_ingest=True)
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_web_research_success(self, capsys, mock_bus):
        from jarvis_engine.commands.task_commands import WebResearchResult
        result = WebResearchResult(
            return_code=0,
            report={
                "query": "python asyncio", "scanned_url_count": 4,
                "findings": [
                    {"domain": "docs.python.org", "url": "https://docs.python.org/3/lib/asyncio.html",
                     "snippet": "asyncio is a library for writing concurrent code"},
                ],
            },
            auto_ingest_record_id="rec-99",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_web_research(query="python asyncio", max_results=8, max_pages=6, auto_ingest=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "web_research" in out
        assert "scanned_url_count=4" in out
        assert "auto_ingest_record_id=rec-99" in out

    def test_web_research_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.task_commands import WebResearchResult
        result = WebResearchResult(return_code=2, report={})
        bus = mock_bus(result)
        rc = main_mod.cmd_web_research(query="something", max_results=8, max_pages=6, auto_ingest=False)
        assert rc == 2


# ===========================================================================
# Weather, open-web
# ===========================================================================


class TestWeather:
    """Tests for cmd_weather."""

    def test_weather_success(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import WeatherResult
        result = WeatherResult(
            return_code=0, location="Austin, TX",
            current={"temp_F": "75", "temp_C": "24", "FeelsLikeF": "73", "humidity": "50"},
            description="Partly cloudy",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_weather(location="Austin, TX")
        assert rc == 0
        out = capsys.readouterr().out
        assert "weather_report" in out
        assert "temperature_f=75" in out
        assert "Partly cloudy" in out

    def test_weather_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import WeatherResult
        result = WeatherResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_weather(location="Nonexistent Place")
        assert rc == 2


class TestOpenWeb:
    """Tests for cmd_open_web."""

    def test_open_web_success(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import OpenWebResult
        result = OpenWebResult(return_code=0, opened_url="https://example.com")
        bus = mock_bus(result)
        rc = main_mod.cmd_open_web(url="https://example.com")
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened_url=https://example.com" in out

    def test_open_web_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.system_commands import OpenWebResult
        result = OpenWebResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_open_web(url="")
        assert rc == 2
