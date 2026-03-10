"""Ops-related CLI command handlers.

Extracted from main.py to improve separation of concerns.
Contains: ops-brief, ops-sync, ops-export-actions, ops-autopilot,
automation-run, missions, growth eval/report/audit, intelligence dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.config import repo_root
from jarvis_engine.voice_extractors import escape_response
from jarvis_engine._bus import get_bus as _get_bus
from jarvis_engine._cli_helpers import cli_dispatch as _dispatch

from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    GrowthAuditCommand,
    GrowthEvalCommand,
    GrowthReportCommand,
    IntelligenceDashboardCommand,
    MissionCancelCommand,
    MissionCreateCommand,
    MissionRunCommand,
    MissionStatusCommand,
    OpsAutopilotCommand,
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
)


def cmd_ops_brief(snapshot_path: Path, output_path: Path | None) -> int:
    result, _ = _dispatch(
        OpsBriefCommand(snapshot_path=snapshot_path, output_path=output_path)
    )
    print(result.brief)
    if result.saved_path:
        print(f"brief_saved={result.saved_path}")
    return 0


def cmd_ops_export_actions(snapshot_path: Path, actions_path: Path) -> int:
    result, _ = _dispatch(
        OpsExportActionsCommand(snapshot_path=snapshot_path, actions_path=actions_path)
    )
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
        from jarvis_engine._shared import load_json_file

        raw = load_json_file(output_path, {}, expected_type=dict)
        prompts_raw = raw.get("connector_prompts", [])
        if not isinstance(prompts_raw, list):
            prompts_raw = []
        raw["connector_prompts"] = prompts_raw
        prompts = prompts_raw
        for item in prompts:
            if not isinstance(item, dict):
                continue
            print(
                "connector_prompt "
                f"id={item.get('connector_id', '')} "
                f'voice="{item.get("option_voice", "")}" '
                f"tap={item.get('option_tap_url', '')}"
            )
    return 0


def cmd_ops_autopilot(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    result = _get_bus().dispatch(
        OpsAutopilotCommand(
            snapshot_path=snapshot_path,
            actions_path=actions_path,
            execute=execute,
            approve_privileged=approve_privileged,
            auto_open_connectors=auto_open_connectors,
        )
    )
    return result.return_code


def cmd_automation_run(
    actions_path: Path, approve_privileged: bool, execute: bool
) -> int:
    result = _get_bus().dispatch(
        AutomationRunCommand(
            actions_path=actions_path,
            approve_privileged=approve_privileged,
            execute=execute,
        )
    )
    for out in result.outcomes:
        print(
            f"title={out.title} allowed={out.allowed} executed={out.executed} "
            f"return_code={out.return_code} reason={out.reason}"
        )
        if out.stderr:
            print(f"stderr={out.stderr.strip()}")
    return 0


def cmd_mission_create(topic: str, objective: str, sources: list[str]) -> int:
    result = _get_bus().dispatch(
        MissionCreateCommand(topic=topic, objective=objective, sources=sources)
    )
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
        print("learning_missions_active=false")
        print("learning_mission_count=0")
        print("response=No active learning missions at the moment.")
    else:
        counts = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "other": 0,
        }
        active_count = 0
        for mission in result.missions:
            status = str(mission.get("status", "")).strip().lower()
            if status in ("pending", "running"):
                active_count += 1
            if status in counts:
                counts[status] += 1
            else:
                counts["other"] += 1

        print(f"learning_mission_count={result.total_count}")
        print(f"learning_missions_active={'true' if active_count > 0 else 'false'}")
        print(f"learning_missions_active_count={active_count}")
        print(f"learning_missions_pending={counts['pending']}")
        print(f"learning_missions_running={counts['running']}")
        print(f"learning_missions_completed={counts['completed']}")
        print(f"learning_missions_failed={counts['failed']}")
        print(f"learning_missions_cancelled={counts['cancelled']}")

        summary_parts: list[str] = []
        for mission in result.missions:
            mission_id = str(mission.get("mission_id", ""))
            status = str(mission.get("status", ""))
            progress_pct = int(mission.get("progress_pct", 0) or 0)
            topic = str(mission.get("topic", ""))
            findings = int(mission.get("verified_findings", 0) or 0)
            updated_utc = str(mission.get("updated_utc", ""))
            status_detail = str(mission.get("status_detail", "")).strip()

            print(
                f"mission_id={mission_id} "
                f"status={status} "
                f"progress_pct={progress_pct} "
                f"topic={topic} "
                f"verified_findings={findings} "
                f"updated_utc={updated_utc}"
            )
            if status_detail:
                print(f"mission_status_detail={status_detail}")
            if mission.get("progress_bar"):
                print(f"progress_bar={mission.get('progress_bar', '')}")

            summary = f"{topic} ({status}, {progress_pct}%, {findings} findings)"
            if status_detail:
                summary += f" — {status_detail}"
            summary_parts.append(summary)

        print(
            f"response=Learning missions ({result.total_count} total, {active_count} active): "
            + " | ".join(summary_parts)
        )
    return 0


def cmd_mission_cancel(mission_id: str) -> int:
    result = _get_bus().dispatch(MissionCancelCommand(mission_id=mission_id))
    if not result.cancelled:
        print(f"error: {result.message or 'cancel failed'}")
        print(f"response=Could not cancel mission: {result.message or 'unknown error'}")
        return 2
    mission = result.mission
    print("mission_cancelled=true")
    print(f"mission_id={mission.get('mission_id', '')}")
    print(f"topic={mission.get('topic', '')}")
    print(f"response=Cancelled mission: {mission.get('topic', '')}")
    return 0


def cmd_mission_run(
    mission_id: str, max_results: int, max_pages: int, auto_ingest: bool
) -> int:
    result = _get_bus().dispatch(
        MissionRunCommand(
            mission_id=mission_id,
            max_results=max_results,
            max_pages=max_pages,
            auto_ingest=auto_ingest,
        )
    )
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
            statement = (
                str(finding.get("statement", "")) if isinstance(finding, dict) else ""
            )
            sources = (
                ",".join(finding.get("source_domains", []))
                if isinstance(finding, dict)
                else ""
            )
            print(f"verified_{idx}={statement}")
            print(f"verified_{idx}_sources={sources}")

    if result.ingested_record_id:
        print(f"mission_ingested_record_id={result.ingested_record_id}")
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
    result = _get_bus().dispatch(
        GrowthEvalCommand(
            model=model,
            endpoint=endpoint,
            tasks_path=tasks_path,
            history_path=history_path,
            num_predict=num_predict,
            temperature=temperature,
            think=think,
            accept_thinking=accept_thinking,
            timeout_s=timeout_s,
        )
    )
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
    result = _get_bus().dispatch(
        GrowthReportCommand(history_path=history_path, last=last)
    )
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
    result = _get_bus().dispatch(
        GrowthAuditCommand(history_path=history_path, run_index=run_index)
    )
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
        print(f"response={escape_response(audit_result.get('response', ''))}")
    return 0


def cmd_intelligence_dashboard(last_runs: int, output_path: str, as_json: bool) -> int:
    result = _get_bus().dispatch(
        IntelligenceDashboardCommand(
            last_runs=last_runs, output_path=output_path, as_json=as_json
        )
    )
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
        print(f"rank_{idx}={item.get('name', '')}:{item.get('score_pct', 0.0)}")
    for row in etas:
        eta = row.get("eta", {})
        print(
            "eta "
            f"target={row.get('target_name', '')} "
            f"target_score_pct={row.get('target_score_pct', 0.0)} "
            f"status={eta.get('status', '')} "
            f"runs={eta.get('runs', '')} "
            f"days={eta.get('days', '')}"
        )
    new_unlocks = achievements.get("new", [])
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
        out.write_text(
            json.dumps(dashboard, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        print(f"dashboard_saved={out}")
    return 0
