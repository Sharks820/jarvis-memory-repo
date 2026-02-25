from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine import main as main_mod


def test_sanitize_memory_content_redacts_credentials() -> None:
    content = "master password: EatFish82001! token=abc123"
    cleaned = main_mod._sanitize_memory_content(content)  # type: ignore[attr-defined]
    assert "EatFish82001!" not in cleaned
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


# ---------------------------------------------------------------------------
# Expanded test coverage — 100+ new tests for untested CLI command paths
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch, PropertyMock
import pytest


# ---- Helper to build a mock bus that returns a given result ----

def _make_bus_mock(result_obj):
    """Create a mock _get_bus() that returns *result_obj* from dispatch()."""
    bus = MagicMock()
    bus.dispatch.return_value = result_obj
    return bus


# ===========================================================================
# Knowledge graph commands
# ===========================================================================

class TestKnowledgeStatus:
    """Tests for cmd_knowledge_status."""

    def test_knowledge_status_text_mode(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import KnowledgeStatusResult
        result = KnowledgeStatusResult(node_count=42, edge_count=100, locked_count=3,
                                       pending_contradictions=1, graph_hash="abc123")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_knowledge_status(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "node_count=42" in out
        assert "edge_count=100" in out
        assert "locked_count=3" in out
        assert "pending_contradictions=1" in out
        assert "graph_hash=abc123" in out

    def test_knowledge_status_json_mode(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import KnowledgeStatusResult
        result = KnowledgeStatusResult(node_count=10, edge_count=20, locked_count=0,
                                       pending_contradictions=0, graph_hash="def456")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_knowledge_status(as_json=True)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["node_count"] == 10
        assert parsed["graph_hash"] == "def456"


class TestContradictionList:
    """Tests for cmd_contradiction_list."""

    def test_contradiction_list_empty(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        result = ContradictionListResult(contradictions=[])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_contradiction_list(status="pending", limit=20, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No contradictions found" in out

    def test_contradiction_list_with_items(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        items = [
            {"contradiction_id": 1, "node_id": "n1", "existing_value": "old",
             "incoming_value": "new", "status": "pending", "created_at": "2026-01-01"},
        ]
        result = ContradictionListResult(contradictions=items)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_contradiction_list(status="pending", limit=20, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id=1" in out
        assert "node=n1" in out

    def test_contradiction_list_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import ContradictionListResult
        items = [{"contradiction_id": 5}]
        result = ContradictionListResult(contradictions=items)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_contradiction_list(status="", limit=10, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["contradictions"][0]["contradiction_id"] == 5


class TestContradictionResolve:
    """Tests for cmd_contradiction_resolve."""

    def test_resolve_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import ContradictionResolveResult
        result = ContradictionResolveResult(success=True, node_id="n1",
                                            resolution="accept_new", message="Resolved.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_contradiction_resolve(contradiction_id=1, resolution="accept_new", merge_value="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "resolved=true" in out
        assert "node_id=n1" in out

    def test_resolve_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import ContradictionResolveResult
        result = ContradictionResolveResult(success=False, message="Not found.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_contradiction_resolve(contradiction_id=99, resolution="keep_old", merge_value="")
        assert rc == 1
        out = capsys.readouterr().out
        assert "resolved=false" in out


class TestFactLock:
    """Tests for cmd_fact_lock."""

    def test_lock_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=True, node_id="fact1", locked=True)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_fact_lock(node_id="fact1", action="lock")
        assert rc == 0
        out = capsys.readouterr().out
        assert "success=true" in out

    def test_lock_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=False, node_id="missing")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_fact_lock(node_id="missing", action="unlock")
        assert rc == 1

    def test_unlock_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import FactLockResult
        result = FactLockResult(success=True, node_id="fact2", locked=False)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_fact_lock(node_id="fact2", action="unlock")
        assert rc == 0
        out = capsys.readouterr().out
        assert "locked=False" in out


class TestKnowledgeRegression:
    """Tests for cmd_knowledge_regression."""

    def test_regression_text(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        report = {
            "status": "ok",
            "message": "All good",
            "discrepancies": [],
            "current": {"node_count": 10, "edge_count": 20, "locked_count": 1, "graph_hash": "aaa"},
        }
        result = KnowledgeRegressionResult(report=report)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "status=ok" in out
        assert "nodes=10" in out

    def test_regression_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        result = KnowledgeRegressionResult(report={"status": "degraded"})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "degraded"

    def test_regression_with_discrepancies(self, capsys, monkeypatch):
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionResult
        report = {
            "status": "warning",
            "discrepancies": [
                {"severity": "high", "type": "missing_node", "message": "Node X missing"},
            ],
            "current": {},
        }
        result = KnowledgeRegressionResult(report=report)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_knowledge_regression(snapshot_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing_node" in out


# ===========================================================================
# Harvesting commands
# ===========================================================================

class TestHarvest:
    """Tests for cmd_harvest."""

    def test_harvest_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import HarvestTopicResult
        result = HarvestTopicResult(
            topic="quantum computing",
            results=[
                {"provider": "anthropic", "status": "ok", "records_created": 3, "cost_usd": 0.001},
                {"provider": "groq", "status": "ok", "records_created": 2, "cost_usd": 0.0005},
            ],
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_harvest(topic="quantum computing", providers=None, max_tokens=2048)
        assert rc == 0
        out = capsys.readouterr().out
        assert "harvest_topic=quantum computing" in out
        assert "provider=anthropic" in out
        assert "records=3" in out

    def test_harvest_with_provider_filter(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import HarvestTopicResult
        result = HarvestTopicResult(topic="ML", results=[], return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_harvest(topic="ML", providers="groq,mistral", max_tokens=1024)
        assert rc == 0
        # Verify providers were parsed into a list
        cmd = bus.dispatch.call_args[0][0]
        assert cmd.providers == ["groq", "mistral"]


class TestIngestSession:
    """Tests for cmd_ingest_session."""

    def test_ingest_session_claude(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import IngestSessionResult
        result = IngestSessionResult(source="claude", sessions_processed=5, records_created=12, return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ingest_session(source="claude", session_path=None, project_path=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "sessions_processed=5" in out
        assert "records_created=12" in out

    def test_ingest_session_with_path(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import IngestSessionResult
        result = IngestSessionResult(source="codex", sessions_processed=1, records_created=4, return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ingest_session(source="codex", session_path="/tmp/session.json", project_path=None)
        assert rc == 0


class TestHarvestBudget:
    """Tests for cmd_harvest_budget."""

    def test_budget_status(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import HarvestBudgetResult
        result = HarvestBudgetResult(
            summary={"period_days": 30, "total_cost_usd": 0.15,
                     "providers": [{"provider": "groq", "total_cost_usd": 0.10, "total_requests": 50}]},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_harvest_budget(action="status", provider=None, period=None,
                                         limit_usd=None, limit_requests=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "budget_period_days=30" in out
        assert "provider=groq" in out

    def test_budget_set(self, capsys, monkeypatch):
        from jarvis_engine.commands.harvest_commands import HarvestBudgetResult
        result = HarvestBudgetResult(
            summary={"provider": "groq", "period": "daily", "limit_usd": 1.0},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_harvest_budget(action="set", provider="groq", period="daily",
                                         limit_usd=1.0, limit_requests=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "budget_set" in out
        assert "provider=groq" in out


# ===========================================================================
# Learning commands
# ===========================================================================

class TestLearn:
    """Tests for cmd_learn."""

    def test_learn_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.learning_commands import LearnInteractionResult
        result = LearnInteractionResult(records_created=2, message="Learned 2 patterns.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_learn(user_message="How's the weather?", assistant_response="It's sunny.")
        assert rc == 0
        out = capsys.readouterr().out
        assert "records_created=2" in out
        assert "Learned 2 patterns" in out


class TestCrossBranchQuery:
    """Tests for cmd_cross_branch_query."""

    def test_cross_branch_query(self, capsys, monkeypatch):
        from jarvis_engine.commands.learning_commands import CrossBranchQueryResult
        result = CrossBranchQueryResult(
            direct_results=[{"record_id": "r1", "distance": 0.12}],
            cross_branch_connections=[
                {"source_branch": "tech", "target_branch": "health", "relation": "related"},
            ],
            branches_involved=["tech", "health"],
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_cross_branch_query(query="AI in healthcare", k=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "direct_results=1" in out
        assert "cross_branch_connections=1" in out
        assert "tech" in out
        assert "health" in out

    def test_cross_branch_query_empty(self, capsys, monkeypatch):
        from jarvis_engine.commands.learning_commands import CrossBranchQueryResult
        result = CrossBranchQueryResult()
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_cross_branch_query(query="nonexistent topic", k=5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "direct_results=0" in out


class TestFlagExpired:
    """Tests for cmd_flag_expired."""

    def test_flag_expired(self, capsys, monkeypatch):
        from jarvis_engine.commands.learning_commands import FlagExpiredFactsResult
        result = FlagExpiredFactsResult(expired_count=7, message="Flagged 7 expired facts.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_flag_expired()
        assert rc == 0
        out = capsys.readouterr().out
        assert "expired_count=7" in out


# ===========================================================================
# Proactive / cost / self-test commands
# ===========================================================================

class TestProactiveCheck:
    """Tests for cmd_proactive_check."""

    def test_proactive_no_alerts(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import ProactiveCheckResult
        result = ProactiveCheckResult(alerts_fired=0, alerts="[]", message="No alerts.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_proactive_check(snapshot_path="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "alerts_fired=0" in out

    def test_proactive_with_alerts(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import ProactiveCheckResult
        alerts_json = json.dumps([{"rule_id": "bill_due", "message": "Electric bill due tomorrow"}])
        result = ProactiveCheckResult(alerts_fired=1, alerts=alerts_json, message="1 alert triggered.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_proactive_check(snapshot_path="/tmp/snapshot.json")
        assert rc == 0
        out = capsys.readouterr().out
        assert "alerts_fired=1" in out
        assert "bill_due" in out


class TestCostReduction:
    """Tests for cmd_cost_reduction."""

    def test_cost_reduction(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import CostReductionResult
        result = CostReductionResult(local_pct=85.3, cloud_cost_usd=0.42,
                                     trend="improving", message="Costs trending down.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_cost_reduction(days=30)
        assert rc == 0
        out = capsys.readouterr().out
        assert "local_pct=85.3" in out
        assert "cloud_cost_usd=0.42" in out
        assert "trend=improving" in out


class TestSelfTest:
    """Tests for cmd_self_test."""

    def test_self_test_passes(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import SelfTestResult
        result = SelfTestResult(
            average_score=0.85,
            tasks_run=5,
            regression_detected=False,
            message="All tests passed.",
            per_task_scores=[
                {"task_id": "recall_1", "score": 0.9},
                {"task_id": "recall_2", "score": 0.8},
            ],
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_self_test(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "average_score=0.8500" in out
        assert "tasks_run=5" in out
        assert "regression_detected=False" in out
        assert "recall_1" in out

    def test_self_test_with_regression(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import SelfTestResult
        result = SelfTestResult(
            average_score=0.3,
            tasks_run=3,
            regression_detected=True,
            message="Regression detected!",
            per_task_scores=[],
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_self_test(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "regression_detected=True" in out


class TestWakeWord:
    """Tests for cmd_wake_word."""

    def test_wake_word_not_started(self, capsys, monkeypatch):
        from jarvis_engine.commands.proactive_commands import WakeWordStartResult
        result = WakeWordStartResult(started=False, message="pyaudio not installed.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_wake_word(threshold=0.5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "started=False" in out


# ===========================================================================
# Brain commands (compact, regression, context edge cases)
# ===========================================================================

class TestBrainCompact:
    """Tests for cmd_brain_compact."""

    def test_brain_compact_text(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainCompactResult
        result = BrainCompactResult(result={"compacted": True, "removed": 50, "kept": 1800})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_compact(keep_recent=1800, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "compacted=True" in out
        assert "removed=50" in out

    def test_brain_compact_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainCompactResult
        result = BrainCompactResult(result={"compacted": True})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_compact(keep_recent=500, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["compacted"] is True


class TestBrainRegression:
    """Tests for cmd_brain_regression."""

    def test_brain_regression_text(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainRegressionResult
        result = BrainRegressionResult(report={"status": "healthy", "duplicate_ratio": 0.02})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_regression(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "status=healthy" in out

    def test_brain_regression_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainRegressionResult
        result = BrainRegressionResult(report={"status": "ok"})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_regression(as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "ok"


class TestBrainContext:
    """Tests for cmd_brain_context edge cases."""

    def test_brain_context_empty_query(self, capsys, monkeypatch):
        rc = main_mod.cmd_brain_context(query="   ", max_items=5, max_chars=1200, as_json=False)
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_brain_context_json_output(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainContextResult
        result = BrainContextResult(packet={
            "query": "gaming", "selected_count": 1,
            "selected": [{"branch": "tech", "source": "user", "kind": "semantic", "summary": "Gaming modes..."}],
            "canonical_facts": [{"key": "mode", "value": "gaming", "confidence": 0.9}],
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_context(query="gaming", max_items=5, max_chars=1200, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["query"] == "gaming"

    def test_brain_context_text_output(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainContextResult
        result = BrainContextResult(packet={
            "query": "test", "selected_count": 0, "selected": [], "canonical_facts": [],
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_context(query="test", max_items=5, max_chars=1200, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "brain_context" in out


class TestBrainStatus:
    """Tests for cmd_brain_status."""

    def test_brain_status_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainStatusResult
        result = BrainStatusResult(status={"updated_utc": "2026-01-01", "branch_count": 5, "branches": []})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_status(as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["branch_count"] == 5

    def test_brain_status_text_with_branches(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import BrainStatusResult
        result = BrainStatusResult(status={
            "updated_utc": "2026-01-01", "branch_count": 1,
            "branches": [{"branch": "tech", "count": 42, "last_ts": "2026-01-01", "last_summary": "stuff"}],
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_brain_status(as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "branch=tech" in out
        assert "count=42" in out


# ===========================================================================
# Status, log, ingest, route commands
# ===========================================================================

class TestStatusCommand:
    """Tests for cmd_status."""

    def test_status_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import StatusResult
        result = StatusResult(
            profile="personal", primary_runtime="python3.12",
            secondary_runtime="ollama", security_strictness="high",
            operation_mode="hybrid", cloud_burst_enabled=True, events=[],
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "profile=personal" in out
        assert "cloud_burst_enabled=True" in out

    def test_status_with_events(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import StatusResult
        event = MagicMock()
        event.ts = "2026-02-25T10:00:00"
        event.event_type = "startup"
        event.message = "Engine started"
        result = StatusResult(events=[event])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "startup" in out
        assert "Engine started" in out

    def test_status_no_events(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import StatusResult
        result = StatusResult(events=[])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "- none" in out


class TestLogCommand:
    """Tests for cmd_log."""

    def test_log_event(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import LogResult
        result = LogResult(ts="2026-02-25T10:00:00", event_type="test", message="hello")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_log(event_type="test", message="hello")
        assert rc == 0
        out = capsys.readouterr().out
        assert "test" in out
        assert "hello" in out


class TestIngestCommand:
    """Tests for cmd_ingest."""

    def test_ingest_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import IngestResult
        result = IngestResult(record_id="rec-123", source="user", kind="semantic", task_id="t1")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ingest(source="user", kind="semantic", task_id="t1", content="Test content")
        assert rc == 0
        out = capsys.readouterr().out
        assert "id=rec-123" in out


class TestRouteCommand:
    """Tests for cmd_route."""

    def test_route_low_easy(self, capsys, monkeypatch):
        from jarvis_engine.commands.task_commands import RouteResult
        result = RouteResult(provider="ollama", reason="low risk local model")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_route(risk="low", complexity="easy")
        assert rc == 0
        out = capsys.readouterr().out
        assert "provider=ollama" in out


# ===========================================================================
# Mission commands
# ===========================================================================

class TestMissionCreate:
    """Tests for cmd_mission_create."""

    def test_create_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionCreateResult
        result = MissionCreateResult(
            mission={"mission_id": "m-1", "topic": "Python async", "sources": ["google", "reddit"]},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_create(topic="Python async", objective="", sources=["google", "reddit"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "mission_id=m-1" in out
        assert "learning_mission_created=true" in out

    def test_create_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionCreateResult
        result = MissionCreateResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_create(topic="", objective="", sources=[])
        assert rc == 2


class TestMissionStatus:
    """Tests for cmd_mission_status."""

    def test_status_empty(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionStatusResult
        result = MissionStatusResult(missions=[], total_count=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_status(last=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "learning_missions=none" in out

    def test_status_with_missions(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionStatusResult
        missions = [
            {"mission_id": "m1", "status": "completed", "topic": "AI safety",
             "verified_findings": 3, "updated_utc": "2026-01-01"},
        ]
        result = MissionStatusResult(missions=missions, total_count=1)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_status(last=5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "mission_id=m1" in out
        assert "AI safety" in out


class TestMissionRun:
    """Tests for cmd_mission_run."""

    def test_run_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionRunResult
        result = MissionRunResult(
            report={"mission_id": "m1", "candidate_count": 10, "verified_count": 3,
                    "verified_findings": [
                        {"statement": "AI can reason", "source_domains": ["arxiv.org"]},
                    ]},
            return_code=0,
            ingested_record_id="rec-42",
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_run(mission_id="m1", max_results=8, max_pages=12, auto_ingest=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "learning_mission_completed=true" in out
        assert "verified_count=3" in out
        assert "mission_ingested_record_id=rec-42" in out

    def test_run_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import MissionRunResult
        result = MissionRunResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_mission_run(mission_id="bad", max_results=8, max_pages=12, auto_ingest=True)
        assert rc == 2


# ===========================================================================
# Ops commands (brief, sync, export-actions, autopilot)
# ===========================================================================

class TestOpsBrief:
    """Tests for cmd_ops_brief."""

    def test_ops_brief_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import OpsBriefResult
        result = OpsBriefResult(brief="Good morning, Conner. Here's your brief.", saved_path="")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ops_brief(snapshot_path=Path("/tmp/snap.json"), output_path=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Good morning" in out

    def test_ops_brief_with_save(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import OpsBriefResult
        result = OpsBriefResult(brief="Brief.", saved_path="/tmp/brief.txt")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ops_brief(snapshot_path=Path("/tmp/snap.json"),
                                     output_path=Path("/tmp/brief.txt"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "brief_saved=" in out


class TestOpsExportActions:
    """Tests for cmd_ops_export_actions."""

    def test_export_actions(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import OpsExportActionsResult
        result = OpsExportActionsResult(actions_path="/tmp/actions.json", action_count=3)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ops_export_actions(snapshot_path=Path("/tmp/snap.json"),
                                              actions_path=Path("/tmp/actions.json"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "action_count=3" in out


class TestOpsSync:
    """Tests for cmd_ops_sync."""

    def test_ops_sync_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import OpsSyncResult
        summary = MagicMock()
        summary.snapshot_path = "/tmp/snap.json"
        summary.tasks = 5
        summary.calendar_events = 2
        summary.emails = 10
        summary.bills = 1
        summary.subscriptions = 3
        summary.medications = 0
        summary.school_items = 0
        summary.family_items = 1
        summary.projects = 2
        summary.connectors_ready = 2
        summary.connectors_pending = 0
        summary.connector_prompts = 0
        result = OpsSyncResult(summary=summary)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ops_sync(output_path=Path("/tmp/snap.json"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "tasks=5" in out
        assert "emails=10" in out

    def test_ops_sync_fail(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import OpsSyncResult
        result = OpsSyncResult(summary=None)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_ops_sync(output_path=Path("/tmp/snap.json"))
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out


class TestAutomationRun:
    """Tests for cmd_automation_run."""

    def test_automation_run_basic(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import AutomationRunResult
        outcome = MagicMock()
        outcome.title = "Send email"
        outcome.allowed = True
        outcome.executed = True
        outcome.return_code = 0
        outcome.reason = "ok"
        outcome.stderr = ""
        result = AutomationRunResult(outcomes=[outcome])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_automation_run(actions_path=Path("/tmp/actions.json"),
                                          approve_privileged=False, execute=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Send email" in out
        assert "allowed=True" in out

    def test_automation_run_with_stderr(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import AutomationRunResult
        outcome = MagicMock()
        outcome.title = "Failing action"
        outcome.allowed = False
        outcome.executed = False
        outcome.return_code = 1
        outcome.reason = "denied"
        outcome.stderr = "Permission denied"
        result = AutomationRunResult(outcomes=[outcome])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_automation_run(actions_path=Path("/tmp/actions.json"),
                                          approve_privileged=False, execute=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Permission denied" in out


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

    def test_web_research_success(self, capsys, monkeypatch):
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
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_web_research(query="python asyncio", max_results=8, max_pages=6, auto_ingest=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "web_research" in out
        assert "scanned_url_count=4" in out
        assert "auto_ingest_record_id=rec-99" in out

    def test_web_research_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.task_commands import WebResearchResult
        result = WebResearchResult(return_code=2, report={})
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_web_research(query="something", max_results=8, max_pages=6, auto_ingest=False)
        assert rc == 2


# ===========================================================================
# Weather, open-web, migrate-memory
# ===========================================================================

class TestWeather:
    """Tests for cmd_weather."""

    def test_weather_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import WeatherResult
        result = WeatherResult(
            return_code=0, location="Austin, TX",
            current={"temp_F": "75", "temp_C": "24", "FeelsLikeF": "73", "humidity": "50"},
            description="Partly cloudy",
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_weather(location="Austin, TX")
        assert rc == 0
        out = capsys.readouterr().out
        assert "weather_report" in out
        assert "temperature_f=75" in out
        assert "Partly cloudy" in out

    def test_weather_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import WeatherResult
        result = WeatherResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_weather(location="Nonexistent Place")
        assert rc == 2


class TestOpenWeb:
    """Tests for cmd_open_web."""

    def test_open_web_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import OpenWebResult
        result = OpenWebResult(return_code=0, opened_url="https://example.com")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_open_web(url="https://example.com")
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened_url=https://example.com" in out

    def test_open_web_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import OpenWebResult
        result = OpenWebResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_open_web(url="")
        assert rc == 2


class TestMigrateMemory:
    """Tests for cmd_migrate_memory."""

    def test_migrate_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import MigrateMemoryResult
        result = MigrateMemoryResult(
            summary={"totals": {"inserted": 100, "skipped": 5, "errors": 0}, "db_path": "/tmp/mem.db"},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_migrate_memory()
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_migration_complete" in out
        assert "total_inserted=100" in out

    def test_migrate_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import MigrateMemoryResult
        result = MigrateMemoryResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_migrate_memory()
        assert rc == 2


# ===========================================================================
# Connector commands
# ===========================================================================

class TestConnectStatus:
    """Tests for cmd_connect_status."""

    def test_connect_status_all_ready(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectStatusResult
        cs = MagicMock()
        cs.connector_id = "email"
        cs.ready = True
        cs.permission_granted = True
        cs.configured = True
        cs.message = "ready"
        result = ConnectStatusResult(statuses=[cs], prompts=[], ready=1, pending=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "ready=1" in out
        assert "id=email" in out

    def test_connect_status_with_prompts(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectStatusResult
        result = ConnectStatusResult(
            statuses=[], prompts=[{"connector_id": "cal", "option_voice": "setup cal", "option_tap_url": "http://cal"}],
            ready=0, pending=1,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "connector_prompts_begin" in out


class TestConnectGrant:
    """Tests for cmd_connect_grant."""

    def test_grant_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectGrantResult
        result = ConnectGrantResult(
            granted={"scopes": ["read", "write"], "granted_utc": "2026-01-01"},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_grant(connector_id="email", scopes=["read", "write"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "granted=true" in out
        assert "read,write" in out

    def test_grant_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectGrantResult
        result = ConnectGrantResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_grant(connector_id="bad", scopes=[])
        assert rc == 2


class TestConnectBootstrap:
    """Tests for cmd_connect_bootstrap."""

    def test_bootstrap_ready(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectBootstrapResult
        result = ConnectBootstrapResult(prompts=[], ready=True)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_bootstrap(auto_open=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "connectors_ready=true" in out

    def test_bootstrap_not_ready(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import ConnectBootstrapResult
        prompts = [{"connector_id": "email", "option_voice": "Setup email", "option_tap_url": "http://setup"}]
        result = ConnectBootstrapResult(prompts=prompts, ready=False)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_connect_bootstrap(auto_open=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "connectors_ready=false" in out
        assert "connector_prompt" in out


# ===========================================================================
# Phone action commands
# ===========================================================================

class TestPhoneAction:
    """Tests for cmd_phone_action."""

    def test_phone_action_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import PhoneActionResult
        record = MagicMock()
        record.action = "send_sms"
        record.number = "+1234567890"
        record.message = "Hello"
        result = PhoneActionResult(record=record, return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_phone_action(
            action="send_sms", number="+1234567890", message="Hello",
            queue_path=Path("/tmp/queue.jsonl"), queue_action=True,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "action=send_sms" in out

    def test_phone_action_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.security_commands import PhoneActionResult
        result = PhoneActionResult(return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_phone_action(
            action="block_number", number="", message="",
            queue_path=Path("/tmp/queue.jsonl"), queue_action=False,
        )
        assert rc == 2


# ===========================================================================
# Growth commands (eval, report, audit, intelligence dashboard)
# ===========================================================================

class TestGrowthEval:
    """Tests for cmd_growth_eval."""

    def test_eval_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import GrowthEvalResult
        task_result = MagicMock()
        task_result.task_id = "t1"
        task_result.coverage = 0.85
        task_result.matched = 3
        task_result.total = 4
        task_result.response_sha256 = "sha256abc"
        run = MagicMock()
        run.model = "gemma3:4b"
        run.score_pct = 82.5
        run.avg_tps = 45.2
        run.avg_latency_s = 1.1
        run.results = [task_result]
        result = GrowthEvalResult(run=run)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_growth_eval(
            model="gemma3:4b", endpoint="http://127.0.0.1:11434",
            tasks_path=Path("/tmp/tasks.json"), history_path=Path("/tmp/hist.jsonl"),
            num_predict=256, temperature=0.0, think=None, accept_thinking=False, timeout_s=120,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "growth_eval_completed=true" in out
        assert "score_pct=82.5" in out

    def test_eval_failure(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import GrowthEvalResult
        result = GrowthEvalResult(run=None)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_growth_eval(
            model="bad", endpoint="x", tasks_path=Path("x"), history_path=Path("x"),
            num_predict=256, temperature=0.0, think=None, accept_thinking=False, timeout_s=120,
        )
        assert rc == 2


class TestGrowthReport:
    """Tests for cmd_growth_report."""

    def test_report(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import GrowthReportResult
        result = GrowthReportResult(summary={
            "runs": 10, "latest_model": "gemma3:4b", "latest_score_pct": 80.0,
            "delta_vs_prev_pct": 2.5, "window_avg_pct": 78.0, "latest_ts": "2026-01-01",
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_growth_report(history_path=Path("/tmp/hist.jsonl"), last=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "runs=10" in out
        assert "latest_score_pct=80.0" in out


class TestGrowthAudit:
    """Tests for cmd_growth_audit."""

    def test_audit(self, capsys, monkeypatch):
        from jarvis_engine.commands.ops_commands import GrowthAuditResult
        result = GrowthAuditResult(run={
            "model": "gemma3:4b", "ts": "2026-01-01", "score_pct": 80.0,
            "tasks": 5, "prev_run_sha256": "prev", "run_sha256": "cur",
            "results": [
                {"task_id": "t1", "matched_tokens": ["a", "b"], "required_tokens": ["a", "b", "c"],
                 "prompt_sha256": "p", "response_sha256": "r",
                 "response_source": "live", "response": "answer"},
            ],
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_growth_audit(history_path=Path("/tmp/hist.jsonl"), run_index=-1)
        assert rc == 0
        out = capsys.readouterr().out
        assert "growth_audit" in out
        assert "task=t1" in out


class TestIntelligenceDashboard:
    """Tests for cmd_intelligence_dashboard."""

    def test_dashboard_json(self, capsys, monkeypatch, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {
            "generated_utc": "2026-01-01",
            "jarvis": {"score_pct": 80.0, "delta_vs_prev_pct": 1.0, "window_avg_pct": 78.0, "latest_model": "gemma3:4b"},
            "methodology": {"history_runs": 10, "slope_score_pct_per_run": 0.5, "avg_days_per_run": 3.0},
            "ranking": [{"name": "GPT-4", "score_pct": 90.0}],
            "etas": [],
            "achievements": {"new": []},
        }
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        rc = main_mod.cmd_intelligence_dashboard(last_runs=20, output_path="", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["jarvis"]["score_pct"] == 80.0

    def test_dashboard_text(self, capsys, monkeypatch, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {
            "generated_utc": "2026-01-01",
            "jarvis": {"score_pct": 80.0, "delta_vs_prev_pct": 1.0, "window_avg_pct": 78.0, "latest_model": "gemma3:4b"},
            "methodology": {"history_runs": 10, "slope_score_pct_per_run": 0.5, "avg_days_per_run": 3.0},
            "ranking": [{"name": "GPT-4", "score_pct": 90.0}],
            "etas": [{"target_name": "GPT-4", "target_score_pct": 90.0, "eta": {"status": "on_track", "runs": 20, "days": 60}}],
            "achievements": {"new": [{"label": "First run completed"}]},
        }
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        rc = main_mod.cmd_intelligence_dashboard(last_runs=20, output_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "intelligence_dashboard" in out
        assert "jarvis_score_pct=80.0" in out
        assert "rank_1=GPT-4:90.0" in out
        assert "achievement_unlocked=First run completed" in out


# ===========================================================================
# Voice commands
# ===========================================================================

class TestVoiceList:
    """Tests for cmd_voice_list."""

    def test_voice_list_with_voices(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceListResult
        result = VoiceListResult(windows_voices=["David", "Zira"], edge_voices=["en-GB-RyanNeural"])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_list()
        assert rc == 0
        out = capsys.readouterr().out
        assert "David" in out
        assert "en-GB-RyanNeural" in out

    def test_voice_list_empty(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceListResult
        result = VoiceListResult(windows_voices=[], edge_voices=[])
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_list()
        assert rc == 1


class TestVoiceSay:
    """Tests for cmd_voice_say."""

    def test_voice_say(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceSayResult
        result = VoiceSayResult(voice_name="David", output_wav="", message="Spoken.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_say(text="Hello", profile="jarvis_like",
                                     voice_pattern="", output_wav="", rate=-1)
        assert rc == 0
        out = capsys.readouterr().out
        assert "voice=David" in out

    def test_voice_say_with_wav(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceSayResult
        result = VoiceSayResult(voice_name="Zira", output_wav="/tmp/out.wav", message="Saved.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_say(text="Test", profile="default",
                                     voice_pattern="", output_wav="/tmp/out.wav", rate=150)
        assert rc == 0
        out = capsys.readouterr().out
        assert "wav=/tmp/out.wav" in out


class TestVoiceEnroll:
    """Tests for cmd_voice_enroll."""

    def test_enroll_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceEnrollResult
        result = VoiceEnrollResult(user_id="conner", profile_path="/tmp/profile",
                                   samples=3, message="Enrolled successfully.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_enroll(user_id="conner", wav_path="/tmp/voice.wav", replace=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "user_id=conner" in out
        assert "samples=3" in out

    def test_enroll_error(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceEnrollResult
        result = VoiceEnrollResult(message="error: WAV file not found.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_enroll(user_id="conner", wav_path="/bad/path.wav", replace=False)
        assert rc == 2


class TestVoiceVerify:
    """Tests for cmd_voice_verify."""

    def test_verify_matched(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(user_id="conner", score=0.95, threshold=0.82,
                                   matched=True, message="Match confirmed.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_verify(user_id="conner", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 0
        out = capsys.readouterr().out
        assert "matched=True" in out

    def test_verify_not_matched(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(user_id="conner", score=0.5, threshold=0.82,
                                   matched=False, message="No match.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_verify(user_id="conner", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 2

    def test_verify_error(self, capsys, monkeypatch):
        from jarvis_engine.commands.voice_commands import VoiceVerifyResult
        result = VoiceVerifyResult(message="error: No enrolled profile.")
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_voice_verify(user_id="nobody", wav_path="/tmp/v.wav", threshold=0.82)
        assert rc == 2


# ===========================================================================
# Run task
# ===========================================================================

class TestRunTask:
    """Tests for cmd_run_task."""

    def test_run_task_success(self, capsys, monkeypatch):
        from jarvis_engine.commands.task_commands import RunTaskResult
        result = RunTaskResult(
            allowed=True, provider="ollama", plan="Generate image", reason="approved",
            output_path="/tmp/output.png", output_text="Generated!", return_code=0,
            auto_ingest_record_id="rec-50",
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_run_task(
            task_type="image", prompt="A sunset", execute=True,
            approve_privileged=False, model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434", quality_profile="max_quality",
            output_path="/tmp/output.png",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "allowed=True" in out
        assert "output_path=/tmp/output.png" in out
        assert "auto_ingest_record_id=rec-50" in out

    def test_run_task_denied(self, capsys, monkeypatch):
        from jarvis_engine.commands.task_commands import RunTaskResult
        result = RunTaskResult(allowed=False, reason="privileged task denied", return_code=2)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_run_task(
            task_type="video", prompt="test", execute=False,
            approve_privileged=False, model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434", quality_profile="max_quality",
            output_path=None,
        )
        assert rc == 2


# ===========================================================================
# Helper functions
# ===========================================================================

class TestHelperFunctions:
    """Tests for private helper functions in main.py."""

    def test_extract_first_phone_number(self):
        assert main_mod._extract_first_phone_number("Call +14155551234 please") == "+14155551234"
        assert main_mod._extract_first_phone_number("no number here") == ""
        assert main_mod._extract_first_phone_number("dial 555-123-4567") == "555-123-4567"
        # Truncation at 256 chars
        long_text = "x" * 300 + "+14155551234"
        assert main_mod._extract_first_phone_number(long_text) == ""

    def test_extract_weather_location(self):
        assert main_mod._extract_weather_location("weather in Austin, TX") == "Austin, TX"
        assert main_mod._extract_weather_location("weather for New York") == "New York"
        assert main_mod._extract_weather_location("forecast at Chicago") == "Chicago"
        # Noise words stripped
        loc = main_mod._extract_weather_location("weather today")
        assert "today" not in loc.lower().split()

    def test_extract_web_query(self):
        assert "python" in main_mod._extract_web_query("search the web for python asyncio")
        assert "ml" in main_mod._extract_web_query("research ML frameworks")
        assert "rust" in main_mod._extract_web_query("look up rust programming")
        assert "react" in main_mod._extract_web_query("find on the web react hooks")

    def test_extract_first_url(self):
        assert main_mod._extract_first_url("go to https://example.com") == "https://example.com"
        assert main_mod._extract_first_url("visit www.google.com") == "https://www.google.com"
        assert main_mod._extract_first_url("no url here") == ""
        # Long text truncation
        long_text = "x" * 1300 + "https://late.com"
        assert main_mod._extract_first_url(long_text) == ""

    def test_is_read_only_voice_request(self):
        assert main_mod._is_read_only_voice_request(
            "runtime status", execute=False, approve_privileged=False
        ) is True
        assert main_mod._is_read_only_voice_request(
            "pause daemon", execute=False, approve_privileged=False
        ) is False
        # execute flag forces non-read-only
        assert main_mod._is_read_only_voice_request(
            "runtime status", execute=True, approve_privileged=False
        ) is False
        # Bare wake words treated as read-only
        assert main_mod._is_read_only_voice_request(
            "jarvis", execute=False, approve_privileged=False
        ) is True
        assert main_mod._is_read_only_voice_request(
            "hey jarvis", execute=False, approve_privileged=False
        ) is True
        # Conversational fallthrough is read-only
        assert main_mod._is_read_only_voice_request(
            "what is the meaning of life", execute=False, approve_privileged=False
        ) is True

    def test_sanitize_memory_content_truncation(self):
        long_content = "a" * 200_000
        cleaned = main_mod._sanitize_memory_content(long_content)
        assert len(cleaned) <= 2000

    def test_sanitize_memory_content_json_redaction(self):
        content = '{"api_key": "sk-secret123", "data": "normal"}'
        cleaned = main_mod._sanitize_memory_content(content)
        assert "sk-secret123" not in cleaned
        assert "[redacted]" in cleaned

    def test_sanitize_memory_content_bearer_redaction(self):
        content = "Authorization: bearer sk-my-token-abc"
        cleaned = main_mod._sanitize_memory_content(content)
        assert "sk-my-token-abc" not in cleaned

    def test_valid_sources_and_kinds(self):
        assert "user" in main_mod._VALID_SOURCES
        assert "claude" in main_mod._VALID_SOURCES
        assert "episodic" in main_mod._VALID_KINDS
        assert "semantic" in main_mod._VALID_KINDS
        assert "procedural" in main_mod._VALID_KINDS

    def test_load_auto_ingest_hashes_missing_file(self, tmp_path):
        result = main_mod._load_auto_ingest_hashes(tmp_path / "nonexistent.json")
        assert result == []

    def test_load_auto_ingest_hashes_corrupted(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        result = main_mod._load_auto_ingest_hashes(path)
        assert result == []

    def test_load_auto_ingest_hashes_valid(self, tmp_path):
        path = tmp_path / "dedupe.json"
        path.write_text(json.dumps({"hashes": ["abc", "def"]}), encoding="utf-8")
        result = main_mod._load_auto_ingest_hashes(path)
        assert result == ["abc", "def"]

    def test_load_auto_ingest_hashes_wrong_type(self, tmp_path):
        path = tmp_path / "dedupe.json"
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        result = main_mod._load_auto_ingest_hashes(path)
        assert result == []


class TestAutoIngestMemory:
    """Tests for _auto_ingest_memory."""

    def test_auto_ingest_disabled_by_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_AUTO_INGEST_DISABLE", "1")
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        result = main_mod._auto_ingest_memory(
            source="user", kind="semantic", task_id="test", content="Test content",
        )
        assert result == ""

    def test_auto_ingest_invalid_source(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JARVIS_AUTO_INGEST_DISABLE", raising=False)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        result = main_mod._auto_ingest_memory(
            source="invalid_source", kind="semantic", task_id="test", content="Test",
        )
        assert result == ""

    def test_auto_ingest_invalid_kind(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JARVIS_AUTO_INGEST_DISABLE", raising=False)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        result = main_mod._auto_ingest_memory(
            source="user", kind="bogus", task_id="test", content="Test",
        )
        assert result == ""

    def test_auto_ingest_empty_content(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JARVIS_AUTO_INGEST_DISABLE", raising=False)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        result = main_mod._auto_ingest_memory(
            source="user", kind="semantic", task_id="test", content="",
        )
        assert result == ""


class TestGamingProcessHelpers:
    """Tests for gaming mode helper functions."""

    def test_read_gaming_mode_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        state = main_mod._read_gaming_mode_state()
        assert state["enabled"] is False

    def test_read_gaming_mode_corrupted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        path = tmp_path / ".planning" / "runtime" / "gaming_mode.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("corrupt json!", encoding="utf-8")
        state = main_mod._read_gaming_mode_state()
        assert state["enabled"] is False

    def test_load_gaming_processes_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        processes = main_mod._load_gaming_processes()
        assert len(processes) > 0
        assert any("FortniteClient" in p for p in processes)

    def test_load_gaming_processes_from_env(self, monkeypatch):
        monkeypatch.setenv("JARVIS_GAMING_PROCESSES", "game1.exe,game2.exe")
        processes = main_mod._load_gaming_processes()
        assert processes == ["game1.exe", "game2.exe"]

    def test_load_gaming_processes_from_file_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"processes": ["custom.exe"]}), encoding="utf-8")
        processes = main_mod._load_gaming_processes()
        assert processes == ["custom.exe"]

    def test_load_gaming_processes_from_file_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(["listgame.exe"]), encoding="utf-8")
        processes = main_mod._load_gaming_processes()
        assert processes == ["listgame.exe"]

    def test_load_gaming_processes_empty_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.delenv("JARVIS_GAMING_PROCESSES", raising=False)
        path = tmp_path / ".planning" / "gaming_processes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"processes": []}), encoding="utf-8")
        processes = main_mod._load_gaming_processes()
        assert len(processes) == len(main_mod.DEFAULT_GAMING_PROCESSES)


# ===========================================================================
# Memory snapshot edge cases
# ===========================================================================

class TestMemorySnapshotEdgeCases:
    """Tests for cmd_memory_snapshot."""

    def test_snapshot_no_action(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(created=False, verified=False)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path=None, note="")
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_snapshot_verify_ok(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            verified=True, ok=True, reason="Hashes match.",
            expected_sha256="abc", actual_sha256="abc",
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path="/tmp/snap.zip", note="")
        assert rc == 0
        out = capsys.readouterr().out
        assert "ok=True" in out

    def test_snapshot_verify_fail(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            verified=True, ok=False, reason="Hash mismatch.",
            expected_sha256="abc", actual_sha256="xyz",
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_memory_snapshot(create=False, verify_path="/tmp/snap.zip", note="")
        assert rc == 2

    def test_snapshot_create(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import MemorySnapshotResult
        result = MemorySnapshotResult(
            created=True, snapshot_path="/tmp/snap.zip",
            metadata_path="/tmp/snap.meta.json", signature_path="/tmp/snap.sig",
            sha256="abc123", file_count=10,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_memory_snapshot(create=True, verify_path=None, note="test")
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_snapshot_created=true" in out
        assert "file_count=10" in out


# ===========================================================================
# Memory maintenance
# ===========================================================================

class TestMemoryMaintenanceEdgeCases:
    """Tests for cmd_memory_maintenance via mock bus."""

    def test_maintenance_with_details(self, capsys, monkeypatch):
        from jarvis_engine.commands.memory_commands import MemoryMaintenanceResult
        result = MemoryMaintenanceResult(report={
            "status": "ok", "report_path": "/tmp/report.json",
            "compact": {"compacted": True, "total_records": 2000, "kept_records": 1800},
            "regression": {"status": "healthy", "duplicate_ratio": 0.01, "unresolved_conflicts": 0},
            "snapshot": {"path": "/tmp/snap.zip"},
        })
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_memory_maintenance(keep_recent=1800, snapshot_note="nightly")
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_maintenance" in out
        assert "compacted=True" in out
        assert "duplicate_ratio=0.01" in out


# ===========================================================================
# Self-heal with mock bus
# ===========================================================================

class TestSelfHealMocked:
    """Tests for cmd_self_heal via mocked bus."""

    def test_self_heal_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import SelfHealResult
        report = {"status": "ok", "actions": ["checked_db", "verified_config"],
                  "regression": {"status": "healthy", "duplicate_ratio": 0.0, "unresolved_conflicts": 0},
                  "report_path": "/tmp/heal.json"}
        result = SelfHealResult(report=report, return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_self_heal(force_maintenance=False, keep_recent=1800,
                                     snapshot_note="test", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "ok"

    def test_self_heal_text(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import SelfHealResult
        report = {"status": "repaired", "actions": ["fixed_index"],
                  "regression": {"status": "ok", "duplicate_ratio": 0.0, "unresolved_conflicts": 0},
                  "report_path": "/tmp/heal.json"}
        result = SelfHealResult(report=report, return_code=0)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        rc = main_mod.cmd_self_heal(force_maintenance=False, keep_recent=500,
                                     snapshot_note="test", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "self_heal" in out
        assert "action=fixed_index" in out


# ===========================================================================
# Mobile desktop sync mocked
# ===========================================================================

class TestMobileDesktopSyncMocked:
    """Tests for cmd_mobile_desktop_sync via mocked bus."""

    def test_sync_json(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import MobileDesktopSyncResult
        result = MobileDesktopSyncResult(
            report={"sync_ok": True, "checks": [{"name": "config", "ok": True}]},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "_auto_ingest_memory", lambda **kw: "")
        rc = main_mod.cmd_mobile_desktop_sync(auto_ingest=False, as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["sync_ok"] is True

    def test_sync_text(self, capsys, monkeypatch):
        from jarvis_engine.commands.system_commands import MobileDesktopSyncResult
        result = MobileDesktopSyncResult(
            report={"sync_ok": True, "report_path": "/tmp/sync.json",
                    "checks": [{"name": "config", "ok": True}]},
            return_code=0,
        )
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "_auto_ingest_memory", lambda **kw: "")
        rc = main_mod.cmd_mobile_desktop_sync(auto_ingest=False, as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "mobile_desktop_sync" in out
        assert "check_config=True" in out


# ===========================================================================
# Serve-mobile edge cases
# ===========================================================================

class TestServeMobileEdgeCases:
    """Tests for cmd_serve_mobile edge cases."""

    def test_config_file_not_found(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
            config_file="/nonexistent/config.json",
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "config file not found" in out

    def test_config_file_invalid_json(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        bad_cfg = tmp_path / "bad.json"
        bad_cfg.write_text("not json", encoding="utf-8")
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
            config_file=str(bad_cfg),
        )
        assert rc == 2

    def test_missing_token_only(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.setenv("JARVIS_MOBILE_SIGNING_KEY", "key123")
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "missing mobile token" in out

    def test_missing_signing_key_only(self, capsys, monkeypatch):
        monkeypatch.setenv("JARVIS_MOBILE_TOKEN", "tok123")
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "missing signing key" in out


# ===========================================================================
# Intelligence dashboard output path validation
# ===========================================================================

class TestIntelligenceDashboardOutputPath:
    """Tests for intelligence dashboard output path restrictions."""

    def test_output_path_outside_repo_json(self, capsys, monkeypatch, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {"jarvis": {}, "methodology": {}, "ranking": [], "etas": [], "achievements": {}}
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        # Use a path clearly outside repo root
        rc = main_mod.cmd_intelligence_dashboard(
            last_runs=5, output_path="/tmp/totally/outside/dashboard.json", as_json=True,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_output_path_inside_repo_json(self, capsys, monkeypatch, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {"jarvis": {}, "methodology": {}, "ranking": [], "etas": [], "achievements": {}}
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = _make_bus_mock(result)
        monkeypatch.setattr(main_mod, "_get_bus", lambda: bus)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        out_path = str(tmp_path / "output" / "dash.json")
        rc = main_mod.cmd_intelligence_dashboard(last_runs=5, output_path=out_path, as_json=True)
        assert rc == 0
        assert (tmp_path / "output" / "dash.json").exists()


# ===========================================================================
# Daemon self-test cycle integration
# ===========================================================================

class TestDaemonSelfTest:
    """Tests for adversarial self-test integration within daemon run loop."""

    def _run_daemon_impl(self, tmp_path, monkeypatch, *,
                         self_test_every_cycles=1, max_cycles=1):
        """Helper: run _cmd_daemon_run_impl with heavy mocking."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", lambda **kw: 0)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
        # Ensure runtime dir exists for self_test_history.jsonl
        (tmp_path / ".planning" / "runtime").mkdir(parents=True, exist_ok=True)
        return main_mod._cmd_daemon_run_impl(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=max_cycles,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
            sync_every_cycles=0,
            self_heal_every_cycles=0,
            self_test_every_cycles=self_test_every_cycles,
        )

    def test_self_test_runs_at_correct_cycle(self, capsys, monkeypatch, tmp_path):
        """Verify self-test fires when cycles % self_test_every_cycles == 0."""
        mock_tester = MagicMock()
        mock_tester.run_memory_quiz.return_value = {"average_score": 0.92, "tasks_run": 4}
        mock_tester.check_regression.return_value = {"regression_detected": False}

        mock_bus = MagicMock()
        mock_bus._engine = MagicMock()
        mock_bus._embed_service = MagicMock()
        monkeypatch.setattr(main_mod, "_get_bus", lambda: mock_bus)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest",
                    return_value=mock_tester) as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0
        mock_cls.assert_called_once_with(mock_bus._engine, mock_bus._embed_service,
                                          score_threshold=0.5)
        mock_tester.run_memory_quiz.assert_called_once()
        mock_tester.save_quiz_result.assert_called_once()
        mock_tester.check_regression.assert_called_once()
        out = capsys.readouterr().out
        assert "self_test_score=0.9200" in out
        assert "self_test_tasks=4" in out

    def test_self_test_skipped_when_disabled(self, capsys, monkeypatch, tmp_path):
        """Verify no self-test activity when self_test_every_cycles=0."""
        mock_bus = MagicMock()
        mock_bus._engine = MagicMock()
        mock_bus._embed_service = MagicMock()
        monkeypatch.setattr(main_mod, "_get_bus", lambda: mock_bus)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest") as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=0, max_cycles=1)

        assert rc == 0
        mock_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "self_test_score" not in out
        assert "self_test_skipped" not in out

    def test_self_test_handles_missing_engine(self, capsys, monkeypatch, tmp_path):
        """Verify 'skipped' message when engine or embed_svc is None on bus."""
        mock_bus = MagicMock()
        mock_bus._engine = None
        mock_bus._embed_service = None
        monkeypatch.setattr(main_mod, "_get_bus", lambda: mock_bus)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest") as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0
        mock_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "self_test_skipped=engine_not_initialized" in out

    def test_self_test_handles_error(self, capsys, monkeypatch, tmp_path):
        """Verify error is caught and printed, daemon continues running."""
        mock_bus = MagicMock()
        mock_bus._engine = MagicMock()
        mock_bus._embed_service = MagicMock()
        monkeypatch.setattr(main_mod, "_get_bus", lambda: mock_bus)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest",
                    side_effect=RuntimeError("quiz DB corrupt")):
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0  # daemon did NOT crash
        out = capsys.readouterr().out
        assert "self_test_error=" in out
        assert "quiz DB corrupt" in out

    def test_self_test_every_cycles_in_command(self):
        """Verify DaemonRunCommand dataclass has self_test_every_cycles with default 20."""
        from jarvis_engine.commands.system_commands import DaemonRunCommand
        cmd = DaemonRunCommand()
        assert hasattr(cmd, "self_test_every_cycles")
        assert cmd.self_test_every_cycles == 20


# ===========================================================================
# Conversation history buffer tests
# ===========================================================================


class TestConversationHistory:
    """Tests for _conversation_history, _add_to_history, _get_history_messages."""

    def setup_method(self):
        """Reset module-level conversation history before each test."""
        main_mod._conversation_history.clear()

    def test_add_to_history_appends_message(self):
        """_add_to_history appends a dict with role and content."""
        main_mod._add_to_history("user", "Hello Jarvis")
        hist = main_mod._get_history_messages()
        assert len(hist) == 1
        assert hist[0] == {"role": "user", "content": "Hello Jarvis"}

    def test_add_to_history_multiple_messages(self):
        """Multiple calls build up the history list."""
        main_mod._add_to_history("user", "What is the weather?")
        main_mod._add_to_history("assistant", "It is sunny.")
        hist = main_mod._get_history_messages()
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[1]["role"] == "assistant"

    def test_history_caps_at_max_turns_times_2(self):
        """History is capped at _CONVERSATION_MAX_TURNS * 2 entries."""
        max_entries = main_mod._CONVERSATION_MAX_TURNS * 2
        # Add more than the cap
        for i in range(max_entries + 6):
            role = "user" if i % 2 == 0 else "assistant"
            main_mod._add_to_history(role, f"message {i}")

        hist = main_mod._get_history_messages()
        assert len(hist) == max_entries
        # Oldest messages should have been evicted; latest should be present
        assert hist[-1]["content"] == f"message {max_entries + 5}"

    def test_history_truncates_long_content(self):
        """Content is truncated to 800 characters."""
        long_msg = "x" * 2000
        main_mod._add_to_history("user", long_msg)
        hist = main_mod._get_history_messages()
        assert len(hist[0]["content"]) == 800

    def test_get_history_returns_copy(self):
        """_get_history_messages returns a copy, not the original list."""
        main_mod._add_to_history("user", "test")
        hist = main_mod._get_history_messages()
        hist.clear()
        # Original should be unaffected
        assert len(main_mod._get_history_messages()) == 1

    def test_conversation_max_turns_is_5(self):
        """_CONVERSATION_MAX_TURNS is set to 5."""
        assert main_mod._CONVERSATION_MAX_TURNS == 5


# ===========================================================================
# _MAX_TOKENS_BY_ROUTE tests
# ===========================================================================


class TestMaxTokensByRoute:
    """Tests for _MAX_TOKENS_BY_ROUTE configuration."""

    def test_max_tokens_math_logic(self):
        assert main_mod._MAX_TOKENS_BY_ROUTE["math_logic"] == 1024

    def test_max_tokens_complex(self):
        assert main_mod._MAX_TOKENS_BY_ROUTE["complex"] == 1024

    def test_max_tokens_routine(self):
        assert main_mod._MAX_TOKENS_BY_ROUTE["routine"] == 512

    def test_max_tokens_simple_private(self):
        assert main_mod._MAX_TOKENS_BY_ROUTE["simple_private"] == 384

    def test_max_tokens_unknown_route_returns_none(self):
        """Unknown routes are not in the dict (caller uses .get with default)."""
        assert main_mod._MAX_TOKENS_BY_ROUTE.get("unknown_route") is None


# ===========================================================================
# _build_smart_context tests
# ===========================================================================


class TestBuildSmartContext:
    """Tests for _build_smart_context function."""

    def test_hybrid_search_path_when_engine_available(self, monkeypatch):
        """When bus has _engine and _embed_service, uses hybrid_search."""
        bus = MagicMock()
        bus._engine = MagicMock()
        bus._embed_service = MagicMock()
        bus._embed_service.embed_query.return_value = [0.1, 0.2, 0.3]

        fake_records = [
            {"summary": "User likes hiking on weekends"},
            {"summary": "User takes metformin daily"},
        ]

        with patch("jarvis_engine.main.hybrid_search", create=True) as mock_hs:
            # hybrid_search is imported inside _build_smart_context, so patch the import target
            with patch.dict("sys.modules", {}):
                pass
            # Patch at the location where it's imported inside the function
            with patch("jarvis_engine.memory.search.hybrid_search", return_value=fake_records):
                memory_lines, fact_lines, _cb = main_mod._build_smart_context(bus, "health")

        # Memory lines come from hybrid_search results
        assert "User likes hiking on weekends" in memory_lines
        assert "User takes metformin daily" in memory_lines

    def test_legacy_fallback_when_no_engine(self, monkeypatch):
        """When bus has no _engine, falls back to build_context_packet."""
        bus = MagicMock(spec=[])  # empty spec - no attributes

        fake_packet = {
            "selected": [
                {"summary": "Legacy memory entry 1"},
                {"summary": "Legacy memory entry 2"},
            ]
        }

        monkeypatch.setattr(
            main_mod, "build_context_packet", lambda *a, **kw: fake_packet
        )

        memory_lines, fact_lines, _cb = main_mod._build_smart_context(bus, "anything")
        assert "Legacy memory entry 1" in memory_lines
        assert "Legacy memory entry 2" in memory_lines

    def test_legacy_fallback_when_hybrid_fails(self, monkeypatch):
        """When hybrid_search raises, falls back to build_context_packet."""
        bus = MagicMock()
        bus._engine = MagicMock()
        bus._embed_service = MagicMock()
        bus._embed_service.embed_query.side_effect = RuntimeError("embed failed")

        fake_packet = {
            "selected": [{"summary": "Fallback memory"}]
        }
        monkeypatch.setattr(
            main_mod, "build_context_packet", lambda *a, **kw: fake_packet
        )

        memory_lines, fact_lines, _cb = main_mod._build_smart_context(bus, "test query")
        assert "Fallback memory" in memory_lines

    def test_kg_facts_injected_when_engine_available(self, monkeypatch, tmp_path):
        """KG facts are queried and returned as fact_lines."""
        bus = MagicMock(spec=[])  # No _engine attr initially — force fallback for memory
        # But we need _engine to be not None for the KG section
        bus._engine = MagicMock()
        bus._embed_service = None  # No embed service — hybrid won't run

        # Legacy path returns empty for memory
        monkeypatch.setattr(
            main_mod, "build_context_packet",
            lambda *a, **kw: {"selected": []},
        )

        # Mock the KnowledgeGraph that's constructed inside _build_smart_context
        mock_kg_instance = MagicMock()
        mock_kg_instance.query_relevant_facts.return_value = [
            {"label": "User is allergic to peanuts", "confidence": 0.9},
            {"label": "User prefers window seat", "confidence": 0.7},
        ]

        with patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance):
            memory_lines, fact_lines, _cb = main_mod._build_smart_context(bus, "tell me about allergies")

        assert "User is allergic to peanuts" in fact_lines

    def test_kg_facts_filtered_by_confidence(self, monkeypatch):
        """KG facts with confidence < 0.5 are excluded from fact_lines."""
        bus = MagicMock(spec=[])
        bus._engine = MagicMock()
        bus._embed_service = None

        monkeypatch.setattr(
            main_mod, "build_context_packet",
            lambda *a, **kw: {"selected": []},
        )

        mock_kg_instance = MagicMock()
        mock_kg_instance.query_relevant_facts.return_value = [
            {"label": "High confidence fact", "confidence": 0.9},
            {"label": "Low confidence fact", "confidence": 0.3},
        ]

        with patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance):
            memory_lines, fact_lines, _cb = main_mod._build_smart_context(bus, "some query")

        assert "High confidence fact" in fact_lines
        assert "Low confidence fact" not in fact_lines

    def test_returns_empty_when_everything_fails(self, monkeypatch):
        """Returns ([], []) when both memory and KG queries fail."""
        bus = MagicMock(spec=[])  # No _engine

        monkeypatch.setattr(
            main_mod, "build_context_packet",
            MagicMock(side_effect=RuntimeError("DB broken")),
        )

        memory_lines, fact_lines, cross_branch_lines = main_mod._build_smart_context(bus, "broken query")
        assert memory_lines == []
        assert fact_lines == []
        assert cross_branch_lines == []


# ===========================================================================
# QueryCommand.history field tests
# ===========================================================================


class TestQueryCommandHistory:
    """Tests for the history field on QueryCommand."""

    def test_query_command_has_history_field(self):
        """QueryCommand has a history field defaulting to empty tuple."""
        from jarvis_engine.commands.task_commands import QueryCommand
        cmd = QueryCommand(query="test")
        assert hasattr(cmd, "history")
        assert cmd.history == ()

    def test_query_command_history_accepts_tuples(self):
        """QueryCommand.history can hold conversation turn tuples."""
        from jarvis_engine.commands.task_commands import QueryCommand
        history = (("user", "Hello"), ("assistant", "Hi there"))
        cmd = QueryCommand(query="follow up", history=history)
        assert cmd.history == history
        assert len(cmd.history) == 2

    def test_query_command_is_frozen(self):
        """QueryCommand is a frozen dataclass (immutable)."""
        from jarvis_engine.commands.task_commands import QueryCommand
        cmd = QueryCommand(query="test")
        with pytest.raises(AttributeError):
            cmd.query = "changed"


# ===========================================================================
# QueryHandler with conversation history injection tests
# ===========================================================================


class TestQueryHandlerHistory:
    """Tests for QueryHandler injecting history into LLM messages."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_injects_history_before_query(self):
        """QueryHandler places history messages between system prompt and user query."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = GatewayResponse(
            text="response", model="test-model", provider="test"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="What about my diet?",
            system_prompt="You are Jarvis.",
            history=(
                ("user", "Tell me about my health"),
                ("assistant", "You take metformin daily."),
            ),
        )

        handler.handle(cmd)

        # Inspect the messages passed to gateway.complete
        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages") or call_kwargs[0][0]
        # If passed as positional, it'll be messages=...
        if not isinstance(messages, list):
            messages = call_kwargs.kwargs["messages"]

        # Expected order: system, history user, history assistant, current user
        assert messages[0] == {"role": "system", "content": "You are Jarvis."}
        assert messages[1] == {"role": "user", "content": "Tell me about my health"}
        assert messages[2] == {"role": "assistant", "content": "You take metformin daily."}
        assert messages[3] == {"role": "user", "content": "What about my diet?"}

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_works_without_history(self):
        """QueryHandler works correctly when history is empty (default)."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = GatewayResponse(
            text="answer", model="test-model", provider="test"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="What time is it?",
            system_prompt="You are helpful.",
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        # Only system + user, no history
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "What time is it?"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_filters_invalid_history_roles(self):
        """QueryHandler only injects 'user' and 'assistant' roles from history."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = GatewayResponse(
            text="ok", model="m", provider="p"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="test",
            history=(
                ("user", "valid user msg"),
                ("system", "injected system msg"),  # should be filtered
                ("assistant", "valid assistant msg"),
                ("admin", "injected admin msg"),  # should be filtered
            ),
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        roles = [m["role"] for m in messages]
        assert "admin" not in roles
        # system only appears if cmd.system_prompt was set (it's empty here)
        # so the injected "system" from history should be filtered out
        history_roles = [m["role"] for m in messages if m["content"] not in ("test",)]
        assert "system" not in history_roles

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_handler_skips_empty_content_in_history(self):
        """QueryHandler skips history entries with empty content."""
        from jarvis_engine.commands.task_commands import QueryCommand
        from jarvis_engine.handlers.task_handlers import QueryHandler
        from jarvis_engine.gateway.models import GatewayResponse

        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = GatewayResponse(
            text="ok", model="m", provider="p"
        )

        handler = QueryHandler(gateway=mock_gateway)
        cmd = QueryCommand(
            query="final question",
            history=(
                ("user", "first question"),
                ("assistant", ""),  # empty - should be skipped
                ("user", "second question"),
            ),
        )

        handler.handle(cmd)

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")

        # Should have: first question, second question, final question (no empty assistant)
        contents = [m["content"] for m in messages]
        assert "" not in contents
