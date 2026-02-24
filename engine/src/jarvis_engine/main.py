from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path

from jarvis_engine.automation import AutomationExecutor, load_actions
from jarvis_engine.brain_memory import (
    brain_compact,
    brain_regression_report,
    brain_status,
    build_context_packet,
    ingest_brain_record,
)
from jarvis_engine.config import load_config, repo_root
from jarvis_engine.connectors import (
    build_connector_prompts,
    evaluate_connector_statuses,
    grant_connector_permission,
)
from jarvis_engine.growth_tracker import (
    audit_run,
    append_history,
    load_golden_tasks,
    read_history,
    run_eval,
    summarize_history,
)
from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard
from jarvis_engine.ingest import IngestionPipeline, MemoryKind, SourceType
from jarvis_engine.learning_missions import create_learning_mission, load_missions, run_learning_mission
from jarvis_engine.life_ops import build_daily_brief, export_actions_json, load_snapshot, suggest_actions
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.memory_snapshots import create_signed_snapshot, run_memory_maintenance, verify_signed_snapshot
from jarvis_engine.mobile_api import run_mobile_server
from jarvis_engine.ops_sync import build_live_snapshot
from jarvis_engine.owner_guard import (
    clear_master_password,
    read_owner_guard,
    revoke_mobile_device,
    set_master_password,
    trust_mobile_device,
    verify_master_password,
    write_owner_guard,
)
from jarvis_engine.phone_guard import (
    append_phone_actions,
    build_phone_action,
    build_spam_block_actions,
    detect_spam_candidates,
    load_call_log,
    write_spam_report,
)
from jarvis_engine.persona import compose_persona_reply, load_persona_config, save_persona_config
from jarvis_engine.resilience import run_mobile_desktop_sync, run_self_heal
from jarvis_engine.router import ModelRouter
from jarvis_engine.runtime_control import read_control_state, reset_control_state, write_control_state
from jarvis_engine.task_orchestrator import TaskOrchestrator, TaskRequest
from jarvis_engine.voice import list_edge_voices, list_windows_voices, speak_text
from jarvis_engine.web_research import run_web_research

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
)
from jarvis_engine.commands.voice_commands import (
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)
from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DesktopWidgetCommand,
    GamingModeCommand,
    LogCommand,
    MigrateMemoryCommand,
    MobileDesktopSyncCommand,
    OpenWebCommand,
    SelfHealCommand,
    ServeMobileCommand,
    StatusCommand,
    WeatherCommand,
)
from jarvis_engine.commands.task_commands import (
    QueryCommand,
    QueryResult,
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
)
from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    GrowthAuditCommand,
    GrowthEvalCommand,
    GrowthReportCommand,
    IntelligenceDashboardCommand,
    MissionCreateCommand,
    MissionRunCommand,
    MissionStatusCommand,
    OpsAutopilotCommand,
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
)
from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PersonaConfigCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
    RuntimeControlCommand,
)
from jarvis_engine.commands.knowledge_commands import (
    ContradictionListCommand,
    ContradictionResolveCommand,
    FactLockCommand,
    KnowledgeRegressionCommand,
    KnowledgeStatusCommand,
)
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)
from jarvis_engine.commands.learning_commands import (
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)

PHONE_NUMBER_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
URL_RE = re.compile(r"\b((?:https?://|www\.)[^\s<>{}\[\]\"']+)", flags=re.IGNORECASE)

# ---------------------------------------------------------------------------
# Command Bus factory -- respects monkeypatched repo_root() in tests
# ---------------------------------------------------------------------------

def _get_bus() -> CommandBus:
    """Create a Command Bus wired to the current repo_root().

    A fresh bus is created on each call so that test monkeypatching of
    ``repo_root`` is always respected.  Handler instantiation is cheap
    (no I/O, no model loading) so this has negligible overhead.
    """
    from jarvis_engine.app import create_app

    return create_app(repo_root())


_auto_ingest_lock = threading.Lock()


def _auto_ingest_dedupe_path() -> Path:
    return repo_root() / ".planning" / "runtime" / "auto_ingest_dedupe.json"


def _sanitize_memory_content(content: str) -> str:
    content = content[:100_000]  # Truncate before regex to prevent catastrophic backtracking
    # Redact master password, tokens, API keys, secrets, signing keys, bearer tokens
    _CRED_KEYS = r'(?:master[\s_-]*)?password|passwd|pwd|token|api[_-]?key|secret|signing[_-]?key'
    # JSON-style: "key": "value"
    cleaned = re.sub(
        rf'(?i)"({_CRED_KEYS})"\s*:\s*"[^"]*"',
        r'"\1": "[redacted]"',
        content,
    )
    # Unquoted style: key=value or key: value
    cleaned = re.sub(
        rf"(?i)({_CRED_KEYS})\s*[:=]\s*\S+",
        r"\1=[redacted]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)(bearer)\s+\S+", r"\1 [redacted]", cleaned)
    return cleaned.strip()[:2000]


def _load_auto_ingest_hashes(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, dict):
        return []
    values = raw.get("hashes", [])
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _store_auto_ingest_hashes(path: Path, hashes: list[str]) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload = {"hashes": hashes[-400:], "updated_utc": datetime.now(UTC).isoformat()}
    _atomic_write_json(path, payload)


_VALID_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
_VALID_KINDS = {"episodic", "semantic", "procedural"}

def _auto_ingest_memory(source: str, kind: str, task_id: str, content: str) -> str:
    if os.getenv("JARVIS_AUTO_INGEST_DISABLE", "").strip().lower() in {"1", "true", "yes"}:
        return ""
    if source not in _VALID_SOURCES or kind not in _VALID_KINDS:
        return ""
    safe_content = _sanitize_memory_content(content)
    if not safe_content:
        return ""
    safe_task_id = task_id[:128]
    dedupe_path = _auto_ingest_dedupe_path()
    dedupe_material = f"{source}|{kind}|{safe_task_id}|{safe_content.lower()}".encode("utf-8")
    dedupe_hash = hashlib.sha256(dedupe_material).hexdigest()
    # Lock prevents race condition when daemon + CLI ingest concurrently.
    # We lock around check + mark to prevent double-ingest, then do the
    # actual ingestion outside the lock (it involves I/O).
    with _auto_ingest_lock:
        seen = _load_auto_ingest_hashes(dedupe_path)
        seen_set = set(seen)
        if dedupe_hash in seen_set:
            return ""
        # Mark as seen immediately to prevent concurrent duplicates
        seen.append(dedupe_hash)
        _store_auto_ingest_hashes(dedupe_path, seen)

    store = MemoryStore(repo_root())
    pipeline = IngestionPipeline(store)
    rec = pipeline.ingest(
        source=source,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        task_id=safe_task_id,
        content=safe_content,
    )
    try:
        ingest_brain_record(
            repo_root(),
            source=source,
            kind=kind,
            task_id=safe_task_id,
            content=safe_content,
            tags=[source, kind],
            confidence=0.74 if source == "task_outcome" else 0.68,
        )
    except ValueError:
        import logging
        logging.getLogger(__name__).warning("brain ingest failed for task_id=%s", safe_task_id[:32])
    return rec.record_id


def _windows_idle_seconds() -> float | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)) == 0:  # type: ignore[attr-defined]
            return None
        tick_now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
        idle_ms = (tick_now - last_input.dwTime) & 0xFFFFFFFF
        return max(0.0, idle_ms / 1000.0)
    except Exception:
        return None


def _load_voice_auth_impl():
    try:
        from jarvis_engine.voice_auth import enroll_voiceprint, verify_voiceprint
    except ModuleNotFoundError as exc:
        return None, None, str(exc)
    return enroll_voiceprint, verify_voiceprint, ""


def _gaming_mode_state_path() -> Path:
    return repo_root() / ".planning" / "runtime" / "gaming_mode.json"


def _gaming_processes_path() -> Path:
    return repo_root() / ".planning" / "gaming_processes.json"


DEFAULT_GAMING_PROCESSES = (
    "FortniteClient-Win64-Shipping.exe",
    "VALORANT-Win64-Shipping.exe",
    "r5apex.exe",
    "cs2.exe",
    "Overwatch.exe",
    "RocketLeague.exe",
    "GTA5.exe",
    "eldenring.exe",
)


def _read_gaming_mode_state() -> dict[str, object]:
    path = _gaming_mode_state_path()
    default: dict[str, object] = {"enabled": False, "auto_detect": False, "updated_utc": "", "reason": ""}
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    if not isinstance(raw, dict):
        return default
    return {
        "enabled": bool(raw.get("enabled", False)),
        "auto_detect": bool(raw.get("auto_detect", False)),
        "updated_utc": str(raw.get("updated_utc", "")),
        "reason": str(raw.get("reason", "")),
    }


def _write_gaming_mode_state(state: dict[str, object]) -> dict[str, object]:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    path = _gaming_mode_state_path()
    payload = {
        "enabled": bool(state.get("enabled", False)),
        "auto_detect": bool(state.get("auto_detect", False)),
        "updated_utc": str(state.get("updated_utc", "")) or datetime.now(UTC).isoformat(),
        "reason": str(state.get("reason", "")).strip()[:200],
    }
    _atomic_write_json(path, payload)
    return payload


def _load_gaming_processes() -> list[str]:
    env_override = os.getenv("JARVIS_GAMING_PROCESSES", "").strip()
    if env_override:
        return [item.strip() for item in env_override.split(",") if item.strip()]

    path = _gaming_processes_path()
    if not path.exists():
        return list(DEFAULT_GAMING_PROCESSES)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return list(DEFAULT_GAMING_PROCESSES)

    if isinstance(raw, dict):
        values = raw.get("processes", [])
    elif isinstance(raw, list):
        values = raw
    else:
        values = []

    if not isinstance(values, list):
        return list(DEFAULT_GAMING_PROCESSES)
    processes = [str(item).strip() for item in values if str(item).strip()]
    return processes or list(DEFAULT_GAMING_PROCESSES)


def _detect_active_game_process() -> tuple[bool, str]:
    if os.name != "nt":
        return False, ""
    patterns = [name.lower() for name in _load_gaming_processes()]
    if not patterns:
        return False, ""
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if result.returncode != 0:
        return False, ""

    running: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("info:"):
            continue
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            continue
        if not row:
            continue
        running.append(row[0].strip().lower())

    for proc_name in running:
        for pattern in patterns:
            if proc_name == pattern or pattern in proc_name:
                return True, proc_name
    return False, ""


def cmd_gaming_mode(enable: bool | None, reason: str, auto_detect: str) -> int:
    result = _get_bus().dispatch(GamingModeCommand(enable=enable, reason=reason, auto_detect=auto_detect))
    state = result.state
    print("gaming_mode")
    print(f"enabled={bool(state.get('enabled', False))}")
    print(f"auto_detect={bool(state.get('auto_detect', False))}")
    print(f"auto_detect_active={result.detected}")
    if result.detected_process:
        print(f"detected_process={result.detected_process}")
    print(f"effective_enabled={result.effective_enabled}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_autopilot_paused_when_enabled")
    print(f"process_config={_gaming_processes_path()}")
    return 0


def cmd_status() -> int:
    result = _get_bus().dispatch(StatusCommand())
    print("Jarvis Engine Status")
    print(f"profile={result.profile}")
    print(f"primary_runtime={result.primary_runtime}")
    print(f"secondary_runtime={result.secondary_runtime}")
    print(f"security_strictness={result.security_strictness}")
    print(f"operation_mode={result.operation_mode}")
    print(f"cloud_burst_enabled={result.cloud_burst_enabled}")
    print("recent_events:")
    if not result.events:
        print("- none")
    else:
        for event in result.events:
            print(f"- [{event.ts}] {event.event_type}: {event.message}")
    return 0


def cmd_log(event_type: str, message: str) -> int:
    result = _get_bus().dispatch(LogCommand(event_type=event_type, message=message))
    print(f"logged: [{result.ts}] {result.event_type}: {result.message}")
    return 0


def cmd_ingest(source: str, kind: str, task_id: str, content: str) -> int:
    result = _get_bus().dispatch(IngestCommand(source=source, kind=kind, task_id=task_id, content=content))
    print(f"ingested: id={result.record_id} source={result.source} kind={result.kind} task_id={result.task_id}")
    return 0


def cmd_serve_mobile(host: str, port: int, token: str | None, signing_key: str | None, allow_insecure_bind: bool = False) -> int:
    effective_token = token or os.getenv("JARVIS_MOBILE_TOKEN", "").strip()
    effective_signing_key = signing_key or os.getenv("JARVIS_MOBILE_SIGNING_KEY", "").strip()
    if not effective_token:
        print("error: missing mobile token. pass --token or set JARVIS_MOBILE_TOKEN")
        return 2
    if not effective_signing_key:
        print("error: missing signing key. pass --signing-key or set JARVIS_MOBILE_SIGNING_KEY")
        return 2

    if allow_insecure_bind:
        os.environ["JARVIS_ALLOW_INSECURE_MOBILE_BIND"] = "true"

    # NOTE: run_mobile_server is called directly here (not via bus) so that
    # tests can monkeypatch main_mod.run_mobile_server.
    try:
        run_mobile_server(
            host=host,
            port=port,
            auth_token=effective_token,
            signing_key=effective_signing_key,
            repo_root=repo_root(),
        )
    except KeyboardInterrupt:
        print("\nmobile_api_stopped=true")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 3
    except OSError as exc:
        print(f"error: could not bind mobile API on {host}:{port}: {exc}")
        return 3
    return 0


def cmd_route(risk: str, complexity: str) -> int:
    result = _get_bus().dispatch(RouteCommand(risk=risk, complexity=complexity))
    print(f"provider={result.provider}")
    print(f"reason={result.reason}")
    return 0


