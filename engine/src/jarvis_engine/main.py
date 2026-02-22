from __future__ import annotations

import argparse
import json
import os
import webbrowser
from pathlib import Path

from jarvis_engine.automation import AutomationExecutor, load_actions
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
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.life_ops import build_daily_brief, export_actions_json, load_snapshot, suggest_actions
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.mobile_api import run_mobile_server
from jarvis_engine.ops_sync import build_live_snapshot
from jarvis_engine.router import ModelRouter
from jarvis_engine.task_orchestrator import TaskOrchestrator, TaskRequest
from jarvis_engine.voice import list_windows_voices, speak_text
from jarvis_engine.voice_auth import enroll_voiceprint, verify_voiceprint


def cmd_status() -> int:
    config = load_config()
    store = MemoryStore(repo_root())
    events = list(store.tail(5))

    print("Jarvis Engine Status")
    print(f"profile={config.profile}")
    print(f"primary_runtime={config.primary_runtime}")
    print(f"secondary_runtime={config.secondary_runtime}")
    print(f"security_strictness={config.security_strictness}")
    print(f"operation_mode={config.operation_mode}")
    print(f"cloud_burst_enabled={config.cloud_burst_enabled}")
    print("recent_events:")
    if not events:
        print("- none")
    else:
        for event in events:
            print(f"- [{event.ts}] {event.event_type}: {event.message}")
    return 0


def cmd_log(event_type: str, message: str) -> int:
    store = MemoryStore(repo_root())
    event = store.append(event_type=event_type, message=message)
    print(f"logged: [{event.ts}] {event.event_type}: {event.message}")
    return 0


def cmd_ingest(source: str, kind: str, task_id: str, content: str) -> int:
    store = MemoryStore(repo_root())
    pipeline = IngestionPipeline(store)
    record = pipeline.ingest(source=source, kind=kind, task_id=task_id, content=content)
    print(f"ingested: id={record.record_id} source={record.source} kind={record.kind} task_id={record.task_id}")
    return 0


def cmd_serve_mobile(host: str, port: int, token: str | None, signing_key: str | None) -> int:
    effective_token = token or os.getenv("JARVIS_MOBILE_TOKEN", "").strip()
    effective_signing_key = signing_key or os.getenv("JARVIS_MOBILE_SIGNING_KEY", "").strip()
    if not effective_token:
        print("error: missing mobile token. pass --token or set JARVIS_MOBILE_TOKEN")
        return 2
    if not effective_signing_key:
        print("error: missing signing key. pass --signing-key or set JARVIS_MOBILE_SIGNING_KEY")
        return 2

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
    except OSError as exc:
        print(f"error: could not bind mobile API on {host}:{port}: {exc}")
        return 3
    return 0


def cmd_route(risk: str, complexity: str) -> int:
    config = load_config()
    router = ModelRouter(cloud_burst_enabled=config.cloud_burst_enabled)
    decision = router.route(risk=risk, complexity=complexity)
    print(f"provider={decision.provider}")
    print(f"reason={decision.reason}")
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
    tasks = load_golden_tasks(tasks_path)
    run = run_eval(
        endpoint=endpoint,
        model=model,
        tasks=tasks,
        num_predict=num_predict,
        temperature=temperature,
        think=think,
        accept_thinking=accept_thinking,
        timeout_s=timeout_s,
    )
    append_history(history_path, run)
    print("growth_eval_completed=true")
    print(f"model={run.model}")
    print(f"score_pct={run.score_pct}")
    print(f"avg_tps={run.avg_tps}")
    print(f"avg_latency_s={run.avg_latency_s}")
    for result in run.results:
        print(
            "task="
            f"{result.task_id} "
            f"coverage_pct={round(result.coverage * 100, 2)} "
            f"matched={result.matched}/{result.total} "
            f"response_sha256={result.response_sha256}"
        )
    return 0


def cmd_growth_report(history_path: Path, last: int) -> int:
    rows = read_history(history_path)
    summary = summarize_history(rows, last=last)
    print("growth_report")
    print(f"runs={summary['runs']}")
    print(f"latest_model={summary['latest_model']}")
    print(f"latest_score_pct={summary['latest_score_pct']}")
    print(f"delta_vs_prev_pct={summary['delta_vs_prev_pct']}")
    print(f"window_avg_pct={summary['window_avg_pct']}")
    print(f"latest_ts={summary['latest_ts']}")
    return 0


