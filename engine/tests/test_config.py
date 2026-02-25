"""Tests for EngineConfig and config loading."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine.config import EngineConfig, load_config, repo_root


# ---------------------------------------------------------------------------
# EngineConfig dataclass defaults
# ---------------------------------------------------------------------------

def test_engine_config_defaults() -> None:
    cfg = EngineConfig()
    assert cfg.profile == "balanced"
    assert cfg.primary_runtime == "desktop_pc"
    assert cfg.cloud_burst_enabled is False
    assert cfg.regression_gate_enabled is True
    assert cfg.capability_mode == "tiered_authorization"
    assert cfg.access_channels == ["desktop"]


# ---------------------------------------------------------------------------
# repo_root() resolution
# ---------------------------------------------------------------------------

def test_repo_root_returns_path_with_engine_dir() -> None:
    root = repo_root()
    assert (root / "engine").is_dir()


def test_repo_root_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo_root()
    monkeypatch.setenv("JARVIS_REPO_ROOT", str(root))
    assert repo_root() == root


def test_repo_root_env_var_invalid_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_REPO_ROOT", "/nonexistent/path")
    # Should still find it via walk-up
    root = repo_root()
    assert (root / "engine").is_dir()


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------

def test_load_config_returns_defaults_when_no_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "balanced"
    assert cfg.cloud_burst_enabled is False


def test_load_config_reads_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({
            "profile": "aggressive",
            "cloud_burst_enabled": True,
        }), encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "aggressive"
    assert cfg.cloud_burst_enabled is True


def test_load_config_env_profile_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ENGINE_PROFILE", "performance")
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"profile": "balanced"}', encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "performance"


def test_load_config_env_profile_override_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var override works even when config file doesn't exist."""
    monkeypatch.setenv("JARVIS_ENGINE_PROFILE", "aggressive")
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "aggressive"


def test_load_config_ignores_unknown_keys() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({
            "profile": "balanced",
            "unknown_future_key": "value",
        }), encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "balanced"
    assert not hasattr(cfg, "unknown_future_key")


def test_load_config_survives_corrupt_json() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text("{broken json", encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.profile == "balanced"  # defaults


# ---------------------------------------------------------------------------
# default_query_model field
# ---------------------------------------------------------------------------

def test_engine_config_default_query_model() -> None:
    """EngineConfig has a default_query_model field with correct default."""
    cfg = EngineConfig()
    assert cfg.default_query_model == "claude-sonnet-4-5-20250929"


def test_load_config_default_query_model_from_file() -> None:
    """default_query_model can be set from config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({
            "default_query_model": "llama3-70b",
        }), encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.default_query_model == "llama3-70b"


def test_load_config_default_query_model_uses_default_when_absent() -> None:
    """default_query_model uses its default when not in config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_root = Path(tmpdir)
        (fake_root / "engine").mkdir()
        config_dir = fake_root / ".planning"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({
            "profile": "balanced",
        }), encoding="utf-8")
        with patch("jarvis_engine.config.repo_root", return_value=fake_root):
            cfg = load_config()
    assert cfg.default_query_model == "claude-sonnet-4-5-20250929"