def cmd_growth_eval(
    model: str,
    endpoint: str,
    tasks_path: Path,
    history_path: Path,
    num_predict: int,
    temperature: float,
    think: bool | None,
    accept_thinking: bool,
    timeout_s: int,
) -> int:
    result = _get_bus().dispatch(GrowthEvalCommand(
        model=model, endpoint=endpoint, tasks_path=tasks_path,
        history_path=history_path, num_predict=num_predict,
        temperature=temperature, think=think,
        accept_thinking=accept_thinking, timeout_s=timeout_s,
    ))
    run = result.run
    if run is None:
        print("error: growth eval failed")
        return 2
    print("growth_eval_completed=true")
    print(f"model={run.model}")
    print(f"score_pct={run.score_pct}")
    print(f"avg_tps={run.avg_tps}")
    print(f"avg_latency_s={run.avg_latency_s}")
    for task_result in run.results:
        print(
            "task="
            f"{task_result.task_id} "
            f"coverage_pct={round(task_result.coverage * 100, 2)} "
            f"matched={task_result.matched}/{task_result.total} "
            f"response_sha256={task_result.response_sha256}"
        )
    return 0


def cmd_growth_report(history_path: Path, last: int) -> int:
    result = _get_bus().dispatch(GrowthReportCommand(history_path=history_path, last=last))
    summary = result.summary or {}
    print("growth_report")
    print(f"runs={summary.get('runs', 0)}")
    print(f"latest_model={summary.get('latest_model', '')}")
    print(f"latest_score_pct={summary.get('latest_score_pct', 0.0)}")
    print(f"delta_vs_prev_pct={summary.get('delta_vs_prev_pct', 0.0)}")
    print(f"window_avg_pct={summary.get('window_avg_pct', 0.0)}")
    print(f"latest_ts={summary.get('latest_ts', '')}")
    return 0


def cmd_growth_audit(history_path: Path, run_index: int) -> int:
    result = _get_bus().dispatch(GrowthAuditCommand(history_path=history_path, run_index=run_index))
    run = result.run or {}
    print("growth_audit")
    print(f"model={run.get('model', '')}")
    print(f"ts={run.get('ts', '')}")
    print(f"score_pct={run.get('score_pct', 0.0)}")
    print(f"tasks={run.get('tasks', 0)}")
    print(f"prev_run_sha256={run.get('prev_run_sha256', '')}")
    print(f"run_sha256={run.get('run_sha256', '')}")
    for audit_result in run.get("results", []):
        matched_tokens = ",".join(audit_result.get("matched_tokens", []))
        required_tokens = ",".join(audit_result.get("required_tokens", []))
        print(f"task={audit_result.get('task_id', '')}")
        print(f"required_tokens={required_tokens}")
        print(f"matched_tokens={matched_tokens}")
        print(f"prompt_sha256={audit_result.get('prompt_sha256', '')}")
        print(f"response_sha256={audit_result.get('response_sha256', '')}")
        print(f"response_source={audit_result.get('response_source', '')}")
        print(f"response={audit_result.get('response', '')}")
    return 0


def cmd_intelligence_dashboard(last_runs: int, output_path: str, as_json: bool) -> int:
    result = _get_bus().dispatch(IntelligenceDashboardCommand(last_runs=last_runs, output_path=output_path, as_json=as_json))
    dashboard = result.dashboard
    if as_json:
        text = json.dumps(dashboard, ensure_ascii=True, indent=2)
        print(text)
        if output_path.strip():
            out = Path(output_path).resolve()
            try:
                out.relative_to(repo_root().resolve())
            except ValueError:
                print("error: output path must be within project root.")
                return 2
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(f"dashboard_saved={out}")
        return 0

    jarvis = dashboard.get("jarvis", {})
    methodology = dashboard.get("methodology", {})
    etas = dashboard.get("etas", [])
    achievements = dashboard.get("achievements", {})
    ranking = dashboard.get("ranking", [])

    print("intelligence_dashboard")
    print(f"generated_utc={dashboard.get('generated_utc', '')}")
    print(f"jarvis_score_pct={jarvis.get('score_pct', 0.0)}")
    print(f"jarvis_delta_vs_prev_pct={jarvis.get('delta_vs_prev_pct', 0.0)}")
    print(f"jarvis_window_avg_pct={jarvis.get('window_avg_pct', 0.0)}")
    print(f"latest_model={jarvis.get('latest_model', '')}")
    print(f"history_runs={methodology.get('history_runs', 0)}")
    print(f"slope_score_pct_per_run={methodology.get('slope_score_pct_per_run', 0.0)}")
    print(f"avg_days_per_run={methodology.get('avg_days_per_run', 0.0)}")
    for idx, item in enumerate(ranking, start=1):
        print(f"rank_{idx}={item.get('name','')}:{item.get('score_pct', 0.0)}")
    for row in etas:
        eta = row.get("eta", {})
        print(
            "eta "
            f"target={row.get('target_name','')} "
            f"target_score_pct={row.get('target_score_pct', 0.0)} "
            f"status={eta.get('status','')} "
            f"runs={eta.get('runs', '')} "
            f"days={eta.get('days', '')}"
        )
    new_unlocks = achievements.get("new", [])
    if isinstance(new_unlocks, list):
        for item in new_unlocks:
            if not isinstance(item, dict):
                continue
            print(f"achievement_unlocked={item.get('label', '')}")

    if output_path.strip():
        out = Path(output_path).resolve()
        try:
            out.relative_to(repo_root().resolve())
        except ValueError:
            print("error: output path must be within project root.")
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dashboard, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"dashboard_saved={out}")
    return 0


def cmd_brain_status(as_json: bool) -> int:
    result = _get_bus().dispatch(BrainStatusCommand(as_json=as_json))
    status = result.status
    if as_json:
        print(json.dumps(status, ensure_ascii=True, indent=2))
        return 0
    print("brain_status")
    print(f"updated_utc={status.get('updated_utc', '')}")
    print(f"branch_count={status.get('branch_count', 0)}")
    branches = status.get("branches", [])
    if isinstance(branches, list):
        for row in branches[:12]:
            if not isinstance(row, dict):
                continue
            print(
                f"branch={row.get('branch','')} count={row.get('count', 0)} "
                f"last_ts={row.get('last_ts','')} summary={row.get('last_summary','')}"
            )
    return 0


def cmd_brain_context(query: str, max_items: int, max_chars: int, as_json: bool) -> int:
    if not query.strip():
        print("error: query is required")
        return 2
    result = _get_bus().dispatch(BrainContextCommand(query=query, max_items=max_items, max_chars=max_chars, as_json=as_json))
    packet = result.packet
    if as_json:
        print(json.dumps(packet, ensure_ascii=True, indent=2))
        return 0
    print("brain_context")
    print(f"query={packet.get('query', '')}")
    print(f"selected_count={packet.get('selected_count', 0)}")
    selected = packet.get("selected", [])
    if isinstance(selected, list):
        for idx, row in enumerate(selected, start=1):
            if not isinstance(row, dict):
                continue
            print(
                f"context_{idx}=branch:{row.get('branch','')} "
                f"source:{row.get('source','')} "
                f"kind:{row.get('kind','')} "
                f"summary:{row.get('summary','')}"
            )
    facts = packet.get("canonical_facts", [])
    if isinstance(facts, list):
        for idx, item in enumerate(facts, start=1):
            if not isinstance(item, dict):
                continue
            print(
                f"fact_{idx}=key:{item.get('key','')} "
                f"value:{item.get('value','')} "
                f"confidence:{item.get('confidence', 0.0)}"
            )
    return 0