def cmd_growth_audit(history_path: Path, run_index: int) -> int:
    rows = read_history(history_path)
    run = audit_run(rows, run_index=run_index)
    print("growth_audit")
    print(f"model={run['model']}")
    print(f"ts={run['ts']}")
    print(f"score_pct={run['score_pct']}")
    print(f"tasks={run['tasks']}")
    print(f"prev_run_sha256={run['prev_run_sha256']}")
    print(f"run_sha256={run['run_sha256']}")
    for result in run["results"]:
        matched_tokens = ",".join(result.get("matched_tokens", []))
        required_tokens = ",".join(result.get("required_tokens", []))
        print(f"task={result.get('task_id', '')}")
        print(f"required_tokens={required_tokens}")
        print(f"matched_tokens={matched_tokens}")
        print(f"prompt_sha256={result.get('prompt_sha256', '')}")
        print(f"response_sha256={result.get('response_sha256', '')}")
        print(f"response_source={result.get('response_source', '')}")
        print(f"response={result.get('response', '')}")
    return 0


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
    root = repo_root()
    store = MemoryStore(root)
    orchestrator = TaskOrchestrator(store, root)
    result = orchestrator.run(
        TaskRequest(
            task_type=task_type,  # type: ignore[arg-type]
            prompt=prompt,
            execute=execute,
            has_explicit_approval=approve_privileged,
            model=model,
            endpoint=endpoint,
            quality_profile=quality_profile,
            output_path=output_path,
        )
    )
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
    return 0 if result.allowed else 2


def cmd_ops_brief(snapshot_path: Path, output_path: Path | None) -> int:
    snapshot = load_snapshot(snapshot_path)
    brief = build_daily_brief(snapshot)
    print(brief)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(brief, encoding="utf-8")
        print(f"brief_saved={output_path}")
    return 0


def cmd_ops_export_actions(snapshot_path: Path, actions_path: Path) -> int:
    snapshot = load_snapshot(snapshot_path)
    actions = suggest_actions(snapshot)
    export_actions_json(actions, actions_path)
    print(f"actions_exported={actions_path}")
    print(f"action_count={len(actions)}")
    return 0


def cmd_ops_sync(output_path: Path) -> int:
    root = repo_root()
    summary = build_live_snapshot(root, output_path)
    print(f"snapshot_path={summary.snapshot_path}")
    print(f"tasks={summary.tasks}")
    print(f"calendar_events={summary.calendar_events}")
    print(f"emails={summary.emails}")
    print(f"bills={summary.bills}")
    print(f"subscriptions={summary.subscriptions}")
    print(f"connectors_ready={summary.connectors_ready}")
    print(f"connectors_pending={summary.connectors_pending}")
    print(f"connector_prompts={summary.connector_prompts}")
    if summary.connector_prompts > 0:
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
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


def cmd_automation_run(actions_path: Path, approve_privileged: bool, execute: bool) -> int:
    store = MemoryStore(repo_root())
    executor = AutomationExecutor(store)
    actions = load_actions(actions_path)
    outcomes = executor.run(
        actions,
        has_explicit_approval=approve_privileged,
        execute=execute,
    )
    for out in outcomes:
        print(
            f"title={out.title} allowed={out.allowed} executed={out.executed} "
            f"return_code={out.return_code} reason={out.reason}"
        )
        if out.stderr:
            print(f"stderr={out.stderr.strip()}")
    return 0


def cmd_connect_status() -> int:
    statuses = evaluate_connector_statuses(repo_root())
    prompts = build_connector_prompts(statuses)
    ready = sum(1 for s in statuses if s.ready)
    print("connector_status")
    print(f"ready={ready}")
    print(f"pending={len(statuses) - ready}")
    for status in statuses:
        print(
            f"id={status.connector_id} ready={status.ready} "
            f"permission={status.permission_granted} configured={status.configured} message={status.message}"
        )
    if prompts:
        print("connector_prompts_begin")
        for prompt in prompts:
            print(
                f"id={prompt.get('connector_id','')} "
                f"voice={prompt.get('option_voice','')} "
                f"tap={prompt.get('option_tap_url','')}"
            )
        print("connector_prompts_end")
    return 0


