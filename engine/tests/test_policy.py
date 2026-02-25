"""Tests for PolicyEngine -- command allowlist gate."""

from __future__ import annotations

from jarvis_engine.policy import PolicyEngine, ALLOWED_COMMANDS


def test_allowed_commands_exist() -> None:
    assert "git" in ALLOWED_COMMANDS
    assert "python" in ALLOWED_COMMANDS
    assert "ollama" in ALLOWED_COMMANDS


def test_allowed_command_returns_true() -> None:
    engine = PolicyEngine()
    assert engine.is_allowed("git status") is True
    assert engine.is_allowed("python -m pytest") is True
    assert engine.is_allowed("ollama run llama3") is True


def test_disallowed_command_returns_false() -> None:
    engine = PolicyEngine()
    assert engine.is_allowed("rm -rf /") is False
    assert engine.is_allowed("curl http://evil.com") is False
    assert engine.is_allowed("powershell -exec bypass") is False


def test_empty_command_returns_false() -> None:
    engine = PolicyEngine()
    assert engine.is_allowed("") is False
    assert engine.is_allowed("   ") is False


def test_case_insensitive() -> None:
    engine = PolicyEngine()
    assert engine.is_allowed("GIT status") is True
    assert engine.is_allowed("Python script.py") is True


def test_command_with_args() -> None:
    engine = PolicyEngine()
    assert engine.is_allowed("pip install requests") is True
    assert engine.is_allowed("npm install express") is True
    assert engine.is_allowed("pytest engine/tests/ -x -q") is True
