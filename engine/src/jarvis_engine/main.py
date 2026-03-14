from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from jarvis_engine.config import repo_root

from jarvis_engine.cli.ops import (  # noqa: E402
    cmd_ops_brief,
    cmd_ops_export_actions,
    cmd_ops_sync,
    cmd_ops_autopilot,
    cmd_automation_run,
    cmd_mission_create,
    cmd_mission_status,
    cmd_mission_cancel,
    cmd_mission_run,
    cmd_growth_eval,
    cmd_growth_report,
    cmd_growth_audit,
    cmd_intelligence_dashboard,
)
from jarvis_engine.cli.knowledge import (  # noqa: E402
    cmd_brain_status,
    cmd_brain_context,
    cmd_brain_compact,
    cmd_brain_regression,
    cmd_knowledge_status,
    cmd_contradiction_list,
    cmd_contradiction_resolve,
    cmd_fact_lock,
    cmd_knowledge_regression,
    cmd_consolidate,
    cmd_harvest,
    cmd_ingest_session,
    cmd_harvest_budget,
    cmd_learn,
    cmd_cross_branch_query,
    cmd_flag_expired,
)
from jarvis_engine.cli.voice import (  # noqa: E402
    cmd_voice_list,
    cmd_voice_say,
    cmd_voice_enroll,
    cmd_voice_verify,
    cmd_voice_listen,
    cmd_voice_run,
    cmd_wake_word,
)
from jarvis_engine.cli.system import (  # noqa: E402
    cmd_gaming_mode,
    cmd_open_web,
    cmd_status,
    cmd_log,
    cmd_ingest,
    cmd_serve_mobile,
    cmd_desktop_widget,
    cmd_runtime_control,
    cmd_persona_config,
    cmd_memory_snapshot,
    cmd_memory_maintenance,
    cmd_migrate_memory,
    cmd_mobile_desktop_sync,
    cmd_self_heal,
    cmd_memory_eval,
    cmd_daemon_run,
    cmd_weather,
)
from jarvis_engine.cli.security import (  # noqa: E402
    cmd_owner_guard,
    cmd_connect_status,
    cmd_connect_grant,
    cmd_connect_bootstrap,
    cmd_phone_action,
    cmd_phone_spam_guard,
)
from jarvis_engine.cli.tasks import (  # noqa: E402
    cmd_route,
    cmd_run_task,
    cmd_web_research,
)
from jarvis_engine.cli.proactive import (  # noqa: E402
    cmd_proactive_check,
    cmd_cost_reduction,
    cmd_self_test,
)

# Backward-compat: run_mobile_server used by tests via main_mod.run_mobile_server
from jarvis_engine.mobile_routes.lifecycle import run_mobile_server  # noqa: F401, E402

# Imports from _constants (used by argparse registrations)
from jarvis_engine._constants import ACTIONS_FILENAME, DEFAULT_API_PORT, OPS_SNAPSHOT_FILENAME  # noqa: E402

logger = logging.getLogger(__name__)


def _dispatch_serve_mobile(args: argparse.Namespace) -> int:
    """Resolve --tls / --no-tls into a tri-state: True, False, or None (auto)."""
    _tls_flag: bool | None = None
    if getattr(args, "tls", None):
        _tls_flag = True
    elif getattr(args, "no_tls", False):
        _tls_flag = False
    return cmd_serve_mobile(
        host=args.host, port=args.port, token=args.token, signing_key=args.signing_key,
        allow_insecure_bind=args.allow_insecure_bind, config_file=args.config_file,
        tls=_tls_flag,
    )


def _dispatch_growth_eval(args: argparse.Namespace) -> int:
    """Convert think string choice to optional bool."""
    think_opt = None
    if args.think == "on":
        think_opt = True
    elif args.think == "off":
        think_opt = False
    return cmd_growth_eval(
        model=args.model, endpoint=args.endpoint, tasks_path=Path(args.tasks_path),
        history_path=Path(args.history_path), num_predict=args.num_predict,
        temperature=args.temperature, think=think_opt,
        accept_thinking=args.accept_thinking, timeout_s=args.timeout_s,
    )


