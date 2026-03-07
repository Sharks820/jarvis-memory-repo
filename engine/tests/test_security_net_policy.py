from __future__ import annotations

from jarvis_engine.security.net_policy import is_safe_ollama_endpoint


def test_safe_local_ollama_endpoints(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT", raising=False)
    assert is_safe_ollama_endpoint("http://127.0.0.1:11434")
    assert is_safe_ollama_endpoint("http://localhost:11434")
    assert is_safe_ollama_endpoint("http://[::1]:11434")


def test_rejects_non_http_schemes(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT", raising=False)
    assert not is_safe_ollama_endpoint("ftp://localhost:11434")
    assert not is_safe_ollama_endpoint("localhost:11434")
    assert not is_safe_ollama_endpoint("")


def test_nonlocal_only_allowed_with_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT", "true")
    assert is_safe_ollama_endpoint("https://example.com:11434")
    monkeypatch.setenv("JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT", "")
    assert not is_safe_ollama_endpoint("https://example.com:11434")

