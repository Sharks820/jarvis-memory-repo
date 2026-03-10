"""Knowledge, brain, harvesting, and learning CLI command handlers.

Extracted from main.py to improve separation of concerns.
Contains: brain-status/context/compact/regression, knowledge-status,
contradiction-list/resolve, fact-lock, knowledge-regression,
consolidate, harvest, ingest-session, harvest-budget,
learn, cross-branch-query, flag-expired.
"""

from __future__ import annotations

import json

from jarvis_engine._bus import get_bus as _get_bus
from jarvis_engine._cli_helpers import cli_dispatch as _dispatch
from jarvis_engine.voice_extractors import escape_response

from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
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


# ---------------------------------------------------------------------------
# Brain commands
# ---------------------------------------------------------------------------


def cmd_brain_status(as_json: bool) -> int:
    result, rc = _dispatch(
        BrainStatusCommand(as_json=as_json),
        as_json=as_json,
        json_field="status",
    )
    if as_json:
        return rc
    status = result.status
    print("brain_status")
    print(f"updated_utc={status.get('updated_utc', '')}")
    branch_count = status.get("branch_count", 0)
    print(f"branch_count={branch_count}")
    branches = status.get("branches", [])
    for row in branches[:12]:
        if not isinstance(row, dict):
            continue
        print(
            f"branch={row.get('branch', '')} count={row.get('count', 0)} "
            f"last_ts={row.get('last_ts', '')} summary={row.get('last_summary', '')}"
        )
    # Structured response for UI consumption (UI-05)
    branch_names = [
        str(row.get("branch", "")) for row in branches[:6] if isinstance(row, dict)
    ]
    summary = f"Brain has {branch_count} branch(es)"
    if branch_names:
        summary += f": {', '.join(branch_names)}"
    print(f"response={summary}")
    return 0


def cmd_brain_context(query: str, max_items: int, max_chars: int, as_json: bool) -> int:
    if not query.strip():
        print("error: query is required")
        return 2
    result, rc = _dispatch(
        BrainContextCommand(
            query=query, max_items=max_items, max_chars=max_chars, as_json=as_json
        ),
        as_json=as_json,
        json_field="packet",
    )
    if as_json:
        return rc
    packet = result.packet
    print("brain_context")
    print(f"query={packet.get('query', '')}")
    print(f"selected_count={packet.get('selected_count', 0)}")
    selected = packet.get("selected", [])
    for idx, row in enumerate(selected, start=1):
        if not isinstance(row, dict):
            continue
        print(
            f"context_{idx}=branch:{row.get('branch', '')} "
            f"source:{row.get('source', '')} "
            f"kind:{row.get('kind', '')} "
            f"summary:{row.get('summary', '')}"
        )
    facts = packet.get("canonical_facts", [])
    if isinstance(facts, list):
        for idx, item in enumerate(facts, start=1):
            if not isinstance(item, dict):
                continue
            print(
                f"fact_{idx}=key:{item.get('key', '')} "
                f"value:{item.get('value', '')} "
                f"confidence:{item.get('confidence', 0.0)}"
            )
    return 0


def cmd_brain_compact(keep_recent: int, as_json: bool) -> int:
    bus_result, rc = _dispatch(
        BrainCompactCommand(keep_recent=keep_recent, as_json=as_json),
        as_json=as_json,
        json_field="result",
    )
    if as_json or rc:
        return rc
    print("brain_compact")
    for key, value in bus_result.result.items():
        print(f"{key}={value}")
    return 0


def cmd_brain_regression(as_json: bool) -> int:
    result, rc = _dispatch(
        BrainRegressionCommand(as_json=as_json),
        as_json=as_json,
        json_field="report",
    )
    if as_json or rc:
        return rc
    print("brain_regression_report")
    for key, value in result.report.items():
        print(f"{key}={value}")
    return 0


# ---------------------------------------------------------------------------
# Knowledge graph commands
# ---------------------------------------------------------------------------


def cmd_knowledge_status(as_json: bool) -> int:
    result, rc = _dispatch(KnowledgeStatusCommand(as_json=as_json))
    if rc:
        return rc
    status_dict = {
        "node_count": result.node_count,
        "edge_count": result.edge_count,
        "locked_count": result.locked_count,
        "pending_contradictions": result.pending_contradictions,
        "graph_hash": result.graph_hash,
    }
    if as_json:
        print(json.dumps(status_dict, ensure_ascii=True, indent=2))
        return 0
    print("knowledge_status")
    for key, value in status_dict.items():
        print(f"{key}={value}")
    return 0


def cmd_contradiction_list(status: str, limit: int, as_json: bool) -> int:
    result, rc = _dispatch(ContradictionListCommand(status=status, limit=limit))
    if rc:
        return rc
    if as_json:
        print(
            json.dumps(
                {"contradictions": result.contradictions},
                ensure_ascii=True,
                indent=2,
                default=str,
            )
        )
        return 0
    if not result.contradictions:
        print("No contradictions found.")
        return 0
    for c in result.contradictions:
        print(
            f"id={c.get('contradiction_id')} node={c.get('node_id')} "
            f"existing={c.get('existing_value')!r} incoming={c.get('incoming_value')!r} "
            f"status={c.get('status')} created={c.get('created_at')}"
        )
    return 0