def _dispatch_owner_guard(args: argparse.Namespace) -> int:
    """Resolve master password from env var with CLI fallback."""
    return cmd_owner_guard(
        enable=args.enable, disable=args.disable, owner_user=args.owner_user,
        trust_device=args.trust_device, revoke_device=args.revoke_device,
        set_master_password_value=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.set_master_password,
        clear_master_password_value=args.clear_master_password,
    )


def _dispatch_gaming_mode(args: argparse.Namespace) -> int:
    """Convert enable/disable flags to optional bool."""
    enable_opt: bool | None = None
    if args.enable:
        enable_opt = True
    elif args.disable:
        enable_opt = False
    return cmd_gaming_mode(enable=enable_opt, reason=args.reason, auto_detect=args.auto_detect)


def _dispatch_voice_run(args: argparse.Namespace) -> int:
    """Resolve master password from env var with CLI fallback."""
    return cmd_voice_run(
        text=args.text, utterance=None, execute=args.execute, approve_privileged=args.approve_privileged,
        speak=args.speak, snapshot_path=Path(args.snapshot_path),
        actions_path=Path(args.actions_path), voice_user=args.voice_user,
        voice_auth_wav=args.voice_auth_wav, voice_threshold=args.voice_threshold,
        master_password=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.master_password,
        model_override=args.model_override,
        skip_voice_auth_guard=args.skip_voice_auth_guard,
    )


def _register_core_commands(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("status", help="Show engine bootstrap status.").set_defaults(handler=lambda a: cmd_status())

    p_log = sub.add_parser("log", help="Append an event to memory log.")
    p_log.add_argument("--type", required=True, help="Event type label.")
    p_log.add_argument("--message", required=True, help="Event description.")
    p_log.set_defaults(handler=lambda a: cmd_log(event_type=a.type, message=a.message))

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
    p_ingest.set_defaults(handler=lambda a: cmd_ingest(source=a.source, kind=a.kind, task_id=a.task_id, content=a.content))

    p_mobile = sub.add_parser("serve-mobile", help="Run secure mobile ingestion API.")
    p_mobile.add_argument("--host", default="127.0.0.1")
    p_mobile.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    p_mobile.add_argument("--token", help="Shared token. Falls back to JARVIS_MOBILE_TOKEN env var.")
    p_mobile.add_argument(
        "--signing-key",
        help="HMAC signing key. Falls back to JARVIS_MOBILE_SIGNING_KEY env var.",
    )
    p_mobile.add_argument(
        "--config-file",
        help="JSON config file with token and signing_key (avoids exposing secrets in process command line).",
    )
    p_mobile.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help="Allow non-loopback HTTP bind (for trusted LAN). Falls back to JARVIS_ALLOW_INSECURE_MOBILE_BIND env var.",
    )
    _tls_group = p_mobile.add_mutually_exclusive_group()
    _tls_group.add_argument(
        "--tls",
        action="store_true",
        default=None,
        help="Require TLS (generate self-signed cert if needed). Default: auto-detect.",
    )
    _tls_group.add_argument(
        "--no-tls",
        action="store_true",
        default=False,
        help="Explicitly disable TLS (plain HTTP).",
    )
    p_mobile.set_defaults(handler=_dispatch_serve_mobile)

    p_route = sub.add_parser("route", help="Get a route decision.")
    p_route.add_argument("--risk", default="low", choices=["low", "medium", "high", "critical"])
    p_route.add_argument(
        "--complexity",
        default="normal",
        choices=["easy", "normal", "hard", "very_hard"],
    )
    p_route.set_defaults(handler=lambda a: cmd_route(risk=a.risk, complexity=a.complexity))


