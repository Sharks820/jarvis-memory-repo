from __future__ import annotations

from jarvis_engine import main as main_mod


def test_cmd_serve_mobile_requires_token_and_signing_key(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
    rc = main_mod.cmd_serve_mobile(host="127.0.0.1", port=8787, token=None, signing_key=None)
    assert rc == 2


def test_cmd_serve_mobile_uses_env_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_mobile_server(host: str, port: int, auth_token: str, signing_key: str, repo_root) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["auth_token"] = auth_token
        captured["signing_key"] = signing_key
        captured["repo_root"] = repo_root

    monkeypatch.setenv("JARVIS_MOBILE_TOKEN", "env-auth")
    monkeypatch.setenv("JARVIS_MOBILE_SIGNING_KEY", "env-sign")
    monkeypatch.setattr(main_mod, "run_mobile_server", fake_run_mobile_server)

    rc = main_mod.cmd_serve_mobile(host="127.0.0.1", port=9001, token=None, signing_key=None)
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["auth_token"] == "env-auth"
    assert captured["signing_key"] == "env-sign"