def cmd_connect_grant(connector_id: str, scopes: list[str]) -> int:
    try:
        granted = grant_connector_permission(repo_root(), connector_id=connector_id, scopes=scopes)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    print(f"connector_id={connector_id}")
    print("granted=true")
    print(f"scopes={','.join(granted.get('scopes', []))}")
    print(f"granted_utc={granted.get('granted_utc', '')}")
    return 0


def cmd_connect_bootstrap(auto_open: bool) -> int:
    statuses = evaluate_connector_statuses(repo_root())
    prompts = build_connector_prompts(statuses)
    if not prompts:
        print("connectors_ready=true")
        return 0
    print("connectors_ready=false")
    for prompt in prompts:
        print(
            "connector_prompt "
            f"id={prompt.get('connector_id','')} "
            f"voice=\"{prompt.get('option_voice','')}\" "
            f"tap={prompt.get('option_tap_url','')}"
        )
        if auto_open:
            url = prompt.get("option_tap_url", "").strip()
            if url:
                webbrowser.open(url)
    return 0


def cmd_voice_list() -> int:
    voices = list_windows_voices()
    if not voices:
        print("voices=none")
        return 1
    print("voices:")
    for name in voices:
        print(f"- {name}")
    return 0


def cmd_voice_say(
    text: str,
    profile: str,
    voice_pattern: str,
    output_wav: str,
    rate: int,
) -> int:
    result = speak_text(
        text=text,
        profile=profile,
        custom_voice_pattern=voice_pattern,
        output_wav=output_wav,
        rate=rate,
    )
    print(f"voice={result.voice_name}")
    if result.output_wav:
        print(f"wav={result.output_wav}")
    print(result.message)
    return 0


def cmd_voice_enroll(user_id: str, wav_path: str, replace: bool) -> int:
    try:
        result = enroll_voiceprint(
            repo_root(),
            user_id=user_id,
            wav_path=wav_path,
            replace=replace,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"user_id={result.user_id}")
    print(f"profile_path={result.profile_path}")
    print(f"samples={result.samples}")
    print(result.message)
    return 0


def cmd_voice_verify(user_id: str, wav_path: str, threshold: float) -> int:
    try:
        result = verify_voiceprint(
            repo_root(),
            user_id=user_id,
            wav_path=wav_path,
            threshold=threshold,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"user_id={result.user_id}")
    print(f"score={result.score}")
    print(f"threshold={result.threshold}")
    print(f"matched={result.matched}")
    print(result.message)
    return 0 if result.matched else 2


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
) -> int:
    lowered = text.lower().strip()
    intent = "unknown"
    rc = 1

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
    elif ("sync" in lowered) and any(k in lowered for k in ["calendar", "email", "inbox", "ops"]):
        intent = "ops_sync"
        live_snapshot = snapshot_path.with_name("ops_snapshot.live.json")
        rc = cmd_ops_sync(live_snapshot)
    elif "brief" in lowered:
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
    elif "generate 3d" in lowered or "generate model" in lowered:
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
    else:
        print("intent=unknown")
        print("reason=No supported voice intent matched.")
        if speak:
            cmd_voice_say(
                text="I did not recognize that command. Please try a supported Jarvis action.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return 2

    print(f"intent={intent}")
    print(f"status_code={rc}")
    if speak:
        completion = "completed successfully" if rc == 0 else "failed or requires approval"
        cmd_voice_say(
            text=f"Command {intent} {completion}.",
            profile="jarvis_like",
            voice_pattern="",
            output_wav="",
            rate=-1,
        )
    return rc


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
        default=str(repo_root() / ".planning" / "ops_snapshot.json"),
    )
    p_ops_brief.add_argument("--output-path")

    p_ops_actions = sub.add_parser("ops-export-actions", help="Export suggested actions from ops snapshot.")
    p_ops_actions.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.json"),
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
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_voice_run.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )

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
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