def _register_growth_commands(sub: argparse._SubParsersAction) -> None:
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
    p_growth_eval.set_defaults(handler=_dispatch_growth_eval)

    p_growth_report = sub.add_parser("growth-report", help="Show growth trend from eval history.")
    p_growth_report.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_report.add_argument("--last", type=int, default=10)
    p_growth_report.set_defaults(handler=lambda a: cmd_growth_report(history_path=Path(a.history_path), last=a.last))

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
    p_growth_audit.set_defaults(handler=lambda a: cmd_growth_audit(history_path=Path(a.history_path), run_index=a.run_index))

    p_intelligence = sub.add_parser(
        "intelligence-dashboard",
        help="Build intelligence ranking/ETA dashboard from local growth history.",
    )
    p_intelligence.add_argument("--last-runs", type=int, default=20)
    p_intelligence.add_argument("--output-path", default=str(repo_root() / ".planning" / "intelligence_dashboard.json"))
    p_intelligence.add_argument("--json", action="store_true", help="Print full JSON payload.")
    p_intelligence.set_defaults(handler=lambda a: cmd_intelligence_dashboard(last_runs=a.last_runs, output_path=a.output_path, as_json=a.json))


def _register_brain_knowledge_commands(sub: argparse._SubParsersAction) -> None:
    p_brain_status = sub.add_parser("brain-status", help="Show high-level brain memory branch stats.")
    p_brain_status.add_argument("--json", action="store_true")
    p_brain_status.set_defaults(handler=lambda a: cmd_brain_status(as_json=a.json))

    p_brain_context = sub.add_parser(
        "brain-context",
        help="Build compact context packet from long-term brain memory.",
    )
    p_brain_context.add_argument("--query", required=True)
    p_brain_context.add_argument("--max-items", type=int, default=10)
    p_brain_context.add_argument("--max-chars", type=int, default=2400)
    p_brain_context.add_argument("--json", action="store_true")
    p_brain_context.set_defaults(handler=lambda a: cmd_brain_context(query=a.query, max_items=a.max_items, max_chars=a.max_chars, as_json=a.json))

    p_brain_compact = sub.add_parser("brain-compact", help="Compact old brain records into summary groups.")
    p_brain_compact.add_argument("--keep-recent", type=int, default=1800)
    p_brain_compact.add_argument("--json", action="store_true")
    p_brain_compact.set_defaults(handler=lambda a: cmd_brain_compact(keep_recent=a.keep_recent, as_json=a.json))

    p_brain_regression = sub.add_parser("brain-regression", help="Run anti-regression health checks for brain memory.")
    p_brain_regression.add_argument("--json", action="store_true")
    p_brain_regression.set_defaults(handler=lambda a: cmd_brain_regression(as_json=a.json))

    p_kg_status = sub.add_parser("knowledge-status", help="Show knowledge graph node/edge/locked/contradiction counts.")
    p_kg_status.add_argument("--json", action="store_true")
    p_kg_status.set_defaults(handler=lambda a: cmd_knowledge_status(as_json=a.json))

    p_clist = sub.add_parser("contradiction-list", help="List knowledge graph contradictions.")
    p_clist.add_argument("--status", default="pending", help="Filter by status (pending, resolved, or empty for all).")
    p_clist.add_argument("--limit", type=int, default=20)
    p_clist.add_argument("--json", action="store_true")
    p_clist.set_defaults(handler=lambda a: cmd_contradiction_list(status=a.status, limit=a.limit, as_json=a.json))

    p_cresolve = sub.add_parser("contradiction-resolve", help="Resolve a knowledge graph contradiction.")
    p_cresolve.add_argument("contradiction_id", type=int, help="Contradiction ID to resolve.")
    p_cresolve.add_argument("--resolution", required=True, choices=["accept_new", "keep_old", "merge"])
    p_cresolve.add_argument("--merge-value", default="", help="Merged value (required for merge resolution).")
    p_cresolve.set_defaults(handler=lambda a: cmd_contradiction_resolve(contradiction_id=a.contradiction_id, resolution=a.resolution, merge_value=a.merge_value))

    p_flock = sub.add_parser("fact-lock", help="Lock or unlock a knowledge graph fact node.")
    p_flock.add_argument("node_id", help="Node ID to lock or unlock.")
    p_flock.add_argument("--action", default="lock", choices=["lock", "unlock"])
    p_flock.set_defaults(handler=lambda a: cmd_fact_lock(node_id=a.node_id, action=a.action))

    p_kg_regression = sub.add_parser("knowledge-regression", help="Run knowledge graph regression check.")
    p_kg_regression.add_argument("--snapshot", default="", help="Path to previous snapshot metadata JSON.")
    p_kg_regression.add_argument("--json", action="store_true")
    p_kg_regression.set_defaults(handler=lambda a: cmd_knowledge_regression(snapshot_path=a.snapshot, as_json=a.json))