def cmd_contradiction_resolve(
    contradiction_id: int, resolution: str, merge_value: str
) -> int:
    result = _get_bus().dispatch(
        ContradictionResolveCommand(
            contradiction_id=contradiction_id,
            resolution=resolution,
            merge_value=merge_value,
        )
    )
    if result.success:
        print(f"resolved=true node_id={result.node_id} resolution={result.resolution}")
        print(result.message)
    else:
        print("resolved=false")
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
    result, rc = _dispatch(
        KnowledgeRegressionCommand(snapshot_path=snapshot_path, as_json=as_json),
        as_json=as_json,
        json_field="report",
    )
    if as_json or rc:
        return rc
    report = result.report or {}
    status = report.get("status", "unknown")
    print(f"knowledge_regression status={status}")
    if report.get("message"):
        print(report["message"])
    for diff_entry in report.get("discrepancies", []):
        print(
            f"  [{diff_entry.get('severity')}] {diff_entry.get('type')}: {diff_entry.get('message')}"
        )
    current = report.get("current", {})
    if current:
        print(
            f"  current: nodes={current.get('node_count', 0)} edges={current.get('edge_count', 0)} "
            f"locked={current.get('locked_count', 0)} hash={current.get('graph_hash', '')}"
        )
    return 0


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


def cmd_consolidate(branch: str, max_groups: int, dry_run: bool) -> int:
    from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

    result = _get_bus().dispatch(
        ConsolidateMemoryCommand(
            branch=branch,
            max_groups=max_groups,
            dry_run=dry_run,
        )
    )
    print(f"consolidation_groups={result.groups_found}")
    print(f"consolidation_records={result.records_consolidated}")
    print(f"consolidation_new_facts={result.new_facts_created}")
    if result.errors:
        print(f"consolidation_errors={len(result.errors)}")
        for e in result.errors:
            print(f"  {e}")
    print(f"response={escape_response(result.message)}")
    return 0 if not result.errors else 2


# ---------------------------------------------------------------------------
# Harvesting commands
# ---------------------------------------------------------------------------


def cmd_harvest(topic: str, providers: str | None, max_tokens: int) -> int:
    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    result = _get_bus().dispatch(
        HarvestTopicCommand(
            topic=topic,
            providers=provider_list,
            max_tokens=max_tokens,
        )
    )
    print(f"harvest_topic={result.topic}")
    for entry in result.results:
        status = entry.get("status", "unknown")
        provider = entry.get("provider", "unknown")
        records = entry.get("records_created", 0)
        cost = entry.get("cost_usd", 0.0)
        print(
            f"provider={provider} status={status} records={records} cost_usd={cost:.6f}"
        )
    return result.return_code


def cmd_ingest_session(
    source: str, session_path: str | None, project_path: str | None
) -> int:
    result, _ = _dispatch(
        IngestSessionCommand(
            source=source,
            session_path=session_path,
            project_path=project_path,
        )
    )
    print(f"ingest_session_source={result.source}")
    print(f"sessions_processed={result.sessions_processed}")
    print(f"records_created={result.records_created}")
    return result.return_code


def cmd_harvest_budget(
    action: str,
    provider: str | None,
    period: str | None,
    limit_usd: float | None,
    limit_requests: int | None,
) -> int:
    result = _get_bus().dispatch(
        HarvestBudgetCommand(
            action=action,
            provider=provider,
            period=period,
            limit_usd=limit_usd,
            limit_requests=limit_requests,
        )
    )
    summary = result.summary
    if action == "set":
        print(
            f"budget_set provider={summary.get('provider', '')} period={summary.get('period', '')} "
            f"limit_usd={summary.get('limit_usd', 0.0)}"
        )
    else:
        print(f"budget_period_days={summary.get('period_days', 30)}")
        print(f"budget_total_cost_usd={summary.get('total_cost_usd', 0.0):.6f}")
        for entry in summary.get("providers", []):
            print(
                f"provider={entry.get('provider', '')} "
                f"cost_usd={entry.get('total_cost_usd', 0.0):.6f} "
                f"requests={entry.get('total_requests', 0)}"
            )
    return result.return_code


# ---------------------------------------------------------------------------
# Learning CLI commands
# ---------------------------------------------------------------------------


def cmd_learn(user_message: str, assistant_response: str) -> int:
    result = _get_bus().dispatch(
        LearnInteractionCommand(
            user_message=user_message,
            assistant_response=assistant_response,
            route="manual",
            topic=user_message[:100],
        )
    )
    print(f"records_created={result.records_created}")
    print(f"message={result.message}")
    return 0


def cmd_cross_branch_query(query: str, k: int) -> int:
    result = _get_bus().dispatch(
        CrossBranchQueryCommand(
            query=query,
            k=k,
        )
    )
    print(f"direct_results={len(result.direct_results)}")
    for dr in result.direct_results:
        print(
            f"  record_id={dr.get('record_id', '')} distance={dr.get('distance', 0.0):.4f}"
        )
    print(f"cross_branch_connections={len(result.cross_branch_connections)}")
    for cb in result.cross_branch_connections:
        print(
            f"  {cb.get('source_branch', '?')}->{cb.get('target_branch', '?')} relation={cb.get('relation', '')}"
        )
    print(f"branches_involved={result.branches_involved}")
    return 0


def cmd_flag_expired() -> int:
    result = _get_bus().dispatch(FlagExpiredFactsCommand())
    print(f"expired_count={result.expired_count}")
    print(f"message={result.message}")
    return 0