def cmd_brain_compact(keep_recent: int, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(BrainCompactCommand(keep_recent=keep_recent, as_json=as_json))
    result = bus_result.result
    if as_json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    print("brain_compact")
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


def cmd_brain_regression(as_json: bool) -> int:
    result = _get_bus().dispatch(BrainRegressionCommand(as_json=as_json))
    report = result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    print("brain_regression_report")
    for key, value in report.items():
        print(f"{key}={value}")
    return 0


def cmd_knowledge_status(as_json: bool) -> int:
    result = _get_bus().dispatch(KnowledgeStatusCommand(as_json=as_json))
    if as_json:
        print(json.dumps({
            "node_count": result.node_count,
            "edge_count": result.edge_count,
            "locked_count": result.locked_count,
            "pending_contradictions": result.pending_contradictions,
            "graph_hash": result.graph_hash,
        }, ensure_ascii=True, indent=2))
        return 0
    print("knowledge_status")
    print(f"node_count={result.node_count}")
    print(f"edge_count={result.edge_count}")
    print(f"locked_count={result.locked_count}")
    print(f"pending_contradictions={result.pending_contradictions}")
    print(f"graph_hash={result.graph_hash}")
    return 0


def cmd_contradiction_list(status: str, limit: int, as_json: bool) -> int:
    result = _get_bus().dispatch(ContradictionListCommand(status=status, limit=limit))
    if as_json:
        print(json.dumps({"contradictions": result.contradictions}, ensure_ascii=True, indent=2, default=str))
        return 0
    if not result.contradictions:
        print("No contradictions found.")
        return 0
    for c in result.contradictions:
        print(f"id={c.get('contradiction_id')} node={c.get('node_id')} "
              f"existing={c.get('existing_value')!r} incoming={c.get('incoming_value')!r} "
              f"status={c.get('status')} created={c.get('created_at')}")
    return 0


def cmd_contradiction_resolve(contradiction_id: int, resolution: str, merge_value: str) -> int:
    result = _get_bus().dispatch(ContradictionResolveCommand(
        contradiction_id=contradiction_id,
        resolution=resolution,
        merge_value=merge_value,
    ))
    if result.success:
        print(f"resolved=true node_id={result.node_id} resolution={result.resolution}")
        print(result.message)
    else:
        print(f"resolved=false")
        print(result.message)
        return 1
    return 0


def cmd_fact_lock(node_id: str, action: str) -> int:
    result = _get_bus().dispatch(FactLockCommand(node_id=node_id, action=action))
    if result.success:
        print(f"success=true node_id={result.node_id} locked={result.locked}")
    else:
        print(f"success=false node_id={result.node_id}")
        return 1
    return 0


def cmd_knowledge_regression(snapshot_path: str, as_json: bool) -> int:
    result = _get_bus().dispatch(KnowledgeRegressionCommand(
        snapshot_path=snapshot_path,
        as_json=as_json,
    ))
    report = result.report or {}
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
        return 0
    status = report.get("status", "unknown")
    print(f"knowledge_regression status={status}")
    if report.get("message"):
        print(report["message"])
    for d in report.get("discrepancies", []):
        print(f"  [{d.get('severity')}] {d.get('type')}: {d.get('message')}")
    current = report.get("current", {})
    if current:
        print(f"  current: nodes={current.get('node_count', 0)} edges={current.get('edge_count', 0)} "
              f"locked={current.get('locked_count', 0)} hash={current.get('graph_hash', '')}")
    return 0


def cmd_memory_snapshot(create: bool, verify_path: str | None, note: str) -> int:
    result = _get_bus().dispatch(MemorySnapshotCommand(create=create, verify_path=verify_path, note=note))
    if result.created:
        print("memory_snapshot_created=true")
        print(f"snapshot_path={result.snapshot_path}")
        print(f"metadata_path={result.metadata_path}")
        print(f"signature_path={result.signature_path}")
        print(f"sha256={result.sha256}")
        print(f"file_count={result.file_count}")
        return 0
    if result.verified:
        print("memory_snapshot_verification")
        print(f"ok={result.ok}")
        print(f"reason={result.reason}")
        print(f"expected_sha256={result.expected_sha256}")
        print(f"actual_sha256={result.actual_sha256}")
        return 0 if result.ok else 2
    print("error: choose --create or --verify-path")
    return 2


def cmd_memory_maintenance(keep_recent: int, snapshot_note: str) -> int:
    result = _get_bus().dispatch(MemoryMaintenanceCommand(keep_recent=keep_recent, snapshot_note=snapshot_note))
    report = result.report
    print("memory_maintenance")
    print(f"status={report.get('status', 'unknown')}")
    print(f"report_path={report.get('report_path', '')}")
    compact = report.get("compact", {})
    if isinstance(compact, dict):
        print(f"compacted={compact.get('compacted', False)}")
        print(f"total_records={compact.get('total_records', 0)}")
        print(f"kept_records={compact.get('kept_records', 0)}")
    regression = report.get("regression", {})
    if isinstance(regression, dict):
        print(f"regression_status={regression.get('status', '')}")
        print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
        print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    snapshot = report.get("snapshot", {})
    if isinstance(snapshot, dict):
        print(f"snapshot_path={snapshot.get('path', '')}")
    return 0


def cmd_persona_config(
    *,
    enable: bool,
    disable: bool,
    humor_level: int | None,
    mode: str,
    style: str,
) -> int:
    result = _get_bus().dispatch(PersonaConfigCommand(
        enable=enable, disable=disable, humor_level=humor_level, mode=mode, style=style,
    ))
    cfg = result.config

    print("persona_config")
    print(f"enabled={cfg.enabled}")
    print(f"mode={cfg.mode}")
    print(f"style={cfg.style}")
    print(f"humor_level={cfg.humor_level}")
    print(f"updated_utc={cfg.updated_utc}")
    return 0


def cmd_desktop_widget() -> int:
    result = _get_bus().dispatch(DesktopWidgetCommand())
    if result.return_code != 0:
        print("error: desktop widget unavailable")
    return result.return_code


def cmd_run_task(
    task_type: str,
    prompt: str,
    execute: bool,
    approve_privileged: bool,
    model: str,
    endpoint: str,
    quality_profile: str,
    output_path: str | None,
) -> int:
    result = _get_bus().dispatch(RunTaskCommand(
        task_type=task_type, prompt=prompt, execute=execute,
        approve_privileged=approve_privileged, model=model,
        endpoint=endpoint, quality_profile=quality_profile,
        output_path=output_path,
    ))
    print(f"allowed={result.allowed}")
    print(f"provider={result.provider}")
    print(f"plan={result.plan}")
    print(f"reason={result.reason}")
    if result.output_path:
        print(f"output_path={result.output_path}")
    if result.output_text:
        print("output_text_begin")
        print(result.output_text)
        print("output_text_end")
    if result.auto_ingest_record_id:
        print(f"auto_ingest_record_id={result.auto_ingest_record_id}")
    return result.return_code


def cmd_ops_brief(snapshot_path: Path, output_path: Path | None) -> int:
    result = _get_bus().dispatch(OpsBriefCommand(snapshot_path=snapshot_path, output_path=output_path))
    print(result.brief)
    if result.saved_path:
        print(f"brief_saved={result.saved_path}")
    return 0


def cmd_ops_export_actions(snapshot_path: Path, actions_path: Path) -> int:
    result = _get_bus().dispatch(OpsExportActionsCommand(snapshot_path=snapshot_path, actions_path=actions_path))
    print(f"actions_exported={result.actions_path}")
    print(f"action_count={result.action_count}")
    return 0


def cmd_ops_sync(output_path: Path) -> int:
    result = _get_bus().dispatch(OpsSyncCommand(output_path=output_path))
    summary = result.summary
    if summary is None:
        print("error: ops sync failed")
        return 2
    print(f"snapshot_path={summary.snapshot_path}")
    print(f"tasks={summary.tasks}")
    print(f"calendar_events={summary.calendar_events}")
    print(f"emails={summary.emails}")
    print(f"bills={summary.bills}")
    print(f"subscriptions={summary.subscriptions}")
    print(f"medications={summary.medications}")
    print(f"school_items={summary.school_items}")
    print(f"family_items={summary.family_items}")
    print(f"projects={summary.projects}")
    print(f"connectors_ready={summary.connectors_ready}")
    print(f"connectors_pending={summary.connectors_pending}")
    print(f"connector_prompts={summary.connector_prompts}")
    if summary.connector_prompts > 0:
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        prompts = raw.get("connector_prompts", []) if isinstance(raw, dict) else []
        for item in prompts:
            if not isinstance(item, dict):
                continue
            print(
                "connector_prompt "
                f"id={item.get('connector_id','')} "
                f"voice=\"{item.get('option_voice','')}\" "
                f"tap={item.get('option_tap_url','')}"
            )
    return 0


def _cmd_ops_autopilot_impl(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    """Implementation body for ops-autopilot (called by handler via callback)."""
    cmd_connect_bootstrap(auto_open=auto_open_connectors)
    sync_rc = cmd_ops_sync(snapshot_path)
    if sync_rc != 0:
        return sync_rc
    brief_rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    if brief_rc != 0:
        return brief_rc
    export_rc = cmd_ops_export_actions(snapshot_path=snapshot_path, actions_path=actions_path)
    if export_rc != 0:
        return export_rc
    return cmd_automation_run(
        actions_path=actions_path,
        approve_privileged=approve_privileged,
        execute=execute,
    )


def cmd_ops_autopilot(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    result = _get_bus().dispatch(OpsAutopilotCommand(
        snapshot_path=snapshot_path, actions_path=actions_path,
        execute=execute, approve_privileged=approve_privileged,
        auto_open_connectors=auto_open_connectors,
    ))
    return result.return_code


def cmd_automation_run(actions_path: Path, approve_privileged: bool, execute: bool) -> int:
    result = _get_bus().dispatch(AutomationRunCommand(
        actions_path=actions_path, approve_privileged=approve_privileged, execute=execute,
    ))
    for out in result.outcomes:
        print(
            f"title={out.title} allowed={out.allowed} executed={out.executed} "
            f"return_code={out.return_code} reason={out.reason}"
        )
        if out.stderr:
            print(f"stderr={out.stderr.strip()}")
    return 0


def cmd_mission_create(topic: str, objective: str, sources: list[str]) -> int:
    result = _get_bus().dispatch(MissionCreateCommand(topic=topic, objective=objective, sources=sources))
    if result.return_code != 0:
        print("error: mission creation failed")
        return result.return_code
    mission = result.mission
    print("learning_mission_created=true")
    print(f"mission_id={mission.get('mission_id', '')}")
    print(f"topic={mission.get('topic', '')}")
    print(f"sources={','.join(str(s) for s in mission.get('sources', []))}")
    return 0


def cmd_mission_status(last: int) -> int:
    result = _get_bus().dispatch(MissionStatusCommand(last=last))
    if not result.missions:
        print("learning_missions=none")
        return 0
    print(f"learning_mission_count={result.total_count}")
    for mission in result.missions:
        print(
            f"mission_id={mission.get('mission_id','')} "
            f"status={mission.get('status','')} "
            f"topic={mission.get('topic','')} "
            f"verified_findings={mission.get('verified_findings', 0)} "
            f"updated_utc={mission.get('updated_utc','')}"
        )
    return 0


def cmd_mission_run(mission_id: str, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    result = _get_bus().dispatch(MissionRunCommand(
        mission_id=mission_id, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: mission run failed")
        return result.return_code

    report = result.report
    print("learning_mission_completed=true")
    print(f"mission_id={report.get('mission_id', '')}")
    print(f"candidate_count={report.get('candidate_count', 0)}")
    print(f"verified_count={report.get('verified_count', 0)}")
    verified = report.get("verified_findings", [])
    if isinstance(verified, list):
        for idx, finding in enumerate(verified[:10], start=1):
            statement = str(finding.get("statement", "")) if isinstance(finding, dict) else ""
            sources = ",".join(finding.get("source_domains", [])) if isinstance(finding, dict) else ""
            print(f"verified_{idx}={statement}")
            print(f"verified_{idx}_sources={sources}")

    if result.ingested_record_id:
        print(f"mission_ingested_record_id={result.ingested_record_id}")
    return 0


def _run_next_pending_mission(*, max_results: int = 6, max_pages: int = 10) -> int:
    missions = load_missions(repo_root())
    for mission in missions:
        if str(mission.get("status", "")).lower() != "pending":
            continue
        mission_id = str(mission.get("mission_id", "")).strip()
        if not mission_id:
            continue
        print(f"mission_autorun_id={mission_id}")
        return cmd_mission_run(
            mission_id=mission_id,
            max_results=max_results,
            max_pages=max_pages,
            auto_ingest=True,
        )
    return 0


def cmd_runtime_control(
    *,
    pause: bool,
    resume: bool,
    safe_on: bool,
    safe_off: bool,
    reset: bool,
    reason: str,
) -> int:
    result = _get_bus().dispatch(RuntimeControlCommand(
        pause=pause, resume=resume, safe_on=safe_on, safe_off=safe_off, reset=reset, reason=reason,
    ))
    state = result.state
    print("runtime_control")
    print(f"daemon_paused={bool(state.get('daemon_paused', False))}")
    print(f"safe_mode={bool(state.get('safe_mode', False))}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_paused_skips_autopilot,safe_mode_forces_non_executing_cycles")
    return 0


def cmd_owner_guard(
    *,
    enable: bool,
    disable: bool,
    owner_user: str,
    trust_device: str,
    revoke_device: str,
    set_master_password_value: str,
    clear_master_password_value: bool,
) -> int:
    result = _get_bus().dispatch(OwnerGuardCommand(
        enable=enable, disable=disable, owner_user=owner_user,
        trust_device=trust_device, revoke_device=revoke_device,
        set_master_password_value=set_master_password_value,
        clear_master_password_value=clear_master_password_value,
    ))
    if result.return_code != 0:
        if enable and not owner_user.strip():
            print("error: --owner-user is required with --enable")
        else:
            print("error: owner guard operation failed")
        return result.return_code
    state = result.state

    print("owner_guard")
    print(f"enabled={bool(state.get('enabled', False))}")
    print(f"owner_user_id={state.get('owner_user_id', '')}")
    trusted = state.get("trusted_mobile_devices", [])
    if isinstance(trusted, list):
        print(f"trusted_mobile_devices={','.join(str(x) for x in trusted)}")
        print(f"trusted_mobile_device_count={len(trusted)}")
    has_master_password = bool(state.get("master_password_hash", ""))
    print(f"master_password_set={has_master_password}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    print("effect=voice_run_restricted_to_owner_and_mobile_api_restricted_to_trusted_devices_when_enabled")
    return 0


def cmd_connect_status() -> int:
    result = _get_bus().dispatch(ConnectStatusCommand())
    print("connector_status")
    print(f"ready={result.ready}")
    print(f"pending={result.pending}")
    for status in result.statuses:
        print(
            f"id={status.connector_id} ready={status.ready} "
            f"permission={status.permission_granted} configured={status.configured} message={status.message}"
        )
    if result.prompts:
        print("connector_prompts_begin")
        for prompt in result.prompts:
            print(
                f"id={prompt.get('connector_id','')} "
                f"voice={prompt.get('option_voice','')} "
                f"tap={prompt.get('option_tap_url','')}"
            )
        print("connector_prompts_end")
    return 0


def cmd_connect_grant(connector_id: str, scopes: list[str]) -> int:
    result = _get_bus().dispatch(ConnectGrantCommand(connector_id=connector_id, scopes=scopes))
    if result.return_code != 0:
        print("error: connector grant failed")
        return result.return_code
    print(f"connector_id={connector_id}")
    print("granted=true")
    print(f"scopes={','.join(result.granted.get('scopes', []))}")
    print(f"granted_utc={result.granted.get('granted_utc', '')}")
    return 0


def cmd_connect_bootstrap(auto_open: bool) -> int:
    result = _get_bus().dispatch(ConnectBootstrapCommand(auto_open=auto_open))
    if result.ready:
        print("connectors_ready=true")
        return 0
    print("connectors_ready=false")
    for prompt in result.prompts:
        print(
            "connector_prompt "
            f"id={prompt.get('connector_id','')} "
            f"voice=\"{prompt.get('option_voice','')}\" "
            f"tap={prompt.get('option_tap_url','')}"
        )
    return 0


def cmd_phone_action(action: str, number: str, message: str, queue_path: Path, queue_action: bool = True) -> int:
    result = _get_bus().dispatch(PhoneActionCommand(
        action=action, number=number, message=message, queue_path=queue_path, queue_action=queue_action,
    ))
    if result.return_code != 0:
        print("error: phone action failed")
        return result.return_code
    record = result.record
    print(f"phone_action_queued={queue_action}")
    print(f"action={record.action}")
    print(f"number={record.number}")
    if record.message:
        print(f"message={record.message}")
    print(f"queue_path={queue_path}")
    return 0


def cmd_phone_spam_guard(
    call_log_path: Path,
    report_path: Path,
    queue_path: Path,
    threshold: float,
    *,
    queue_actions: bool = True,
) -> int:
    result = _get_bus().dispatch(PhoneSpamGuardCommand(
        call_log_path=call_log_path, report_path=report_path, queue_path=queue_path,
        threshold=threshold, queue_actions=queue_actions,
    ))
    if result.return_code != 0:
        if not call_log_path.exists():
            print(f"error: call log not found: {call_log_path}")
        else:
            print("error: invalid call log JSON.")
        return result.return_code

    print(f"spam_candidates={result.candidates_count}")
    print(f"queued_actions={result.queued_actions_count}")
    print(f"report_path={report_path}")
    print(f"queue_path={queue_path}")
    print("option_voice=Jarvis, block likely spam calls now")
    print("option_tap=https://www.samsung.com/us/support/answer/ANS10003465/")
    return 0


def cmd_weather(location: str) -> int:
    result = _get_bus().dispatch(WeatherCommand(location=location))
    if result.return_code != 0:
        print("error: weather lookup failed")
        return result.return_code

    print("weather_report")
    print(f"location={result.location}")
    print(f"temperature_f={result.current.get('temp_F', '')}")
    print(f"temperature_c={result.current.get('temp_C', '')}")
    print(f"feels_like_f={result.current.get('FeelsLikeF', '')}")
    print(f"humidity={result.current.get('humidity', '')}")
    if result.description:
        print(f"conditions={result.description}")
    return 0


def cmd_migrate_memory() -> int:
    """Migrate JSONL/JSON memory data into SQLite (one-time command)."""
    result = _get_bus().dispatch(MigrateMemoryCommand())
    if result.return_code != 0:
        print("error: memory migration failed")
        return result.return_code
    summary = result.summary
    totals = summary.get("totals", {})
    print("memory_migration_complete")
    print(f"total_inserted={totals.get('inserted', 0)}")
    print(f"total_skipped={totals.get('skipped', 0)}")
    print(f"total_errors={totals.get('errors', 0)}")
    print(f"db_path={summary.get('db_path', '')}")
    return 0


def cmd_web_research(query: str, *, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    cleaned = query.strip()
    if not cleaned:
        print("error: query is required for web research.")
        return 2
    result = _get_bus().dispatch(WebResearchCommand(
        query=query, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: web research failed")
        return result.return_code

    report = result.report
    print("web_research")
    print(f"query={report.get('query', '')}")
    print(f"scanned_url_count={report.get('scanned_url_count', 0)}")
    findings = report.get("findings", [])
    if isinstance(findings, list):
        for idx, row in enumerate(findings[:6], start=1):
            if not isinstance(row, dict):
                continue
            print(f"source_{idx}={row.get('domain', '')} {row.get('url', '')}")
            snippet = str(row.get("snippet", "")).strip()
            if snippet:
                print(f"finding_{idx}={snippet[:260]}")

    # Emit a response= summary so the Quick Panel and TTS can display findings.
    summary_parts: list[str] = []
    if isinstance(findings, list):
        for row in findings[:4]:
            if not isinstance(row, dict):
                continue
            snippet = str(row.get("snippet", "")).strip()
            domain = str(row.get("domain", "")).strip()
            if snippet:
                summary_parts.append(f"{snippet} ({domain})" if domain else snippet)
    if summary_parts:
        print(f"response=Here's what I found: " + " | ".join(summary_parts))
    else:
        print(f"response=I searched the web for '{report.get('query', '')}' but couldn't find clear results.")

    if result.auto_ingest_record_id:
        print(f"auto_ingest_record_id={result.auto_ingest_record_id}")
    return 0


def cmd_mobile_desktop_sync(*, auto_ingest: bool, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(MobileDesktopSyncCommand(auto_ingest=auto_ingest, as_json=as_json))
    report = bus_result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print("mobile_desktop_sync")
        print(f"sync_ok={report.get('sync_ok', False)}")
        print(f"report_path={report.get('report_path', '')}")
        checks = report.get("checks", [])
        if isinstance(checks, list):
            for row in checks:
                if not isinstance(row, dict):
                    continue
                print(f"check_{row.get('name','')}={row.get('ok', False)}")
    if auto_ingest:
        rec_id = _auto_ingest_memory(
            source="task_outcome",
            kind="episodic",
            task_id=f"sync-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            content=(
                f"Mobile/Desktop sync executed. "
                f"sync_ok={report.get('sync_ok', False)}; "
                f"trusted_mobile_devices={report.get('owner_guard', {}).get('trusted_mobile_device_count', 0)}"
            ),
        )
        if rec_id:
            print(f"auto_ingest_record_id={rec_id}")
    return bus_result.return_code


def cmd_self_heal(*, force_maintenance: bool, keep_recent: int, snapshot_note: str, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(SelfHealCommand(
        force_maintenance=force_maintenance, keep_recent=keep_recent,
        snapshot_note=snapshot_note, as_json=as_json,
    ))
    report = bus_result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print("self_heal")
        print(f"status={report.get('status', 'unknown')}")
        print(f"report_path={report.get('report_path', '')}")
        actions = report.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                print(f"action={action}")
        regression = report.get("regression", {})
        if isinstance(regression, dict):
            print(f"regression_status={regression.get('status', '')}")
            print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
            print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    return bus_result.return_code


def cmd_harvest(topic: str, providers: str | None, max_tokens: int) -> int:
    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    result = _get_bus().dispatch(HarvestTopicCommand(
        topic=topic,
        providers=provider_list,
        max_tokens=max_tokens,
    ))
    print(f"harvest_topic={result.topic}")
    for entry in result.results:
        status = entry.get("status", "unknown")
        provider = entry.get("provider", "unknown")
        records = entry.get("records_created", 0)
        cost = entry.get("cost_usd", 0.0)
        print(f"provider={provider} status={status} records={records} cost_usd={cost:.6f}")
    return result.return_code


def cmd_ingest_session(source: str, session_path: str | None, project_path: str | None) -> int:
    result = _get_bus().dispatch(IngestSessionCommand(
        source=source,
        session_path=session_path,
        project_path=project_path,
    ))
    print(f"ingest_session_source={result.source}")
    print(f"sessions_processed={result.sessions_processed}")
    print(f"records_created={result.records_created}")
    return result.return_code


def cmd_harvest_budget(action: str, provider: str | None, period: str | None,
                       limit_usd: float | None, limit_requests: int | None) -> int:
    result = _get_bus().dispatch(HarvestBudgetCommand(
        action=action,
        provider=provider,
        period=period,
        limit_usd=limit_usd,
        limit_requests=limit_requests,
    ))
    summary = result.summary
    if action == "set":
        print(f"budget_set provider={summary.get('provider', '')} period={summary.get('period', '')} "
              f"limit_usd={summary.get('limit_usd', 0.0)}")
    else:
        print(f"budget_period_days={summary.get('period_days', 30)}")
        print(f"budget_total_cost_usd={summary.get('total_cost_usd', 0.0):.6f}")
        for entry in summary.get("providers", []):
            print(f"provider={entry.get('provider', '')} "
                  f"cost_usd={entry.get('total_cost_usd', 0.0):.6f} "
                  f"requests={entry.get('total_requests', 0)}")
    return result.return_code


# ---------------------------------------------------------------------------
# Learning CLI commands
# ---------------------------------------------------------------------------

def cmd_learn(user_message: str, assistant_response: str) -> int:
    result = _get_bus().dispatch(LearnInteractionCommand(
        user_message=user_message,
        assistant_response=assistant_response,
    ))
    print(f"records_created={result.records_created}")
    print(f"message={result.message}")
    return 0


def cmd_cross_branch_query(query: str, k: int) -> int:
    result = _get_bus().dispatch(CrossBranchQueryCommand(
        query=query,
        k=k,
    ))
    print(f"direct_results={len(result.direct_results)}")
    for dr in result.direct_results:
        print(f"  record_id={dr.get('record_id', '')} distance={dr.get('distance', 0.0):.4f}")
    print(f"cross_branch_connections={len(result.cross_branch_connections)}")
    for cb in result.cross_branch_connections:
        print(f"  {cb.get('source_branch', '?')}->{cb.get('target_branch', '?')} relation={cb.get('relation', '')}")
    print(f"branches_involved={result.branches_involved}")
    return 0


def cmd_flag_expired() -> int:
    result = _get_bus().dispatch(FlagExpiredFactsCommand())
    print(f"expired_count={result.expired_count}")
    print(f"message={result.message}")
    return 0


def cmd_memory_eval() -> int:
    from jarvis_engine.growth_tracker import (
        DEFAULT_MEMORY_TASKS,
        run_memory_eval,
    )

    from jarvis_engine.app import create_app
    from jarvis_engine.config import repo_root as _repo_root

    root = _repo_root()
    db_path = root / ".planning" / "brain" / "jarvis_memory.db"

    engine = None
    embed_service = None
    if db_path.exists():
        try:
            from jarvis_engine.memory.embeddings import EmbeddingService
            from jarvis_engine.memory.engine import MemoryEngine

            embed_service = EmbeddingService()
            engine = MemoryEngine(db_path, embed_service=embed_service)
        except Exception as exc:
            print(f"error=failed to init memory engine: {exc}")
            return 1

    try:
        results = run_memory_eval(DEFAULT_MEMORY_TASKS, engine, embed_service)
    except RuntimeError as exc:
        print(f"error={exc}")
        return 1

    for r in results:
        print(
            f"task={r.task_id} score={r.overall_score:.2f} "
            f"results={r.results_found} branch_cov={r.branch_coverage:.2f} "
            f"kw_cov={r.keyword_coverage:.2f}"
        )

    if results:
        avg = sum(r.overall_score for r in results) / len(results)
        print(f"average_score={avg:.4f}")
    else:
        print("average_score=0.0000")
    return 0


def _extract_first_phone_number(text: str) -> str:
    if len(text) > 256:
        text = text[:256]
    match = PHONE_NUMBER_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_weather_location(text: str) -> str:
    # Try explicit "in/for <location>" first
    match = re.search(r"(?:weather|forecast)\s+(?:in|for|at)\s+(.+)", text, flags=re.IGNORECASE)
    if match:
        location = match.group(1).strip().rstrip("?.!,;:")
        return location[:120]
    # Fallback: grab text after weather/forecast, filter noise words
    match = re.search(r"(?:weather|forecast)\s+(.+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    location = match.group(1).strip().rstrip("?.!,;:")
    noise = {"like", "today", "right", "now", "outside", "currently", "report",
             "update", "check", "please", "is", "the", "what", "how", "look"}
    words = [w for w in location.split() if w.lower() not in noise]
    return " ".join(words)[:120]


def _extract_web_query(text: str) -> str:
    lowered = text.lower().strip()
    patterns = [
        r"(?:search(?:\s+the)?\s+(?:web|internet|online)\s+for)\s+(.+)",
        r"(?:research)\s+(.+)",
        r"(?:look\s*up|lookup)\s+(.+)",
        r"(?:find(?:\s+on\s+the\s+web)?)\s+(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip().rstrip("?.!,;:")
        if value:
            return value[:260]
    cleaned = lowered
    for prefix in ("jarvis,", "jarvis"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned[:260]


def _extract_first_url(text: str) -> str:
    if len(text) > 1200:
        text = text[:1200]
    match = URL_RE.search(text)
    if not match:
        return ""
    raw = match.group(1).strip().rstrip(").,!?;:")
    if raw.lower().startswith("www."):
        raw = f"https://{raw}"
    return raw[:500]


def _is_read_only_voice_request(lowered: str, *, execute: bool, approve_privileged: bool) -> bool:
    if execute or approve_privileged:
        return False
    mutation_markers = [
        "pause jarvis",
        "pause daemon",
        "pause autopilot",
        "go idle",
        "stand down",
        "resume jarvis",
        "resume daemon",
        "resume autopilot",
        "safe mode on",
        "enable safe mode",
        "safe mode off",
        "disable safe mode",
        "auto gaming mode",
        "gaming mode on",
        "gaming mode off",
        "self heal",
        "self-heal",
        "repair yourself",
        "diagnose yourself",
        "sync mobile",
        "sync desktop",
        "cross-device sync",
        "sync devices",
        "send text",
        "send message",
        "ignore call",
        "decline call",
        "reject call",
        "place call",
        "make call",
        "dial ",
        "block likely spam",
        "automation run",
        "open website",
        "open webpage",
        "open page",
        "open url",
        "browse to",
        "go to ",
        "generate code",
        "generate image",
        "generate video",
        "generate 3d",
    ]
    if any(marker in lowered for marker in mutation_markers):
        return False
    read_only_markers = [
        "runtime status",
        "control status",
        "safe mode status",
        "gaming mode status",
        "gaming mode state",
        "what time",
        "time is it",
        "current time",
        "what date",
        "what day",
        "weather",
        "forecast",
        "search web",
        "search the web",
        "search internet",
        "search online",
        "look up",
        "lookup",
        "research ",
        "daily brief",
        "ops brief",
        "morning brief",
        "my brief",
        "brief me",
        "give me a brief",
        "run brief",
        "my schedule",
        "my calendar",
        "my meetings",
        "my agenda",
        "my tasks",
        "my todo",
        "my to-do",
        "what do you know",
        "what do you remember",
        "do you remember",
        "search memory",
        "what did i tell you",
        "what have i said",
        "knowledge status",
        "knowledge graph",
        "brain status",
        "memory status",
        "mission status",
        "system status",
        "jarvis status",
        "how are you",
        "status report",
        "health check",
        "are you working",
        "are you running",
    ]
    if any(marker in lowered for marker in read_only_markers):
        return True
    # Bare wake words or very short greetings (e.g. "jarvis", "hey jarvis")
    # are not state-mutating — treat as read-only so owner guard doesn't block them.
    stripped = lowered.strip()
    if stripped in ("jarvis", "hey jarvis", "hi jarvis", "hello jarvis", "ok jarvis", "a jarvis", "ay jarvis", "jarvis activate"):
        return True
    # Commands that don't match any mutation marker are conversational queries
    # routed to the LLM. These are read-only (no state changes) and should
    # not be blocked by owner guard. Only explicit mutation commands above
    # require authentication.
    return True


def cmd_open_web(url: str) -> int:
    result = _get_bus().dispatch(OpenWebCommand(url=url))
    if result.return_code != 0:
        print("error=No URL provided or invalid URL.")
        return result.return_code
    print(f"opened_url={result.opened_url}")
    return 0


def _cmd_daemon_run_impl(
    interval_s: int,
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    max_cycles: int,
    idle_interval_s: int,
    idle_after_s: int,
    run_missions: bool,
    sync_every_cycles: int = 5,
    self_heal_every_cycles: int = 20,
) -> int:
    """Implementation body for daemon-run (called by handler via callback)."""
    active_interval = max(30, interval_s)
    idle_interval = max(30, idle_interval_s)
    idle_after = max(60, idle_after_s)
    max_consecutive_failures = 10
    consecutive_failures = 0
    cycles = 0
    print("jarvis_daemon_started=true")
    print(f"active_interval_s={active_interval}")
    print(f"idle_interval_s={idle_interval}")
    print(f"idle_after_s={idle_after}")
    try:
        while True:
            cycles += 1
            idle_seconds = _windows_idle_seconds()
            is_active = True if idle_seconds is None else idle_seconds < idle_after
            sleep_seconds = active_interval if is_active else idle_interval
            gaming_state = _read_gaming_mode_state()
            control_state = read_control_state(repo_root())
            auto_detect = bool(gaming_state.get("auto_detect", False))
            auto_detect_hit = False
            detected_process = ""
            if auto_detect:
                auto_detect_hit, detected_process = _detect_active_game_process()
            gaming_mode_enabled = bool(gaming_state.get("enabled", False)) or auto_detect_hit
            daemon_paused = bool(control_state.get("daemon_paused", False))
            safe_mode = bool(control_state.get("safe_mode", False))
            print(f"cycle={cycles} ts={datetime.now(UTC).isoformat()}")
            print(f"daemon_paused={daemon_paused}")
            print(f"safe_mode={safe_mode}")
            print(f"gaming_mode={gaming_mode_enabled}")
            print(f"gaming_mode_auto_detect={auto_detect}")
            if detected_process:
                print(f"gaming_mode_detected_process={detected_process}")
            if gaming_state.get("reason", ""):
                print(f"gaming_mode_reason={gaming_state.get('reason', '')}")
            if control_state.get("reason", ""):
                print(f"runtime_control_reason={control_state.get('reason', '')}")
            print(f"device_active={is_active}")
            if idle_seconds is not None:
                print(f"idle_seconds={round(idle_seconds, 1)}")
            if daemon_paused:
                print("cycle_skipped=runtime_control_daemon_paused")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            if gaming_mode_enabled:
                print("cycle_skipped=gaming_mode_enabled")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            # --- Non-core subsystems: isolated so failures never affect circuit breaker ---
            if run_missions:
                try:
                    mission_rc = _run_next_pending_mission()
                except Exception as exc:  # noqa: BLE001
                    mission_rc = 2
                    print(f"mission_cycle_error={exc}")
                else:
                    print(f"mission_cycle_rc={mission_rc}")
            if sync_every_cycles > 0 and (cycles == 1 or cycles % sync_every_cycles == 0):
                try:
                    sync_rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
                except Exception as exc:  # noqa: BLE001
                    sync_rc = 2
                    print(f"sync_cycle_error={exc}")
                else:
                    print(f"sync_cycle_rc={sync_rc}")
            if self_heal_every_cycles > 0 and (cycles == 1 or cycles % self_heal_every_cycles == 0):
                try:
                    heal_rc = cmd_self_heal(
                        force_maintenance=False,
                        keep_recent=1800,
                        snapshot_note="daemon-self-heal",
                        as_json=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    heal_rc = 2
                    print(f"self_heal_cycle_error={exc}")
                else:
                    print(f"self_heal_cycle_rc={heal_rc}")
            # --- Core autopilot: only this drives the circuit breaker ---
            exec_cycle = execute and not safe_mode
            approve_cycle = approve_privileged and not safe_mode
            if safe_mode and (execute or approve_privileged):
                print("safe_mode_override=execute_and_privileged_flags_forced_false")
            try:
                rc = cmd_ops_autopilot(
                    snapshot_path=snapshot_path,
                    actions_path=actions_path,
                    execute=exec_cycle,
                    approve_privileged=approve_cycle,
                    auto_open_connectors=auto_open_connectors,
                )
            except Exception as exc:  # noqa: BLE001
                rc = 2
                print(f"cycle_error={exc}")
            print(f"cycle_rc={rc}")
            # Circuit breaker: only autopilot (rc) counts toward consecutive failures.
            # Mission, sync, and self-heal failures are logged but never trigger shutdown.
            if rc == 0:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                print(f"consecutive_failures={consecutive_failures}")
                if consecutive_failures >= max_consecutive_failures:
                    print("daemon_circuit_breaker_open=true")
                    return 3
            if max_cycles > 0 and cycles >= max_cycles:
                break
            print(f"sleep_s={sleep_seconds}")
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("jarvis_daemon_stopped=true")
    return 0


def cmd_daemon_run(
    interval_s: int,
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    max_cycles: int,
    idle_interval_s: int,
    idle_after_s: int,
    run_missions: bool,
    sync_every_cycles: int = 5,
    self_heal_every_cycles: int = 20,
) -> int:
    result = _get_bus().dispatch(DaemonRunCommand(
        interval_s=interval_s, snapshot_path=snapshot_path, actions_path=actions_path,
        execute=execute, approve_privileged=approve_privileged,
        auto_open_connectors=auto_open_connectors, max_cycles=max_cycles,
        idle_interval_s=idle_interval_s, idle_after_s=idle_after_s,
        run_missions=run_missions, sync_every_cycles=sync_every_cycles,
        self_heal_every_cycles=self_heal_every_cycles,
    ))
    return result.return_code


def cmd_voice_list() -> int:
    result = _get_bus().dispatch(VoiceListCommand())
    print("voices_windows:")
    if result.windows_voices:
        for name in result.windows_voices:
            print(f"- {name}")
    else:
        print("- none")

    print("voices_edge_en_gb:")
    if result.edge_voices:
        for name in result.edge_voices:
            print(f"- {name}")
    else:
        print("- none")
    return 0 if (result.windows_voices or result.edge_voices) else 1


def cmd_voice_say(
    text: str,
    profile: str,
    voice_pattern: str,
    output_wav: str,
    rate: int,
) -> int:
    result = _get_bus().dispatch(VoiceSayCommand(
        text=text, profile=profile, voice_pattern=voice_pattern,
        output_wav=output_wav, rate=rate,
    ))
    print(f"voice={result.voice_name}")
    if result.output_wav:
        print(f"wav={result.output_wav}")
    print(result.message)
    return 0


def cmd_voice_enroll(user_id: str, wav_path: str, replace: bool) -> int:
    result = _get_bus().dispatch(VoiceEnrollCommand(user_id=user_id, wav_path=wav_path, replace=replace))
    if result.message.startswith("error:"):
        print(result.message)
        return 2
    print(f"user_id={result.user_id}")
    print(f"profile_path={result.profile_path}")
    print(f"samples={result.samples}")
    print(result.message)
    return 0


def cmd_voice_verify(user_id: str, wav_path: str, threshold: float) -> int:
    result = _get_bus().dispatch(VoiceVerifyCommand(user_id=user_id, wav_path=wav_path, threshold=threshold))
    if result.message.startswith("error:"):
        print(result.message)
        return 2
    print(f"user_id={result.user_id}")
    print(f"score={result.score}")
    print(f"threshold={result.threshold}")
    print(f"matched={result.matched}")
    print(result.message)
    return 0 if result.matched else 2


def cmd_voice_listen(
    duration: float,
    language: str,
    model: str,
    execute: bool,
) -> int:
    """Record from microphone, transcribe, optionally execute as voice command."""
    result = _get_bus().dispatch(
        VoiceListenCommand(
            max_duration_seconds=duration,
            language=language,
            model_size=model,
        )
    )
    if result.message.startswith("error:"):
        print(result.message)
        return 2
    if not result.text:
        print("(no speech detected)")
        return 0
    print(f"transcription={result.text}")
    print(f"confidence={result.confidence}")
    print(f"duration={result.duration_seconds}s")
    if execute and result.text:
        print("executing transcribed command...")
        return cmd_voice_run(
            text=result.text,
            execute=True,
            approve_privileged=False,
            speak=False,
            snapshot_path=Path(repo_root() / ".planning" / "ops_snapshot.live.json"),
            actions_path=Path(repo_root() / ".planning" / "actions.generated.json"),
            voice_user="conner",
            voice_auth_wav="",
            voice_threshold=0.82,
            master_password="",
        )
    return 0


def _cmd_voice_run_impl(
    text: str,
    execute: bool,
    approve_privileged: bool,
    speak: bool,
    snapshot_path: Path,
    actions_path: Path,
    voice_user: str,
    voice_auth_wav: str,
    voice_threshold: float,
    master_password: str,
) -> int:
    """Implementation body for voice-run (called by handler via callback)."""
    lowered = text.lower().strip()
    intent = "unknown"
    rc = 1
    phone_queue = repo_root() / ".planning" / "phone_actions.jsonl"
    phone_report = repo_root() / ".planning" / "phone_spam_report.json"
    phone_call_log = Path(os.getenv("JARVIS_CALL_LOG_JSON", str(repo_root() / ".planning" / "phone_call_log.json")))
    owner_guard = read_owner_guard(repo_root())
    master_password_ok = False
    if master_password.strip():
        master_password_ok = verify_master_password(repo_root(), master_password.strip())
    read_only_request = _is_read_only_voice_request(
        lowered,
        execute=execute,
        approve_privileged=approve_privileged,
    )

    def _require_state_mutation_voice_auth() -> bool:
        if voice_auth_wav.strip() or master_password_ok:
            return True
        print("intent=voice_auth_required")
        print("reason=state_mutating_voice_actions_require_voice_auth_wav")
        if speak:
            cmd_voice_say(
                text="Voice authentication is required for state changing commands.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return False

    if bool(owner_guard.get("enabled", False)):
        expected_owner = str(owner_guard.get("owner_user_id", "")).strip().lower()
        incoming_owner = voice_user.strip().lower()
        if expected_owner and incoming_owner != expected_owner and not master_password_ok:
            print("intent=owner_guard_blocked")
            print("reason=voice_user_not_owner")
            if speak:
                cmd_voice_say(
                    text="Owner guard blocked this command.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2
        if not voice_auth_wav.strip() and not master_password_ok and not read_only_request:
            print("intent=owner_guard_blocked")
            print("reason=voice_auth_required_when_owner_guard_enabled")
            if speak:
                cmd_voice_say(
                    text="Owner guard requires voice authentication for state-changing commands.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2

    if (execute or approve_privileged) and not voice_auth_wav.strip() and not master_password_ok:
        print("intent=voice_auth_required")
        print("reason=execute_or_privileged_voice_actions_require_voice_auth_wav")
        if speak:
            cmd_voice_say(
                text="Voice authentication is required for executable commands.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return 2

    if voice_auth_wav.strip():
        verify_rc = cmd_voice_verify(
            user_id=voice_user,
            wav_path=voice_auth_wav,
            threshold=voice_threshold,
        )
        if verify_rc != 0:
            print("intent=voice_auth_failed")
            if speak:
                cmd_voice_say(
                    text="Voice authentication failed. Command blocked.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2

    if ("connect" in lowered or "setup" in lowered) and any(k in lowered for k in ["email", "calendar", "all", "everything"]):
        intent = "connect_bootstrap"
        rc = cmd_connect_bootstrap(auto_open=execute)
    elif any(
        k in lowered
        for k in ["pause jarvis", "pause daemon", "pause autopilot", "go idle", "stand down", "pause yourself"]
    ):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_pause"
        rc = cmd_runtime_control(
            pause=True,
            resume=False,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(
        k in lowered
        for k in ["resume jarvis", "resume daemon", "resume autopilot", "wake up", "start working again"]
    ):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_resume"
        rc = cmd_runtime_control(
            pause=False,
            resume=True,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["safe mode on", "enable safe mode"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_safe_on"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=True,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["safe mode off", "disable safe mode"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_safe_off"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=False,
            safe_off=True,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["runtime status", "control status", "safe mode status"]):
        intent = "runtime_status"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="",
        )
    elif "auto gaming mode" in lowered and any(k in lowered for k in ["on", "enable", "start"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_auto_enable"
        rc = cmd_gaming_mode(enable=None, reason="voice_command", auto_detect="on")
    elif "auto gaming mode" in lowered and any(k in lowered for k in ["off", "disable", "stop"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_auto_disable"
        rc = cmd_gaming_mode(enable=None, reason="voice_command", auto_detect="off")
    elif "gaming mode" in lowered and any(k in lowered for k in ["on", "enable", "start"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_enable"
        rc = cmd_gaming_mode(enable=True, reason="voice_command", auto_detect="")
    elif "gaming mode" in lowered and any(k in lowered for k in ["off", "disable", "stop"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_disable"
        rc = cmd_gaming_mode(enable=False, reason="voice_command", auto_detect="")
    elif "gaming mode" in lowered and any(k in lowered for k in ["status", "state"]):
        intent = "gaming_mode_status"
        rc = cmd_gaming_mode(enable=None, reason="", auto_detect="")
    elif "weather" in lowered or "forecast" in lowered:
        intent = "weather"
        rc = cmd_weather(location=_extract_weather_location(text))
    elif any(
        key in lowered
        for key in [
            "search the web for",
            "search web for",
            "search the internet for",
            "search online for",
            "web search",
            "find on the web",
        ]
    ):
        intent = "web_research"
        rc = cmd_web_research(
            query=_extract_web_query(text),
            max_results=8,
            max_pages=6,
            auto_ingest=True,
        )
    elif any(
        key in lowered
        for key in [
            "open website",
            "open webpage",
            "open page",
            "open url",
            "browse to",
            "go to ",
        ]
    ):
        intent = "open_web"
        if not execute:
            print("reason=Set --execute to open browser URLs.")
            return 2
        url = _extract_first_url(text)
        if not url:
            print("reason=No valid URL found. Include full URL like https://example.com")
            return 2
        rc = cmd_open_web(url)
    elif any(key in lowered for key in ["sync mobile", "sync desktop", "cross-device sync", "sync devices"]):
        intent = "mobile_desktop_sync"
        rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
    elif any(key in lowered for key in ["self heal", "self-heal", "repair yourself", "diagnose yourself"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "self_heal"
        rc = cmd_self_heal(
            force_maintenance=False,
            keep_recent=1800,
            snapshot_note="voice-self-heal",
            as_json=False,
        )
    elif any(
        k in lowered
        for k in [
            "organize my day",
            "run autopilot",
            "daily autopilot",
            "plan my day",
            "plan today",
            "organize today",
            "help me prioritize",
        ]
    ):
        intent = "ops_autopilot"
        rc = cmd_ops_autopilot(
            snapshot_path=snapshot_path,
            actions_path=actions_path,
            execute=execute,
            approve_privileged=approve_privileged,
            auto_open_connectors=execute,
        )
    elif (
        ("block" in lowered and "spam" in lowered and "call" in lowered)
        or ("stop" in lowered and "scam" in lowered and "call" in lowered)
        or ("handle" in lowered and "spam" in lowered and "calls" in lowered)
    ):
        intent = "phone_spam_guard"
        rc = cmd_phone_spam_guard(
            call_log_path=phone_call_log,
            report_path=phone_report,
            queue_path=phone_queue,
            threshold=0.65,
            queue_actions=execute,
        )
    elif any(k in lowered for k in ["send text", "send message", "send a text", "send a message", "text to ", "message to "]):
        number = _extract_first_phone_number(text)
        intent = "phone_send_sms"
        if not number:
            print("intent=phone_send_sms")
            print("reason=No phone number found in voice command.")
            return 2
        # Extract SMS body: strip trigger phrase and number, use remainder
        sms_body = text
        for _trigger in ["send a text to", "send a message to", "send text to", "send message to", "text to", "message to"]:
            if _trigger in lowered:
                sms_body = text[lowered.index(_trigger) + len(_trigger):].strip()
                break
        # Remove the phone number from the body if present
        if number in sms_body:
            sms_body = sms_body.replace(number, "", 1).strip()
        # Fall back to colon-delimited body
        if not sms_body and ":" in text:
            sms_body = text.split(":", 1)[1].strip()
        if not sms_body:
            sms_body = text
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="send_sms",
            number=number,
            message=sms_body,
            queue_path=phone_queue,
        )
    elif any(k in lowered for k in ["ignore call", "decline call", "reject call"]):
        number = _extract_first_phone_number(text)
        intent = "phone_ignore_call"
        if not number:
            print("intent=phone_ignore_call")
            print("reason=No phone number found in voice command.")
            return 2
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="ignore_call",
            number=number,
            message="",
            queue_path=phone_queue,
        )
    elif (lowered.startswith("call ") or "place a call" in lowered or "make a call" in lowered or "phone call" in lowered):
        number = _extract_first_phone_number(text)
        intent = "phone_place_call"
        if not number:
            # No phone number found — don't queue a call to nobody
            print("intent=phone_place_call")
            print("reason=No phone number found in voice command.")
            return 2
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="place_call",
            number=number,
            message="",
            queue_path=phone_queue,
        )
    elif ("sync" in lowered) and any(k in lowered for k in ["calendar", "email", "inbox", "ops"]):
        intent = "ops_sync"
        live_snapshot = snapshot_path.with_name("ops_snapshot.live.json")
        rc = cmd_ops_sync(live_snapshot)
    elif any(k in lowered for k in ["daily brief", "ops brief", "morning brief", "give me a brief", "my brief", "run brief", "brief me"]):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    elif "automation" in lowered and any(k in lowered for k in ["run", "execute", "start"]):
        intent = "automation_run"
        rc = cmd_automation_run(
            actions_path=actions_path,
            approve_privileged=approve_privileged,
            execute=execute,
        )
    elif "generate code" in lowered:
        intent = "generate_code"
        prompt = text.split("generate code", 1)[1].strip() if "generate code" in lowered else text
        prompt = prompt or "Generate high-quality production code for the requested task."
        rc = cmd_run_task(
            task_type="code",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate image" in lowered:
        intent = "generate_image"
        prompt = text.split("generate image", 1)[1].strip() if "generate image" in lowered else text
        prompt = prompt or "Generate a high-quality concept image."
        rc = cmd_run_task(
            task_type="image",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate video" in lowered:
        intent = "generate_video"
        prompt = text.split("generate video", 1)[1].strip() if "generate video" in lowered else text
        prompt = prompt or "Generate a high-quality short cinematic video."
        rc = cmd_run_task(
            task_type="video",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate 3d" in lowered or "generate a 3d model" in lowered or "generate 3d model" in lowered:
        intent = "generate_model3d"
        rc = cmd_run_task(
            task_type="model3d",
            prompt=text,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    # --- Schedule / calendar / meeting queries ---
    elif any(
        k in lowered
        for k in [
            "my schedule",
            "my calendar",
            "my meetings",
            "my agenda",
            "what's on today",
            "what is on today",
            "what do i have today",
            "what's happening today",
            "what is happening today",
            "today's schedule",
            "today's meetings",
            "upcoming meetings",
            "upcoming events",
            "next meeting",
            "next appointment",
            "daily briefing",
            "morning briefing",
            "give me a briefing",
            "give me my briefing",
        ]
    ):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    # --- Task queries ---
    elif any(
        k in lowered
        for k in [
            "my tasks",
            "my to-do",
            "my todo",
            "what are my tasks",
            "task list",
            "pending tasks",
            "open tasks",
            "what do i need to do",
            "what should i do",
            "what needs to be done",
        ]
    ):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    # --- Memory search / knowledge queries ---
    elif any(
        k in lowered
        for k in [
            "what do you know about",
            "what do you remember about",
            "do you remember when",
            "do you remember that",
            "do you remember my",
            "search memory for",
            "search your memory for",
            "search your memory about",
            "what did i tell you about",
            "what have i said about",
        ]
    ):
        intent = "brain_context"
        # Extract the query portion after the trigger phrase (longest-first to avoid partial matches)
        _memory_triggers = [
            "what do you remember about",
            "what do you know about",
            "search your memory about",
            "search your memory for",
            "what did i tell you about",
            "what have i said about",
            "do you remember when",
            "do you remember that",
            "do you remember my",
            "search memory for",
        ]
        query_text = text
        for trigger in _memory_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                query_text = text[idx:].strip().rstrip("?").strip()
                break
        if not query_text:
            query_text = text
        rc = cmd_brain_context(query=query_text, max_items=5, max_chars=1200, as_json=False)
    # --- Memory save / remember ---
    elif any(
        k in lowered
        for k in [
            "remember that",
            "remember this",
            "save this",
            "make a note",
            "take a note",
            "note that",
            "don't forget",
        ]
    ):
        intent = "memory_ingest"
        # Extract content after the trigger phrase (include both colon and non-colon variants)
        content = text
        _remember_triggers = [
            "remember that",
            "remember this:",
            "remember this",
            "save this:",
            "save this",
            "make a note:",
            "make a note that",
            "make a note",
            "take a note:",
            "take a note that",
            "take a note",
            "note that",
            "don't forget that",
            "don't forget",
        ]
        for trigger in _remember_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                content = text[idx:].strip()
                break
        if not content:
            content = text
        rc = cmd_ingest(
            source="user",
            kind="episodic",
            task_id=f"voice-remember-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            content=content,
        )
    # --- Knowledge graph queries ---
    elif any(
        k in lowered
        for k in [
            "knowledge status",
            "knowledge graph",
            "how much do you know",
            "brain status",
            "memory status",
        ]
    ):
        intent = "brain_status"
        rc = cmd_brain_status(as_json=False)
    # --- Mission / learning queries ---
    elif any(
        k in lowered
        for k in [
            "mission status",
            "learning mission",
            "active missions",
            "my missions",
        ]
    ):
        intent = "mission_status"
        rc = cmd_mission_status(last=5)
    # --- System status ---
    elif any(
        k in lowered
        for k in [
            "system status",
            "jarvis status",
            "how are you",
            "status report",
            "health check",
            "are you working",
            "are you running",
        ]
    ):
        intent = "system_status"
        rc = cmd_status()
    else:
        # No keyword match -- route through LLM for a conversational response.
        intent = "llm_conversation"
        # Build memory context so the LLM knows about the user
        context_lines: list[str] = []
        try:
            packet = build_context_packet(repo_root(), query=text, max_items=5, max_chars=1200)
            selected = packet.get("selected", [])
            if isinstance(selected, list):
                for row in selected:
                    if not isinstance(row, dict):
                        continue
                    summary = str(row.get("summary", "")).strip()
                    if summary:
                        context_lines.append(summary)
        except Exception:
            pass
        persona = load_persona_config(repo_root())
        persona_desc = ""
        if persona.enabled:
            persona_desc = (
                "You are Jarvis, an intelligent personal AI assistant. "
                "You are witty, knowledgeable, and speak like a refined British butler "
                "with dry humor. Keep responses concise and natural. "
                "Never repeat the same phrases. Vary your language."
            )
        else:
            persona_desc = "You are Jarvis, a helpful personal AI assistant. Keep responses concise."
        system_prompt = persona_desc
        if context_lines:
            system_prompt += "\n\nRelevant memories about the user:\n" + "\n".join(f"- {line}" for line in context_lines[:5])
        # Pick best available cloud model, fall back to local
        from jarvis_engine.gateway.models import CLOUD_MODEL_MAP
        _llm_model: str | None = None
        for _env_key, _model_alias in [
            ("GROQ_API_KEY", "kimi-k2"),
            ("MISTRAL_API_KEY", "devstral-2"),
            ("ZAI_API_KEY", "glm-4.7-flash"),
        ]:
            if os.environ.get(_env_key, ""):
                _llm_model = _model_alias
                break
        if _llm_model is None:
            _llm_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
        try:
            result: QueryResult = _get_bus().dispatch(QueryCommand(
                query=text,
                system_prompt=system_prompt,
                max_tokens=512,
                model=_llm_model,
            ))
            if result.return_code != 0:
                print(f"intent=llm_unavailable")
                print(f"reason={result.text.strip() or 'LLM gateway not available.'}")
                rc = 1
            elif result.text.strip():
                print(f"response={result.text.strip()}")
                print(f"model={result.model}")
                print(f"provider={result.provider}")
                if speak:
                    cmd_voice_say(
                        text=result.text.strip(),
                        profile="jarvis_like",
                        voice_pattern="",
                        output_wav="",
                        rate=-1,
                    )
                rc = 0
            else:
                print("intent=llm_empty_response")
                print("reason=LLM returned empty response.")
                rc = 1
        except Exception as exc:
            print(f"intent=llm_error")
            print(f"reason={exc}")
            if speak:
                cmd_voice_say(
                    text="I'm having trouble connecting to my language model. Please try again.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            rc = 1

    print(f"intent={intent}")
    print(f"status_code={rc}")
    try:
        auto_id = _auto_ingest_memory(
            source="user",
            kind="episodic",
            task_id=f"voice-{intent}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            content=(
                f"Voice command accepted. intent={intent}; status_code={rc}; execute={execute}; "
                f"approve_privileged={approve_privileged}; voice_user={voice_user}; text={text[:500]}"
            ),
        )
        if auto_id:
            print(f"auto_ingest_record_id={auto_id}")
    except Exception:
        pass
    if speak:
        persona = load_persona_config(repo_root())
        persona_line = compose_persona_reply(
            persona,
            intent=intent,
            success=(rc == 0),
            reason="" if rc == 0 else "failed or requires approval",
        )
        cmd_voice_say(
            text=persona_line,
            profile="jarvis_like",
            voice_pattern="",
            output_wav="",
            rate=-1,
        )
    return rc


def cmd_voice_run(
    text: str,
    execute: bool,
    approve_privileged: bool,
    speak: bool,
    snapshot_path: Path,
    actions_path: Path,
    voice_user: str,
    voice_auth_wav: str,
    voice_threshold: float,
    master_password: str,
) -> int:
    result = _get_bus().dispatch(VoiceRunCommand(
        text=text, execute=execute, approve_privileged=approve_privileged,
        speak=speak, snapshot_path=snapshot_path, actions_path=actions_path,
        voice_user=voice_user, voice_auth_wav=voice_auth_wav,
        voice_threshold=voice_threshold, master_password=master_password,
    ))
    return result.return_code


def cmd_proactive_check(snapshot_path: str) -> int:
    result = _get_bus().dispatch(ProactiveCheckCommand(snapshot_path=snapshot_path))
    print(f"alerts_fired={result.alerts_fired}")
    if result.alerts_fired:
        try:
            alerts = json.loads(result.alerts)
        except (json.JSONDecodeError, TypeError):
            alerts = []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            print(f"  [{a.get('rule_id', '?')}] {a.get('message', '')}")
    print(f"message={result.message}")
    return 0


def cmd_wake_word(threshold: float) -> int:
    result = _get_bus().dispatch(WakeWordStartCommand(threshold=threshold))
    print(f"started={result.started}")
    print(f"message={result.message}")
    if result.started:
        # Block until interrupted
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Wake word detection stopped.")
    return 0


def cmd_cost_reduction(days: int) -> int:
    result = _get_bus().dispatch(CostReductionCommand(days=days))
    print(f"local_pct={result.local_pct}")
    print(f"cloud_cost_usd={result.cloud_cost_usd}")
    print(f"trend={result.trend}")
    print(f"message={result.message}")
    return 0


def cmd_self_test(threshold: float) -> int:
    result = _get_bus().dispatch(SelfTestCommand(score_threshold=threshold))
    print(f"average_score={result.average_score:.4f}")
    print(f"tasks_run={result.tasks_run}")
    print(f"regression_detected={result.regression_detected}")
    for task_score in result.per_task_scores:
        print(f"  task={task_score.get('task_id', '?')} score={task_score.get('score', 0.0):.4f}")
    print(f"message={result.message}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis engine bootstrap CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show engine bootstrap status.")

    p_log = sub.add_parser("log", help="Append an event to memory log.")
    p_log.add_argument("--type", required=True, help="Event type label.")
    p_log.add_argument("--message", required=True, help="Event description.")

    p_ingest = sub.add_parser("ingest", help="Append structured memory from a source.")
    p_ingest.add_argument(
        "--source",
        required=True,
        choices=["user", "claude", "opus", "gemini", "task_outcome"],
    )
    p_ingest.add_argument(
        "--kind",
        required=True,
        choices=["episodic", "semantic", "procedural"],
    )
    p_ingest.add_argument("--task-id", required=True, help="Task/session id.")
    p_ingest.add_argument("--content", required=True, help="Memory content.")

    p_mobile = sub.add_parser("serve-mobile", help="Run secure mobile ingestion API.")
    p_mobile.add_argument("--host", default="127.0.0.1")
    p_mobile.add_argument("--port", type=int, default=8787)
    p_mobile.add_argument("--token", help="Shared token. Falls back to JARVIS_MOBILE_TOKEN env var.")
    p_mobile.add_argument(
        "--signing-key",
        help="HMAC signing key. Falls back to JARVIS_MOBILE_SIGNING_KEY env var.",
    )
    p_mobile.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help="Allow non-loopback HTTP bind (for trusted LAN). Falls back to JARVIS_ALLOW_INSECURE_MOBILE_BIND env var.",
    )

    p_route = sub.add_parser("route", help="Get a route decision.")
    p_route.add_argument("--risk", default="low", choices=["low", "medium", "high", "critical"])
    p_route.add_argument(
        "--complexity",
        default="normal",
        choices=["easy", "normal", "hard", "very_hard"],
    )

    p_growth_eval = sub.add_parser("growth-eval", help="Run golden-task model growth evaluation.")
    p_growth_eval.add_argument("--model", required=True, help="Ollama model id.")
    p_growth_eval.add_argument("--endpoint", default="http://127.0.0.1:11434")
    p_growth_eval.add_argument(
        "--tasks-path",
        default=str(repo_root() / ".planning" / "golden_tasks.json"),
    )
    p_growth_eval.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_eval.add_argument("--num-predict", type=int, default=256)
    p_growth_eval.add_argument("--temperature", type=float, default=0.0)
    p_growth_eval.add_argument("--timeout-s", type=int, default=120)
    p_growth_eval.add_argument(
        "--accept-thinking",
        action="store_true",
        help="Allow scoring from thinking text when final response is empty.",
    )
    p_growth_eval.add_argument(
        "--think",
        choices=["auto", "on", "off"],
        default="auto",
        help="Set thinking mode for supported models.",
    )

    p_growth_report = sub.add_parser("growth-report", help="Show growth trend from eval history.")
    p_growth_report.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_report.add_argument("--last", type=int, default=10)

    p_growth_audit = sub.add_parser("growth-audit", help="Show auditable prompt/response evidence.")
    p_growth_audit.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_audit.add_argument(
        "--run-index",
        type=int,
        default=-1,
        help="Python-style index. -1 means latest run.",
    )

    p_intelligence = sub.add_parser(
        "intelligence-dashboard",
        help="Build intelligence ranking/ETA dashboard from local growth history.",
    )
    p_intelligence.add_argument("--last-runs", type=int, default=20)
    p_intelligence.add_argument("--output-path", default=str(repo_root() / ".planning" / "intelligence_dashboard.json"))
    p_intelligence.add_argument("--json", action="store_true", help="Print full JSON payload.")

    p_brain_status = sub.add_parser("brain-status", help="Show high-level brain memory branch stats.")
    p_brain_status.add_argument("--json", action="store_true")

    p_brain_context = sub.add_parser(
        "brain-context",
        help="Build compact context packet from long-term brain memory.",
    )
    p_brain_context.add_argument("--query", required=True)
    p_brain_context.add_argument("--max-items", type=int, default=10)
    p_brain_context.add_argument("--max-chars", type=int, default=2400)
    p_brain_context.add_argument("--json", action="store_true")

    p_brain_compact = sub.add_parser("brain-compact", help="Compact old brain records into summary groups.")
    p_brain_compact.add_argument("--keep-recent", type=int, default=1800)
    p_brain_compact.add_argument("--json", action="store_true")

    p_brain_regression = sub.add_parser("brain-regression", help="Run anti-regression health checks for brain memory.")
    p_brain_regression.add_argument("--json", action="store_true")

    p_kg_status = sub.add_parser("knowledge-status", help="Show knowledge graph node/edge/locked/contradiction counts.")
    p_kg_status.add_argument("--json", action="store_true")

    p_clist = sub.add_parser("contradiction-list", help="List knowledge graph contradictions.")
    p_clist.add_argument("--status", default="pending", help="Filter by status (pending, resolved, or empty for all).")
    p_clist.add_argument("--limit", type=int, default=20)
    p_clist.add_argument("--json", action="store_true")

    p_cresolve = sub.add_parser("contradiction-resolve", help="Resolve a knowledge graph contradiction.")
    p_cresolve.add_argument("contradiction_id", type=int, help="Contradiction ID to resolve.")
    p_cresolve.add_argument("--resolution", required=True, choices=["accept_new", "keep_old", "merge"])
    p_cresolve.add_argument("--merge-value", default="", help="Merged value (required for merge resolution).")

    p_flock = sub.add_parser("fact-lock", help="Lock or unlock a knowledge graph fact node.")
    p_flock.add_argument("node_id", help="Node ID to lock or unlock.")
    p_flock.add_argument("--action", default="lock", choices=["lock", "unlock"])

    p_kg_regression = sub.add_parser("knowledge-regression", help="Run knowledge graph regression check.")
    p_kg_regression.add_argument("--snapshot", default="", help="Path to previous snapshot metadata JSON.")
    p_kg_regression.add_argument("--json", action="store_true")

    p_snapshot = sub.add_parser("memory-snapshot", help="Create or verify signed memory snapshot.")
    p_snapshot_group = p_snapshot.add_mutually_exclusive_group(required=True)
    p_snapshot_group.add_argument("--create", action="store_true")
    p_snapshot_group.add_argument("--verify-path")
    p_snapshot.add_argument("--note", default="")

    p_maintenance = sub.add_parser("memory-maintenance", help="Run compact + regression + signed snapshot maintenance.")
    p_maintenance.add_argument("--keep-recent", type=int, default=1800)
    p_maintenance.add_argument("--snapshot-note", default="nightly")

    p_web_research = sub.add_parser("web-research", help="Search the public web and summarize findings with source links.")
    p_web_research.add_argument("--query", required=True)
    p_web_research.add_argument("--max-results", type=int, default=8)
    p_web_research.add_argument("--max-pages", type=int, default=6)
    p_web_research.add_argument("--no-ingest", action="store_true")

    p_sync = sub.add_parser("mobile-desktop-sync", help="Run cross-device state checks and write sync report.")
    p_sync.add_argument("--json", action="store_true")
    p_sync.add_argument("--no-ingest", action="store_true")

    p_self_heal = sub.add_parser("self-heal", help="Run Jarvis self-healing checks and safe repairs.")
    p_self_heal.add_argument("--force-maintenance", action="store_true")
    p_self_heal.add_argument("--keep-recent", type=int, default=1800)
    p_self_heal.add_argument("--snapshot-note", default="self-heal")
    p_self_heal.add_argument("--json", action="store_true")

    p_persona = sub.add_parser("persona-config", help="Configure Jarvis personality response style.")
    p_persona.add_argument("--enable", action="store_true")
    p_persona.add_argument("--disable", action="store_true")
    p_persona.add_argument("--humor-level", type=int)
    p_persona.add_argument("--mode", default="")
    p_persona.add_argument("--style", default="")

    sub.add_parser("migrate-memory", help="Migrate JSONL/JSON memory data into SQLite (one-time).")

    sub.add_parser("desktop-widget", help="Launch desktop-native Jarvis widget window.")

    p_run_task = sub.add_parser("run-task", help="Run multimodal Jarvis task.")
    p_run_task.add_argument("--type", required=True, choices=["image", "code", "video", "model3d"])
    p_run_task.add_argument("--prompt", required=True)
    p_run_task.add_argument("--execute", action="store_true", help="Execute instead of dry-run plan.")
    p_run_task.add_argument(
        "--approve-privileged",
        action="store_true",
        help="Allow privileged task classes (video/3d).",
    )
    p_run_task.add_argument("--model", default="qwen3-coder:30b")
    p_run_task.add_argument("--endpoint", default="http://127.0.0.1:11434")
    p_run_task.add_argument(
        "--quality-profile",
        default="max_quality",
        choices=["max_quality", "balanced", "fast"],
    )
    p_run_task.add_argument("--output-path")

    p_ops_brief = sub.add_parser("ops-brief", help="Generate daily life operations brief.")
    p_ops_brief.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_brief.add_argument("--output-path")

    p_ops_actions = sub.add_parser("ops-export-actions", help="Export suggested actions from ops snapshot.")
    p_ops_actions.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_actions.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )

    p_ops_sync = sub.add_parser("ops-sync", help="Build live operations snapshot from connectors.")
    p_ops_sync.add_argument(
        "--output-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )

    p_ops_autopilot = sub.add_parser("ops-autopilot", help="Run connector check, sync, brief, action export, and automation.")
    p_ops_autopilot.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_autopilot.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_ops_autopilot.add_argument("--execute", action="store_true")
    p_ops_autopilot.add_argument("--approve-privileged", action="store_true")
    p_ops_autopilot.add_argument("--auto-open-connectors", action="store_true")

    p_daemon = sub.add_parser("daemon-run", help="Run Jarvis autopilot loop continuously.")
    p_daemon.add_argument("--interval-s", type=int, default=180)
    p_daemon.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_daemon.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_daemon.add_argument("--execute", action="store_true")
    p_daemon.add_argument("--approve-privileged", action="store_true")
    p_daemon.add_argument("--auto-open-connectors", action="store_true")
    p_daemon.add_argument("--idle-interval-s", type=int, default=900)
    p_daemon.add_argument("--idle-after-s", type=int, default=300)
    p_daemon.add_argument("--max-cycles", type=int, default=0, help="For testing; 0 means run forever.")
    p_daemon.add_argument("--skip-missions", action="store_true", help="Disable background learning mission execution.")
    p_daemon.add_argument("--sync-every-cycles", type=int, default=5)
    p_daemon.add_argument("--self-heal-every-cycles", type=int, default=20)

    p_mission_create = sub.add_parser("mission-create", help="Create a learning mission.")
    p_mission_create.add_argument("--topic", required=True)
    p_mission_create.add_argument("--objective", default="")
    p_mission_create.add_argument(
        "--source",
        action="append",
        default=[],
        help="Learning source profile (repeatable), e.g. google, reddit, official_docs",
    )

    p_mission_status = sub.add_parser("mission-status", help="Show recent learning missions.")
    p_mission_status.add_argument("--last", type=int, default=10)

    p_mission_run = sub.add_parser("mission-run", help="Run one learning mission with source verification.")
    p_mission_run.add_argument("--id", required=True, help="Mission id from mission-create.")
    p_mission_run.add_argument("--max-results", type=int, default=8)
    p_mission_run.add_argument("--max-pages", type=int, default=12)
    p_mission_run.add_argument("--no-ingest", action="store_true", help="Do not ingest verified findings.")

    p_runtime = sub.add_parser("runtime-control", help="Pause/resume daemon and toggle safe mode.")
    p_runtime_group = p_runtime.add_mutually_exclusive_group()
    p_runtime_group.add_argument("--pause", action="store_true")
    p_runtime_group.add_argument("--resume", action="store_true")
    p_runtime_group.add_argument("--reset", action="store_true")
    p_runtime.add_argument("--safe-on", action="store_true")
    p_runtime.add_argument("--safe-off", action="store_true")
    p_runtime.add_argument("--reason", default="")

    p_owner = sub.add_parser("owner-guard", help="Lock Jarvis to owner voice and trusted mobile devices.")
    p_owner.add_argument("--enable", action="store_true")
    p_owner.add_argument("--disable", action="store_true")
    p_owner.add_argument("--owner-user", default="")
    p_owner.add_argument("--trust-device", default="")
    p_owner.add_argument("--revoke-device", default="")
    p_owner.add_argument(
        "--set-master-password", default="",
        help="DEPRECATED: use JARVIS_MASTER_PASSWORD env var instead. "
             "CLI passwords are visible in process listings.",
    )
    p_owner.add_argument("--clear-master-password", action="store_true")

    p_gaming = sub.add_parser("gaming-mode", help="Enable/disable low-impact mode for gaming sessions.")
    p_gaming_group = p_gaming.add_mutually_exclusive_group()
    p_gaming_group.add_argument("--enable", action="store_true")
    p_gaming_group.add_argument("--disable", action="store_true")
    p_gaming.add_argument("--auto-detect", choices=["on", "off"], default="")
    p_gaming.add_argument("--reason", default="")

    p_automation = sub.add_parser("automation-run", help="Run planned actions with capability gates.")
    p_automation.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_automation.add_argument(
        "--approve-privileged",
        action="store_true",
        help="Required to execute privileged actions.",
    )
    p_automation.add_argument(
        "--execute",
        action="store_true",
        help="Execute commands (default is dry-run).",
    )

    sub.add_parser("connect-status", help="Show connector readiness and prompts.")

    p_connect_grant = sub.add_parser("connect-grant", help="Grant connector permission.")
    p_connect_grant.add_argument("--id", required=True, help="Connector id (for example: email, calendar).")
    p_connect_grant.add_argument("--scope", action="append", default=[], help="Optional scope (repeatable).")

    p_connect_bootstrap = sub.add_parser("connect-bootstrap", help="Show connector prompts and optionally open setup links.")
    p_connect_bootstrap.add_argument("--auto-open", action="store_true", help="Open tap URLs in browser.")

    p_phone_action = sub.add_parser("phone-action", help="Queue phone action (send SMS/place call/ignore/block).")
    p_phone_action.add_argument("--action", required=True, choices=["send_sms", "place_call", "ignore_call", "block_number", "silence_unknown_callers"])
    p_phone_action.add_argument("--number", default="")
    p_phone_action.add_argument("--message", default="")
    p_phone_action.add_argument(
        "--queue-path",
        default=str(repo_root() / ".planning" / "phone_actions.jsonl"),
    )

    p_phone_spam = sub.add_parser("phone-spam-guard", help="Analyze call logs and queue spam-block actions.")
    p_phone_spam.add_argument(
        "--call-log-path",
        default=str(repo_root() / ".planning" / "phone_call_log.json"),
    )
    p_phone_spam.add_argument(
        "--report-path",
        default=str(repo_root() / ".planning" / "phone_spam_report.json"),
    )
    p_phone_spam.add_argument(
        "--queue-path",
        default=str(repo_root() / ".planning" / "phone_actions.jsonl"),
    )
    p_phone_spam.add_argument("--threshold", type=float, default=0.65)

    sub.add_parser("voice-list", help="List available local Windows voices.")

    p_voice = sub.add_parser("voice-say", help="Speak text with local Windows voice synthesis.")
    p_voice.add_argument("--text", required=True)
    p_voice.add_argument("--profile", default="jarvis_like", choices=["jarvis_like", "default"])
    p_voice.add_argument("--voice-pattern", default="")
    p_voice.add_argument("--output-wav", default="")
    p_voice.add_argument("--rate", type=int, default=-1)

    p_voice_enroll = sub.add_parser("voice-enroll", help="Enroll a user voiceprint from WAV.")
    p_voice_enroll.add_argument("--user-id", required=True, help="Identity label, e.g. conner.")
    p_voice_enroll.add_argument("--wav", required=True, help="Path to WAV sample of your voice.")
    p_voice_enroll.add_argument("--replace", action="store_true", help="Replace existing profile.")

    p_voice_verify = sub.add_parser("voice-verify", help="Verify WAV sample against enrolled voiceprint.")
    p_voice_verify.add_argument("--user-id", required=True)
    p_voice_verify.add_argument("--wav", required=True)
    p_voice_verify.add_argument("--threshold", type=float, default=0.82)

    p_voice_run = sub.add_parser("voice-run", help="Run a voice/text command through intent mapping.")
    p_voice_run.add_argument("--text", required=True)
    p_voice_run.add_argument("--execute", action="store_true")
    p_voice_run.add_argument("--approve-privileged", action="store_true")
    p_voice_run.add_argument("--speak", action="store_true", help="Speak completion status.")
    p_voice_run.add_argument("--voice-user", default="conner")
    p_voice_run.add_argument("--voice-auth-wav", default="", help="Optional WAV path for voice authentication.")
    p_voice_run.add_argument("--voice-threshold", type=float, default=0.82)
    p_voice_run.add_argument(
        "--master-password", default="",
        help="DEPRECATED: use JARVIS_MASTER_PASSWORD env var instead. "
             "CLI passwords are visible in process listings.",
    )
    p_voice_run.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_voice_run.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )

    p_voice_listen = sub.add_parser("voice-listen", help="Record from microphone and transcribe speech-to-text.")
    p_voice_listen.add_argument("--duration", type=float, default=30.0, help="Max recording duration in seconds.")
    p_voice_listen.add_argument("--language", default="en", help="Language code hint for transcription.")
    p_voice_listen.add_argument("--model", default="small.en", help="Whisper model size (e.g. tiny.en, small.en, medium).")
    p_voice_listen.add_argument("--execute", action="store_true", help="Execute transcribed text as a voice command.")

    # -- Harvesting --
    p_harvest = sub.add_parser("harvest", help="Harvest knowledge about a topic from external AI sources.")
    p_harvest.add_argument("--topic", required=True, help="Topic to harvest knowledge about.")
    p_harvest.add_argument("--providers", default=None, help="Comma-separated list of providers (default: all available).")
    p_harvest.add_argument("--max-tokens", type=int, default=2048, help="Max tokens per provider response.")

    p_ingest_session = sub.add_parser("ingest-session", help="Ingest knowledge from Claude Code or Codex session files.")
    p_ingest_session.add_argument("--source", required=True, choices=["claude", "codex"], help="Session source type.")
    p_ingest_session.add_argument("--session-path", default=None, help="Specific session file path (optional).")
    p_ingest_session.add_argument("--project-path", default=None, help="Claude Code project path to scope search (optional).")

    p_harvest_budget = sub.add_parser("harvest-budget", help="View or set harvest budget limits.")
    p_harvest_budget.add_argument("--action", default="status", choices=["status", "set"], help="Budget action.")
    p_harvest_budget.add_argument("--provider", default=None, help="Provider name.")
    p_harvest_budget.add_argument("--period", default=None, choices=["daily", "monthly"], help="Budget period.")
    p_harvest_budget.add_argument("--limit-usd", type=float, default=None, help="USD limit.")
    p_harvest_budget.add_argument("--limit-requests", type=int, default=None, help="Request count limit.")

    # -- Learning --
    p_learn = sub.add_parser("learn", help="Manually trigger learning from text input.")
    p_learn.add_argument("--user-message", required=True, help="User message text.")
    p_learn.add_argument("--assistant-response", required=True, help="Assistant response text.")

    p_cbq = sub.add_parser("cross-branch-query", help="Query across knowledge branches.")
    p_cbq.add_argument("query", help="Natural language query.")
    p_cbq.add_argument("--k", type=int, default=10, help="Max results to return.")

    sub.add_parser("flag-expired", help="Flag expired knowledge graph facts.")

    sub.add_parser("memory-eval", help="Run memory-recall golden task evaluation.")

    p_proactive = sub.add_parser("proactive-check", help="Manually trigger proactive evaluation.")
    p_proactive.add_argument("--snapshot-path", default="", help="Path to ops snapshot JSON.")

    p_wakeword = sub.add_parser("wake-word", help="Start wake word detection (blocking).")
    p_wakeword.add_argument("--threshold", type=float, default=0.5, help="Detection threshold.")

    p_cost_red = sub.add_parser("cost-reduction", help="Show local vs cloud query ratio and trend.")
    p_cost_red.add_argument("--days", type=int, default=30, help="Number of days to look back.")

    p_selftest = sub.add_parser("self-test", help="Run adversarial memory quiz.")
    p_selftest.add_argument("--threshold", type=float, default=0.5, help="Score threshold for alerts.")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status()
    if args.command == "log":
        return cmd_log(event_type=args.type, message=args.message)
    if args.command == "ingest":
        return cmd_ingest(
            source=args.source,
            kind=args.kind,
            task_id=args.task_id,
            content=args.content,
        )
    if args.command == "serve-mobile":
        return cmd_serve_mobile(
            host=args.host,
            port=args.port,
            token=args.token,
            signing_key=args.signing_key,
            allow_insecure_bind=args.allow_insecure_bind,
        )
    if args.command == "route":
        return cmd_route(risk=args.risk, complexity=args.complexity)
    if args.command == "growth-eval":
        think_opt = None
        if args.think == "on":
            think_opt = True
        elif args.think == "off":
            think_opt = False
        return cmd_growth_eval(
            model=args.model,
            endpoint=args.endpoint,
            tasks_path=Path(args.tasks_path),
            history_path=Path(args.history_path),
            num_predict=args.num_predict,
            temperature=args.temperature,
            think=think_opt,
            accept_thinking=args.accept_thinking,
            timeout_s=args.timeout_s,
        )
    if args.command == "growth-report":
        return cmd_growth_report(
            history_path=Path(args.history_path),
            last=args.last,
        )
    if args.command == "growth-audit":
        return cmd_growth_audit(
            history_path=Path(args.history_path),
            run_index=args.run_index,
        )
    if args.command == "intelligence-dashboard":
        return cmd_intelligence_dashboard(
            last_runs=args.last_runs,
            output_path=args.output_path,
            as_json=args.json,
        )
    if args.command == "brain-status":
        return cmd_brain_status(as_json=args.json)
    if args.command == "brain-context":
        return cmd_brain_context(
            query=args.query,
            max_items=args.max_items,
            max_chars=args.max_chars,
            as_json=args.json,
        )
    if args.command == "brain-compact":
        return cmd_brain_compact(
            keep_recent=args.keep_recent,
            as_json=args.json,
        )
    if args.command == "brain-regression":
        return cmd_brain_regression(as_json=args.json)
    if args.command == "knowledge-status":
        return cmd_knowledge_status(as_json=args.json)
    if args.command == "contradiction-list":
        return cmd_contradiction_list(
            status=args.status,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "contradiction-resolve":
        return cmd_contradiction_resolve(
            contradiction_id=args.contradiction_id,
            resolution=args.resolution,
            merge_value=args.merge_value,
        )
    if args.command == "fact-lock":
        return cmd_fact_lock(
            node_id=args.node_id,
            action=args.action,
        )
    if args.command == "knowledge-regression":
        return cmd_knowledge_regression(
            snapshot_path=args.snapshot,
            as_json=args.json,
        )
    if args.command == "memory-snapshot":
        return cmd_memory_snapshot(
            create=args.create,
            verify_path=args.verify_path,
            note=args.note,
        )
    if args.command == "memory-maintenance":
        return cmd_memory_maintenance(
            keep_recent=args.keep_recent,
            snapshot_note=args.snapshot_note,
        )
    if args.command == "web-research":
        return cmd_web_research(
            query=args.query,
            max_results=args.max_results,
            max_pages=args.max_pages,
            auto_ingest=not args.no_ingest,
        )
    if args.command == "mobile-desktop-sync":
        return cmd_mobile_desktop_sync(
            auto_ingest=not args.no_ingest,
            as_json=args.json,
        )
    if args.command == "self-heal":
        return cmd_self_heal(
            force_maintenance=args.force_maintenance,
            keep_recent=args.keep_recent,
            snapshot_note=args.snapshot_note,
            as_json=args.json,
        )
    if args.command == "persona-config":
        return cmd_persona_config(
            enable=args.enable,
            disable=args.disable,
            humor_level=args.humor_level,
            mode=args.mode,
            style=args.style,
        )
    if args.command == "migrate-memory":
        return cmd_migrate_memory()
    if args.command == "desktop-widget":
        return cmd_desktop_widget()
    if args.command == "run-task":
        return cmd_run_task(
            task_type=args.type,
            prompt=args.prompt,
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            model=args.model,
            endpoint=args.endpoint,
            quality_profile=args.quality_profile,
            output_path=args.output_path,
        )
    if args.command == "ops-brief":
        out_path = Path(args.output_path) if args.output_path else None
        return cmd_ops_brief(
            snapshot_path=Path(args.snapshot_path),
            output_path=out_path,
        )
    if args.command == "ops-export-actions":
        return cmd_ops_export_actions(
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
        )
    if args.command == "ops-sync":
        return cmd_ops_sync(
            output_path=Path(args.output_path),
        )
    if args.command == "ops-autopilot":
        return cmd_ops_autopilot(
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            auto_open_connectors=args.auto_open_connectors,
        )
    if args.command == "daemon-run":
        return cmd_daemon_run(
            interval_s=args.interval_s,
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            auto_open_connectors=args.auto_open_connectors,
            max_cycles=args.max_cycles,
            idle_interval_s=args.idle_interval_s,
            idle_after_s=args.idle_after_s,
            run_missions=not args.skip_missions,
            sync_every_cycles=args.sync_every_cycles,
            self_heal_every_cycles=args.self_heal_every_cycles,
        )
    if args.command == "mission-create":
        return cmd_mission_create(
            topic=args.topic,
            objective=args.objective,
            sources=list(args.source),
        )
    if args.command == "mission-status":
        return cmd_mission_status(last=args.last)
    if args.command == "mission-run":
        return cmd_mission_run(
            mission_id=args.id,
            max_results=args.max_results,
            max_pages=args.max_pages,
            auto_ingest=not args.no_ingest,
        )
    if args.command == "runtime-control":
        return cmd_runtime_control(
            pause=args.pause,
            resume=args.resume,
            safe_on=args.safe_on,
            safe_off=args.safe_off,
            reset=args.reset,
            reason=args.reason,
        )
    if args.command == "owner-guard":
        return cmd_owner_guard(
            enable=args.enable,
            disable=args.disable,
            owner_user=args.owner_user,
            trust_device=args.trust_device,
            revoke_device=args.revoke_device,
            set_master_password_value=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.set_master_password,
            clear_master_password_value=args.clear_master_password,
        )
    if args.command == "gaming-mode":
        enable_opt: bool | None = None
        if args.enable:
            enable_opt = True
        elif args.disable:
            enable_opt = False
        return cmd_gaming_mode(enable=enable_opt, reason=args.reason, auto_detect=args.auto_detect)
    if args.command == "automation-run":
        return cmd_automation_run(
            actions_path=Path(args.actions_path),
            approve_privileged=args.approve_privileged,
            execute=args.execute,
        )
    if args.command == "connect-status":
        return cmd_connect_status()
    if args.command == "connect-grant":
        return cmd_connect_grant(
            connector_id=args.id,
            scopes=list(args.scope),
        )
    if args.command == "connect-bootstrap":
        return cmd_connect_bootstrap(auto_open=args.auto_open)
    if args.command == "phone-action":
        return cmd_phone_action(
            action=args.action,
            number=args.number,
            message=args.message,
            queue_path=Path(args.queue_path),
        )
    if args.command == "phone-spam-guard":
        return cmd_phone_spam_guard(
            call_log_path=Path(args.call_log_path),
            report_path=Path(args.report_path),
            queue_path=Path(args.queue_path),
            threshold=args.threshold,
        )
    if args.command == "voice-list":
        return cmd_voice_list()
    if args.command == "voice-say":
        return cmd_voice_say(
            text=args.text,
            profile=args.profile,
            voice_pattern=args.voice_pattern,
            output_wav=args.output_wav,
            rate=args.rate,
        )
    if args.command == "voice-enroll":
        return cmd_voice_enroll(
            user_id=args.user_id,
            wav_path=args.wav,
            replace=args.replace,
        )
    if args.command == "voice-verify":
        return cmd_voice_verify(
            user_id=args.user_id,
            wav_path=args.wav,
            threshold=args.threshold,
        )
    if args.command == "voice-run":
        return cmd_voice_run(
            text=args.text,
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            speak=args.speak,
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            voice_user=args.voice_user,
            voice_auth_wav=args.voice_auth_wav,
            voice_threshold=args.voice_threshold,
            master_password=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.master_password,
        )
    if args.command == "voice-listen":
        return cmd_voice_listen(
            duration=args.duration,
            language=args.language,
            model=args.model,
            execute=args.execute,
        )
    if args.command == "harvest":
        return cmd_harvest(
            topic=args.topic,
            providers=args.providers,
            max_tokens=args.max_tokens,
        )
    if args.command == "ingest-session":
        return cmd_ingest_session(
            source=args.source,
            session_path=args.session_path,
            project_path=args.project_path,
        )
    if args.command == "harvest-budget":
        return cmd_harvest_budget(
            action=args.action,
            provider=args.provider,
            period=args.period,
            limit_usd=args.limit_usd,
            limit_requests=args.limit_requests,
        )
    if args.command == "learn":
        return cmd_learn(
            user_message=args.user_message,
            assistant_response=args.assistant_response,
        )
    if args.command == "cross-branch-query":
        return cmd_cross_branch_query(
            query=args.query,
            k=args.k,
        )
    if args.command == "flag-expired":
        return cmd_flag_expired()
    if args.command == "memory-eval":
        return cmd_memory_eval()
    if args.command == "proactive-check":
        return cmd_proactive_check(snapshot_path=args.snapshot_path)
    if args.command == "wake-word":
        return cmd_wake_word(threshold=args.threshold)
    if args.command == "cost-reduction":
        return cmd_cost_reduction(days=args.days)
    if args.command == "self-test":
        return cmd_self_test(threshold=args.threshold)
    print(f"error: unhandled command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