def _register_memory_commands(sub: argparse._SubParsersAction) -> None:
    p_snapshot = sub.add_parser("memory-snapshot", help="Create or verify signed memory snapshot.")
    p_snapshot_group = p_snapshot.add_mutually_exclusive_group(required=True)
    p_snapshot_group.add_argument("--create", action="store_true")
    p_snapshot_group.add_argument("--verify-path")
    p_snapshot.add_argument("--note", default="")
    p_snapshot.set_defaults(handler=lambda a: cmd_memory_snapshot(create=a.create, verify_path=a.verify_path, note=a.note))

    p_maintenance = sub.add_parser("memory-maintenance", help="Run compact + regression + signed snapshot maintenance.")
    p_maintenance.add_argument("--keep-recent", type=int, default=1800)
    p_maintenance.add_argument("--snapshot-note", default="nightly")
    p_maintenance.set_defaults(handler=lambda a: cmd_memory_maintenance(keep_recent=a.keep_recent, snapshot_note=a.snapshot_note))

    p_web_research = sub.add_parser("web-research", help="Search the public web and summarize findings with source links.")
    p_web_research.add_argument("--query", required=True)
    p_web_research.add_argument("--max-results", type=int, default=8)
    p_web_research.add_argument("--max-pages", type=int, default=6)
    p_web_research.add_argument("--no-ingest", action="store_true")
    p_web_research.set_defaults(handler=lambda a: cmd_web_research(query=a.query, max_results=a.max_results, max_pages=a.max_pages, auto_ingest=not a.no_ingest))

    p_sync = sub.add_parser("mobile-desktop-sync", help="Run cross-device state checks and write sync report.")
    p_sync.add_argument("--json", action="store_true")
    p_sync.add_argument("--no-ingest", action="store_true")
    p_sync.set_defaults(handler=lambda a: cmd_mobile_desktop_sync(auto_ingest=not a.no_ingest, as_json=a.json))

    p_self_heal = sub.add_parser("self-heal", help="Run Jarvis self-healing checks and safe repairs.")
    p_self_heal.add_argument("--force-maintenance", action="store_true")
    p_self_heal.add_argument("--keep-recent", type=int, default=1800)
    p_self_heal.add_argument("--snapshot-note", default="self-heal")
    p_self_heal.add_argument("--json", action="store_true")
    p_self_heal.set_defaults(handler=lambda a: cmd_self_heal(force_maintenance=a.force_maintenance, keep_recent=a.keep_recent, snapshot_note=a.snapshot_note, as_json=a.json))

    p_persona = sub.add_parser("persona-config", help="Configure Jarvis personality response style.")
    p_persona.add_argument("--enable", action="store_true")
    p_persona.add_argument("--disable", action="store_true")
    p_persona.add_argument("--humor-level", type=int)
    p_persona.add_argument("--mode", default="")
    p_persona.add_argument("--style", default="")
    p_persona.set_defaults(handler=lambda a: cmd_persona_config(enable=a.enable, disable=a.disable, humor_level=a.humor_level, mode=a.mode, style=a.style))

    sub.add_parser("migrate-memory", help="Migrate JSONL/JSON memory data into SQLite (one-time).").set_defaults(handler=lambda a: cmd_migrate_memory())

    sub.add_parser("desktop-widget", help="Launch desktop-native Jarvis widget window.").set_defaults(handler=lambda a: cmd_desktop_widget())


def _register_task_commands(sub: argparse._SubParsersAction) -> None:
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
    p_run_task.set_defaults(handler=lambda a: cmd_run_task(task_type=a.type, prompt=a.prompt, execute=a.execute, approve_privileged=a.approve_privileged, model=a.model, endpoint=a.endpoint, quality_profile=a.quality_profile, output_path=a.output_path))


