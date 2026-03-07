"""Tests for helper/utility functions used by CLI commands.

Covers: sanitize_memory_content, _extract_first_phone_number, _extract_weather_location,
_extract_web_query, _extract_first_url, _is_read_only_voice_request, auto_ingest_memory,
gaming process helpers, _load_auto_ingest_hashes, valid sources/kinds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine import main as main_mod
from jarvis_engine import voice_pipeline as voice_pipeline_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine import auto_ingest as auto_ingest_mod
from jarvis_engine import _bus as bus_mod


# ===========================================================================
# Helper functions
# ===========================================================================


class TestHelperFunctions:
    """Tests for private helper functions in main.py."""

    @pytest.mark.parametrize("text,expected", [
        pytest.param("Call +14155551234 please", "+14155551234", id="international_format"),
        pytest.param("no number here", "", id="no_number"),
        pytest.param("dial 555-123-4567", "555-123-4567", id="dashed_format"),
        pytest.param("x" * 300 + "+14155551234", "", id="truncation_at_256_chars"),
    ])
    def test_extract_first_phone_number(self, text, expected):
        assert voice_pipeline_mod._extract_first_phone_number(text) == expected

    @pytest.mark.parametrize("text,expected", [
        pytest.param("weather in Austin, TX", "Austin, TX", id="city_state"),
        pytest.param("weather for New York", "New York", id="city_for"),
        pytest.param("forecast at Chicago", "Chicago", id="forecast_at"),
    ])
    def test_extract_weather_location(self, text, expected):
        assert voice_pipeline_mod._extract_weather_location(text) == expected

    def test_extract_weather_location_strips_noise_words(self):
        loc = voice_pipeline_mod._extract_weather_location("weather today")
        assert "today" not in loc.lower().split()

    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("search the web for python asyncio", "python", id="search_web"),
        pytest.param("research ML frameworks", "ml", id="research"),
        pytest.param("look up rust programming", "rust", id="look_up"),
        pytest.param("find on the web react hooks", "react", id="find_on_web"),
    ])
    def test_extract_web_query(self, text, expected_substr):
        assert expected_substr in voice_pipeline_mod._extract_web_query(text)

    @pytest.mark.parametrize("text,expected", [
        pytest.param("go to https://example.com", "https://example.com", id="https_url"),
        pytest.param("visit www.google.com", "https://www.google.com", id="www_url"),
        pytest.param("no url here", "", id="no_url"),
        pytest.param("x" * 1300 + "https://late.com", "", id="truncation_at_1024_chars"),
    ])
    def test_extract_first_url(self, text, expected):
        assert voice_pipeline_mod._extract_first_url(text) == expected

    @pytest.mark.parametrize("text,execute,approve,expected", [
        pytest.param("runtime status", False, False, True, id="read_only_status"),
        pytest.param("pause daemon", False, False, False, id="mutation_pause"),
        pytest.param("runtime status", True, False, False, id="execute_flag_forces_non_readonly"),
        pytest.param("jarvis", False, False, True, id="bare_wake_word"),
        pytest.param("hey jarvis", False, False, True, id="hey_wake_word"),
        pytest.param("what is the meaning of life", False, False, False, id="conversational_fallthrough"),
    ])
    def test_is_read_only_voice_request(self, text, execute, approve, expected):
        assert voice_pipeline_mod._is_read_only_voice_request(
            text, execute=execute, approve_privileged=approve
        ) is expected

    def test_sanitize_memory_content_truncation(self):
        long_content = "a" * 200_000
        cleaned = auto_ingest_mod.sanitize_memory_content(long_content)
        assert len(cleaned) <= 2000

    @pytest.mark.parametrize("content,forbidden_substr", [
        pytest.param('{"api_key": "sk-secret123", "data": "normal"}', "sk-secret123", id="json_api_key"),
        pytest.param("Authorization: bearer sk-my-token-abc", "sk-my-token-abc", id="bearer_token"),
        pytest.param("master password: ExamplePass123! token=abc123", "ExamplePass123!", id="master_password"),
    ])
    def test_sanitize_memory_content_redacts_secrets(self, content, forbidden_substr):
        cleaned = auto_ingest_mod.sanitize_memory_content(content)
        assert forbidden_substr not in cleaned

    def test_valid_sources_and_kinds(self):
        assert "user" in auto_ingest_mod.VALID_SOURCES
        assert "claude" in auto_ingest_mod.VALID_SOURCES
        assert "episodic" in auto_ingest_mod.VALID_KINDS
        assert "semantic" in auto_ingest_mod.VALID_KINDS
        assert "procedural" in auto_ingest_mod.VALID_KINDS

    @pytest.mark.parametrize("file_content,expected", [
        pytest.param(None, [], id="missing_file"),
        pytest.param("not json at all", [], id="corrupted_json"),
        pytest.param(json.dumps(["not", "a", "dict"]), [], id="wrong_type_list"),
        pytest.param(json.dumps({"hashes": ["abc", "def"]}), ["abc", "def"], id="valid_hashes"),
    ])
    def test_load_auto_ingest_hashes(self, tmp_path, file_content, expected):
        path = tmp_path / "dedupe.json"
        if file_content is not None:
            path.write_text(file_content, encoding="utf-8")
        result = auto_ingest_mod._load_auto_ingest_hashes(path)
        assert result == expected


class TestAutoIngestMemory:
    """Tests for _auto_ingest_memory."""

    @pytest.mark.parametrize("env_disable,source,kind,content", [
        pytest.param("1", "user", "semantic", "Test content", id="disabled_by_env"),
        pytest.param(None, "invalid_source", "semantic", "Test", id="invalid_source"),
        pytest.param(None, "user", "bogus", "Test", id="invalid_kind"),
        pytest.param(None, "user", "semantic", "", id="empty_content"),
    ])
    def test_auto_ingest_returns_empty(self, monkeypatch, tmp_path,
                                        env_disable, source, kind, content):
        if env_disable is not None:
            monkeypatch.setenv("JARVIS_AUTO_INGEST_DISABLE", env_disable)
        else:
            monkeypatch.delenv("JARVIS_AUTO_INGEST_DISABLE", raising=False)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        result = auto_ingest_mod.auto_ingest_memory(
            source=source, kind=kind, task_id="test", content=content,
        )
        assert result == ""


class TestGamingProcessHelpers:
    """Tests for gaming mode helper functions."""

    def test_read_gaming_mode_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        state = daemon_loop_mod.read_gaming_mode_state()
        assert state["enabled"] is False

    def test_read_gaming_mode_corrupted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        path = tmp_path / ".planning" / "runtime" / "gaming_mode.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("corrupt json!", encoding="utf-8")
        state = daemon_loop_mod.read_gaming_mode_state()
        assert state["enabled"] is False

    def testload_gaming_processes_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        processes = daemon_loop_mod.load_gaming_processes()
        assert len(processes) > 0
        assert any("FortniteClient" in p for p in processes)

    def testload_gaming_processes_from_env(self, monkeypatch):
        monkeypatch.setenv("JARVIS_GAMING_PROCESSES", "game1.exe,game2.exe")
        processes = daemon_loop_mod.load_gaming_processes()
        assert processes == ["game1.exe", "game2.exe"]

    def testload_gaming_processes_from_file_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"processes": ["custom.exe"]}), encoding="utf-8")
        processes = daemon_loop_mod.load_gaming_processes()
        assert processes == ["custom.exe"]

    def testload_gaming_processes_from_file_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(["listgame.exe"]), encoding="utf-8")
        processes = daemon_loop_mod.load_gaming_processes()
        assert processes == ["listgame.exe"]

    def testload_gaming_processes_empty_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"processes": []}), encoding="utf-8")
        processes = daemon_loop_mod.load_gaming_processes()
        assert len(processes) == len(daemon_loop_mod.DEFAULT_GAMING_PROCESSES)
