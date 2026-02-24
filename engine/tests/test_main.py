from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine import main as main_mod


def test_sanitize_memory_content_redacts_credentials() -> None:
    content = "master password: ExamplePass123! token=abc123"
    cleaned = main_mod._sanitize_memory_content(content)  # type: ignore[attr-defined]
    assert "ExamplePass123!" not in cleaned
    assert "abc123" not in cleaned
    assert "[redacted]" in cleaned


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


def test_cmd_voice_run_execute_requires_voice_auth() -> None:
    rc = main_mod.cmd_voice_run(
        text="Jarvis, sync my calendar and inbox",
        execute=True,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_state_mutation_requires_voice_auth() -> None:
    rc = main_mod.cmd_voice_run(
        text="Jarvis, pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_owner_guard_blocks_non_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, runtime status",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="other_user",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_owner_guard_allows_read_only_without_voice_auth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, runtime status",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 0


def test_cmd_voice_run_owner_guard_requires_voice_auth_for_mutation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_owner_guard_allows_bare_wake_word(tmp_path: Path, monkeypatch, capsys) -> None:
    """Bare wake words like 'Jarvis' should not be blocked by owner guard."""
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    for wake_word in ["Jarvis", "hey jarvis", "Hi Jarvis", "hello jarvis", "ok jarvis"]:
        capsys.readouterr()  # clear buffer
        main_mod.cmd_voice_run(
            text=wake_word,
            execute=False,
            approve_privileged=False,
            speak=False,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            voice_user="conner",
            voice_auth_wav="",
            voice_threshold=0.82,
            master_password="",
        )
        captured = capsys.readouterr()
        assert "owner_guard_blocked" not in captured.out, (
            f"Owner guard should not block bare wake word: {wake_word!r}"
        )


def test_cmd_voice_run_owner_guard_allows_with_master_password(tmp_path: Path, monkeypatch) -> None:
    """Master password should bypass owner guard for any command."""
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="TestPass123!",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="TestPass123!",
    )
    assert rc != 2, "Master password should bypass owner guard"


def test_cmd_phone_spam_guard_can_run_without_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    call_log_path = tmp_path / "calls.json"
    report_path = tmp_path / "report.json"
    queue_path = tmp_path / "queue.jsonl"
    call_log_path.write_text(
        json.dumps(
            [
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:00:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:01:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:02:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:03:00+00:00"},
            ]
        ),
        encoding="utf-8",
    )
    rc = main_mod.cmd_phone_spam_guard(
        call_log_path=call_log_path,
        report_path=report_path,
        queue_path=queue_path,
        threshold=0.65,
        queue_actions=False,
    )
    assert rc == 0
    assert report_path.exists()
    assert queue_path.exists() is False


def test_cmd_gaming_mode_persists_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_gaming_mode(enable=True, reason="gaming", auto_detect="")
    assert rc == 0

    state_path = tmp_path / ".planning" / "runtime" / "gaming_mode.json"
    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["enabled"] is True
    assert raw["reason"] == "gaming"

    rc2 = main_mod.cmd_gaming_mode(enable=False, reason="", auto_detect="")
    assert rc2 == 0
    raw2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw2["enabled"] is False


def test_cmd_daemon_run_uses_active_interval_when_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(main_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 2
    assert calls["sleep"] == [120]


def test_cmd_daemon_run_skips_autopilot_while_gaming_mode_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_gaming_mode(enable=True, reason="session", auto_detect="")
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 0.0)

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(main_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_daemon_run_skips_autopilot_when_auto_detect_finds_game(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_gaming_mode(enable=False, reason="auto", auto_detect="on")
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (True, "fortniteclient-win64-shipping.exe"))

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(main_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_runtime_control_persists_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_runtime_control(
        pause=True,
        resume=False,
        safe_on=True,
        safe_off=False,
        reset=False,
        reason="maintenance",
    )
    assert rc == 0
    state_path = tmp_path / ".planning" / "runtime" / "control.json"
    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["daemon_paused"] is True
    assert raw["safe_mode"] is True
    assert raw["reason"] == "maintenance"


def test_cmd_brain_status_and_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rid = main_mod._auto_ingest_memory(  # type: ignore[attr-defined]
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
    rc_create = main_mod.cmd_memory_snapshot(create=True, verify_path=None, note="test")
    assert rc_create == 0

    snap_dir = tmp_path / ".planning" / "brain" / "snapshots"
    snaps = list(snap_dir.glob("*.zip"))
    assert snaps

    rc_verify = main_mod.cmd_memory_snapshot(create=False, verify_path=str(snaps[0]), note="")
    assert rc_verify == 0


def test_cmd_memory_maintenance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_memory_maintenance(keep_recent=500, snapshot_note="nightly")
    assert rc == 0


def test_cmd_persona_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_persona_config(
        enable=True,
        disable=False,
        humor_level=3,
        mode="jarvis_british",
        style="brilliant_secret_agent",
    )
    assert rc == 0


def test_cmd_daemon_run_skips_autopilot_when_runtime_paused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_runtime_control(
        pause=True,
        resume=False,
        safe_on=False,
        safe_off=False,
        reset=False,
        reason="pause test",
    )
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(main_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_daemon_run_safe_mode_forces_non_execute_cycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_runtime_control(
        pause=False,
        resume=False,
        safe_on=True,
        safe_off=False,
        reset=False,
        reason="safe",
    )
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))

    observed: dict[str, bool] = {"execute": True, "approve_privileged": True}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        observed["execute"] = bool(kwargs.get("execute"))
        observed["approve_privileged"] = bool(kwargs.get("approve_privileged"))
        return 0

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=True,
        approve_privileged=True,
        auto_open_connectors=False,
        max_cycles=1,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert observed["execute"] is False
    assert observed["approve_privileged"] is False


def test_cmd_voice_run_routes_web_research(monkeypatch) -> None:
    observed: dict[str, str] = {}

    def fake_web_research(query: str, *, max_results: int, max_pages: int, auto_ingest: bool) -> int:
        observed["query"] = query
        observed["max_results"] = str(max_results)
        observed["max_pages"] = str(max_pages)
        observed["auto_ingest"] = str(auto_ingest)
        return 0

    monkeypatch.setattr(main_mod, "cmd_web_research", fake_web_research)
    rc = main_mod.cmd_voice_run(
        text="Jarvis, search the web for samsung galaxy s25 spam call filtering",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 0
    assert "spam call filtering" in observed["query"]


def test_cmd_mobile_desktop_sync_and_self_heal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
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