def _register_ops_commands(sub: argparse._SubParsersAction) -> None:
    p_ops_brief = sub.add_parser("ops-brief", help="Generate daily life operations brief.")
    p_ops_brief.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_ops_brief.add_argument("--output-path")
    p_ops_brief.set_defaults(handler=lambda a: cmd_ops_brief(snapshot_path=Path(a.snapshot_path), output_path=Path(a.output_path) if a.output_path else None))

    p_ops_actions = sub.add_parser("ops-export-actions", help="Export suggested actions from ops snapshot.")
    p_ops_actions.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_ops_actions.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / ACTIONS_FILENAME),
    )
    p_ops_actions.set_defaults(handler=lambda a: cmd_ops_export_actions(snapshot_path=Path(a.snapshot_path), actions_path=Path(a.actions_path)))

    p_ops_sync = sub.add_parser("ops-sync", help="Build live operations snapshot from connectors.")
    p_ops_sync.add_argument(
        "--output-path",
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_ops_sync.set_defaults(handler=lambda a: cmd_ops_sync(output_path=Path(a.output_path)))

    p_ops_autopilot = sub.add_parser("ops-autopilot", help="Run connector check, sync, brief, action export, and automation.")
    p_ops_autopilot.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_ops_autopilot.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / ACTIONS_FILENAME),
    )
    p_ops_autopilot.add_argument("--execute", action="store_true")
    p_ops_autopilot.add_argument("--approve-privileged", action="store_true")
    p_ops_autopilot.add_argument("--auto-open-connectors", action="store_true")
    p_ops_autopilot.set_defaults(handler=lambda a: cmd_ops_autopilot(snapshot_path=Path(a.snapshot_path), actions_path=Path(a.actions_path), execute=a.execute, approve_privileged=a.approve_privileged, auto_open_connectors=a.auto_open_connectors))

    p_daemon = sub.add_parser("daemon-run", help="Run Jarvis autopilot loop continuously.")
    p_daemon.add_argument("--interval-s", type=int, default=180)
    p_daemon.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_daemon.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / ACTIONS_FILENAME),
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
    p_daemon.add_argument("--self-test-every-cycles", type=int, default=20)
    p_daemon.set_defaults(handler=lambda a: cmd_daemon_run(interval_s=a.interval_s, snapshot_path=Path(a.snapshot_path), actions_path=Path(a.actions_path), execute=a.execute, approve_privileged=a.approve_privileged, auto_open_connectors=a.auto_open_connectors, max_cycles=a.max_cycles, idle_interval_s=a.idle_interval_s, idle_after_s=a.idle_after_s, run_missions=not a.skip_missions, sync_every_cycles=a.sync_every_cycles, self_heal_every_cycles=a.self_heal_every_cycles, self_test_every_cycles=a.self_test_every_cycles))


def _register_mission_learning_commands(sub: argparse._SubParsersAction) -> None:
    p_mission_create = sub.add_parser("mission-create", help="Create a learning mission.")
    p_mission_create.add_argument("--topic", required=True)
    p_mission_create.add_argument("--objective", default="")
    p_mission_create.add_argument(
        "--source",
        action="append",
        default=[],
        help="Learning source profile (repeatable), e.g. google, reddit, official_docs",
    )
    p_mission_create.set_defaults(handler=lambda a: cmd_mission_create(topic=a.topic, objective=a.objective, sources=list(a.source)))

    p_mission_status = sub.add_parser("mission-status", help="Show recent learning missions.")
    p_mission_status.add_argument("--last", type=int, default=10)
    p_mission_status.set_defaults(handler=lambda a: cmd_mission_status(last=a.last))

    p_mission_run = sub.add_parser("mission-run", help="Run one learning mission with source verification.")
    p_mission_run.add_argument("--id", required=True, help="Mission id from mission-create.")
    p_mission_run.add_argument("--max-results", type=int, default=8)
    p_mission_run.add_argument("--max-pages", type=int, default=12)
    p_mission_run.add_argument("--no-ingest", action="store_true", help="Do not ingest verified findings.")
    p_mission_run.set_defaults(handler=lambda a: cmd_mission_run(mission_id=a.id, max_results=a.max_results, max_pages=a.max_pages, auto_ingest=not a.no_ingest))

    p_mission_cancel = sub.add_parser("mission-cancel", help="Cancel a pending learning mission.")
    p_mission_cancel.add_argument("--id", required=True, help="Mission id to cancel.")
    p_mission_cancel.set_defaults(handler=lambda a: cmd_mission_cancel(mission_id=a.id))

    p_consolidate = sub.add_parser("consolidate", help="Consolidate episodic memories into semantic facts.")
    p_consolidate.add_argument("--branch", default="", help="Restrict to specific branch (empty = all).")
    p_consolidate.add_argument("--max-groups", type=int, default=20, help="Max groups to process.")
    p_consolidate.add_argument("--dry-run", action="store_true", help="Compute clusters but don't write.")
    p_consolidate.set_defaults(handler=lambda a: cmd_consolidate(branch=a.branch, max_groups=a.max_groups, dry_run=a.dry_run))


def _register_runtime_security_commands(sub: argparse._SubParsersAction) -> None:
    p_runtime = sub.add_parser("runtime-control", help="Pause/resume daemon and toggle safe mode.")
    p_runtime_group = p_runtime.add_mutually_exclusive_group()
    p_runtime_group.add_argument("--pause", action="store_true")
    p_runtime_group.add_argument("--resume", action="store_true")
    p_runtime_group.add_argument("--reset", action="store_true")
    p_runtime.add_argument("--safe-on", action="store_true")
    p_runtime.add_argument("--safe-off", action="store_true")
    p_runtime.add_argument("--reason", default="")
    p_runtime.set_defaults(handler=lambda a: cmd_runtime_control(pause=a.pause, resume=a.resume, safe_on=a.safe_on, safe_off=a.safe_off, reset=a.reset, reason=a.reason))

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
    p_owner.set_defaults(handler=_dispatch_owner_guard)

    p_gaming = sub.add_parser("gaming-mode", help="Enable/disable low-impact mode for gaming sessions.")
    p_gaming_group = p_gaming.add_mutually_exclusive_group()
    p_gaming_group.add_argument("--enable", action="store_true")
    p_gaming_group.add_argument("--disable", action="store_true")
    p_gaming.add_argument("--auto-detect", choices=["on", "off"], default="")
    p_gaming.add_argument("--reason", default="")
    p_gaming.set_defaults(handler=_dispatch_gaming_mode)

    p_automation = sub.add_parser("automation-run", help="Run planned actions with capability gates.")
    p_automation.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / ACTIONS_FILENAME),
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
    p_automation.set_defaults(handler=lambda a: cmd_automation_run(actions_path=Path(a.actions_path), approve_privileged=a.approve_privileged, execute=a.execute))

    sub.add_parser("connect-status", help="Show connector readiness and prompts.").set_defaults(handler=lambda a: cmd_connect_status())

    p_connect_grant = sub.add_parser("connect-grant", help="Grant connector permission.")
    p_connect_grant.add_argument("--id", required=True, help="Connector id (for example: email, calendar).")
    p_connect_grant.add_argument("--scope", action="append", default=[], help="Optional scope (repeatable).")
    p_connect_grant.set_defaults(handler=lambda a: cmd_connect_grant(connector_id=a.id, scopes=list(a.scope)))

    p_connect_bootstrap = sub.add_parser("connect-bootstrap", help="Show connector prompts and optionally open setup links.")
    p_connect_bootstrap.add_argument("--auto-open", action="store_true", help="Open tap URLs in browser.")
    p_connect_bootstrap.set_defaults(handler=lambda a: cmd_connect_bootstrap(auto_open=a.auto_open))


def _register_phone_commands(sub: argparse._SubParsersAction) -> None:
    p_phone_action = sub.add_parser("phone-action", help="Queue phone action (send SMS/place call/ignore/block).")
    p_phone_action.add_argument("--action", required=True, choices=["send_sms", "place_call", "ignore_call", "block_number", "silence_unknown_callers"])
    p_phone_action.add_argument("--number", default="")
    p_phone_action.add_argument("--message", default="")
    p_phone_action.add_argument(
        "--queue-path",
        default=str(repo_root() / ".planning" / "phone_actions.jsonl"),
    )
    p_phone_action.set_defaults(handler=lambda a: cmd_phone_action(action=a.action, number=a.number, message=a.message, queue_path=Path(a.queue_path)))

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
    p_phone_spam.set_defaults(handler=lambda a: cmd_phone_spam_guard(call_log_path=Path(a.call_log_path), report_path=Path(a.report_path), queue_path=Path(a.queue_path), threshold=a.threshold))


def _register_voice_commands(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("voice-list", help="List available local Windows voices.").set_defaults(handler=lambda a: cmd_voice_list())

    p_voice = sub.add_parser("voice-say", help="Speak text with local Windows voice synthesis.")
    p_voice.add_argument("--text", required=True)
    p_voice.add_argument("--profile", default="jarvis_like", choices=["jarvis_like", "default"])
    p_voice.add_argument("--voice-pattern", default="")
    p_voice.add_argument("--output-wav", default="")
    p_voice.add_argument("--rate", type=int, default=-1)
    p_voice.set_defaults(handler=lambda a: cmd_voice_say(text=a.text, profile=a.profile, voice_pattern=a.voice_pattern, output_wav=a.output_wav, rate=a.rate))

    p_voice_enroll = sub.add_parser("voice-enroll", help="Enroll a user voiceprint from WAV.")
    p_voice_enroll.add_argument("--user-id", required=True, help="Identity label, e.g. conner.")
    p_voice_enroll.add_argument("--wav", required=True, help="Path to WAV sample of your voice.")
    p_voice_enroll.add_argument("--replace", action="store_true", help="Replace existing profile.")
    p_voice_enroll.set_defaults(handler=lambda a: cmd_voice_enroll(user_id=a.user_id, wav_path=a.wav, replace=a.replace))

    p_voice_verify = sub.add_parser("voice-verify", help="Verify WAV sample against enrolled voiceprint.")
    p_voice_verify.add_argument("--user-id", required=True)
    p_voice_verify.add_argument("--wav", required=True)
    p_voice_verify.add_argument("--threshold", type=float, default=0.82)
    p_voice_verify.set_defaults(handler=lambda a: cmd_voice_verify(user_id=a.user_id, wav_path=a.wav, threshold=a.threshold))

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
        default=str(repo_root() / ".planning" / OPS_SNAPSHOT_FILENAME),
    )
    p_voice_run.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / ACTIONS_FILENAME),
    )
    p_voice_run.add_argument(
        "--model-override",
        default="",
        help="Optional explicit model alias to force for this command.",
    )
    p_voice_run.add_argument(
        "--skip-voice-auth-guard",
        action="store_true",
        help="Bypass voice-auth requirement guard (owner identity checks still apply).",
    )
    p_voice_run.set_defaults(handler=_dispatch_voice_run)

    p_voice_listen = sub.add_parser("voice-listen", help="Record from microphone and transcribe speech-to-text.")
    p_voice_listen.add_argument("--duration", type=float, default=30.0, help="Max recording duration in seconds.")
    p_voice_listen.add_argument("--language", default="en", help="Language code hint for transcription.")
    p_voice_listen.add_argument("--execute", action="store_true", help="Execute transcribed text as a voice command.")
    p_voice_listen.set_defaults(handler=lambda a: cmd_voice_listen(duration=a.duration, language=a.language, execute=a.execute))


def _register_harvesting_commands(sub: argparse._SubParsersAction) -> None:
    p_harvest = sub.add_parser("harvest", help="Harvest knowledge about a topic from external AI sources.")
    p_harvest.add_argument("--topic", required=True, help="Topic to harvest knowledge about.")
    p_harvest.add_argument("--providers", default=None, help="Comma-separated list of providers (default: all available).")
    p_harvest.add_argument("--max-tokens", type=int, default=2048, help="Max tokens per provider response.")
    p_harvest.set_defaults(handler=lambda a: cmd_harvest(topic=a.topic, providers=a.providers, max_tokens=a.max_tokens))

    p_ingest_session = sub.add_parser("ingest-session", help="Ingest knowledge from Claude Code or Codex session files.")
    p_ingest_session.add_argument("--source", required=True, choices=["claude", "codex"], help="Session source type.")
    p_ingest_session.add_argument("--session-path", default=None, help="Specific session file path (optional).")
    p_ingest_session.add_argument("--project-path", default=None, help="Claude Code project path to scope search (optional).")
    p_ingest_session.set_defaults(handler=lambda a: cmd_ingest_session(source=a.source, session_path=a.session_path, project_path=a.project_path))

    p_harvest_budget = sub.add_parser("harvest-budget", help="View or set harvest budget limits.")
    p_harvest_budget.add_argument("--action", default="status", choices=["status", "set"], help="Budget action.")
    p_harvest_budget.add_argument("--provider", default=None, help="Provider name.")
    p_harvest_budget.add_argument("--period", default=None, choices=["daily", "monthly"], help="Budget period.")
    p_harvest_budget.add_argument("--limit-usd", type=float, default=None, help="USD limit.")
    p_harvest_budget.add_argument("--limit-requests", type=int, default=None, help="Request count limit.")
    p_harvest_budget.set_defaults(handler=lambda a: cmd_harvest_budget(action=a.action, provider=a.provider, period=a.period, limit_usd=a.limit_usd, limit_requests=a.limit_requests))


def _register_learning_commands(sub: argparse._SubParsersAction) -> None:
    p_learn = sub.add_parser("learn", help="Manually trigger learning from text input.")
    p_learn.add_argument("--user-message", required=True, help="User message text.")
    p_learn.add_argument("--assistant-response", required=True, help="Assistant response text.")
    p_learn.set_defaults(handler=lambda a: cmd_learn(user_message=a.user_message, assistant_response=a.assistant_response))

    p_cbq = sub.add_parser("cross-branch-query", help="Query across knowledge branches.")
    p_cbq.add_argument("query", help="Natural language query.")
    p_cbq.add_argument("--k", type=int, default=10, help="Max results to return.")
    p_cbq.set_defaults(handler=lambda a: cmd_cross_branch_query(query=a.query, k=a.k))

    sub.add_parser("flag-expired", help="Flag expired knowledge graph facts.").set_defaults(handler=lambda a: cmd_flag_expired())

    sub.add_parser("memory-eval", help="Run memory-recall golden task evaluation.").set_defaults(handler=lambda a: cmd_memory_eval())


def _register_proactive_commands(sub: argparse._SubParsersAction) -> None:
    p_proactive = sub.add_parser("proactive-check", help="Manually trigger proactive evaluation.")
    p_proactive.add_argument("--snapshot-path", default="", help="Path to ops snapshot JSON.")
    p_proactive.set_defaults(handler=lambda a: cmd_proactive_check(snapshot_path=a.snapshot_path))

    p_wakeword = sub.add_parser("wake-word", help="Start wake word detection (blocking).")
    p_wakeword.add_argument("--threshold", type=float, default=0.5, help="Detection threshold.")
    p_wakeword.set_defaults(handler=lambda a: cmd_wake_word(threshold=a.threshold))

    p_cost_red = sub.add_parser("cost-reduction", help="Show local vs cloud query ratio and trend.")
    p_cost_red.add_argument("--days", type=int, default=30, help="Number of days to look back.")
    p_cost_red.set_defaults(handler=lambda a: cmd_cost_reduction(days=a.days))

    p_selftest = sub.add_parser("self-test", help="Run adversarial memory quiz.")
    p_selftest.add_argument("--threshold", type=float, default=0.5, help="Score threshold for alerts.")
    p_selftest.set_defaults(handler=lambda a: cmd_self_test(threshold=a.threshold))


def main() -> int:
    """CLI entrypoint: parse args and dispatch to the matching command handler."""
    parser = argparse.ArgumentParser(description="Jarvis engine bootstrap CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    _register_core_commands(sub)
    _register_growth_commands(sub)
    _register_brain_knowledge_commands(sub)
    _register_memory_commands(sub)
    _register_task_commands(sub)
    _register_ops_commands(sub)
    _register_mission_learning_commands(sub)
    _register_runtime_security_commands(sub)
    _register_phone_commands(sub)
    _register_voice_commands(sub)
    _register_harvesting_commands(sub)
    _register_learning_commands(sub)
    _register_proactive_commands(sub)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
