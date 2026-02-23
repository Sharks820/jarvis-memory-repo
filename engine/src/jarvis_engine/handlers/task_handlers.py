"""Task handler classes -- adapter shims delegating to existing functions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from jarvis_engine.commands.task_commands import (
    RouteCommand,
    RouteResult,
    RunTaskCommand,
    RunTaskResult,
    WebResearchCommand,
    WebResearchResult,
)


class RunTaskHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: RunTaskCommand) -> RunTaskResult:
        from jarvis_engine.memory_store import MemoryStore
        from jarvis_engine.task_orchestrator import TaskOrchestrator, TaskRequest

        store = MemoryStore(self._root)
        orchestrator = TaskOrchestrator(store, self._root)
        result = orchestrator.run(
            TaskRequest(
                task_type=cmd.task_type,  # type: ignore[arg-type]
                prompt=cmd.prompt,
                execute=cmd.execute,
                has_explicit_approval=cmd.approve_privileged,
                model=cmd.model,
                endpoint=cmd.endpoint,
                quality_profile=cmd.quality_profile,
                output_path=cmd.output_path,
            )
        )
        auto_id = ""
        try:
            from jarvis_engine import main as _main_mod

            auto_id = _main_mod._auto_ingest_memory(
                source="task_outcome",
                kind="episodic",
                task_id=f"task-{cmd.task_type}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                content=(
                    f"Task type={cmd.task_type}; execute={cmd.execute}; approved={cmd.approve_privileged}; "
                    f"allowed={result.allowed}; provider={result.provider}; reason={result.reason}; "
                    f"prompt={cmd.prompt[:400]}"
                ),
            )
        except Exception:
            pass
        return RunTaskResult(
            allowed=result.allowed,
            provider=result.provider,
            plan=result.plan,
            reason=result.reason,
            output_path=result.output_path,
            output_text=result.output_text,
            return_code=0 if result.allowed else 2,
            auto_ingest_record_id=auto_id,
        )


class RouteHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: RouteCommand) -> RouteResult:
        from jarvis_engine.config import load_config
        from jarvis_engine.router import ModelRouter

        config = load_config()
        router = ModelRouter(cloud_burst_enabled=config.cloud_burst_enabled)
        decision = router.route(risk=cmd.risk, complexity=cmd.complexity)
        return RouteResult(provider=decision.provider, reason=decision.reason)


class WebResearchHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: WebResearchCommand) -> WebResearchResult:
        from jarvis_engine.web_research import run_web_research

        cleaned = cmd.query.strip()
        if not cleaned:
            return WebResearchResult(return_code=2)
        try:
            report = run_web_research(
                cleaned,
                max_results=max(2, min(cmd.max_results, 20)),
                max_pages=max(1, min(cmd.max_pages, 20)),
                max_summary_lines=6,
            )
        except ValueError:
            return WebResearchResult(return_code=2)
        except Exception:
            return WebResearchResult(return_code=2)

        auto_id = ""
        summary_lines = report.get("summary_lines", [])
        findings = report.get("findings", [])
        if cmd.auto_ingest and isinstance(summary_lines, list) and summary_lines:
            try:
                from jarvis_engine import main as _main_mod

                lines = []
                for line in summary_lines[:6]:
                    value = str(line).strip()
                    if value:
                        lines.append(f"- {value}")
                if lines:
                    top_domains = []
                    if isinstance(findings, list):
                        for row in findings[:4]:
                            if isinstance(row, dict):
                                domain = str(row.get("domain", "")).strip()
                                if domain:
                                    top_domains.append(domain)
                    domain_text = ", ".join(dict.fromkeys(top_domains))
                    auto_id = _main_mod._auto_ingest_memory(
                        source="task_outcome",
                        kind="semantic",
                        task_id=f"web-research-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                        content=(
                            f"Web research query: {cleaned}\n"
                            f"Top domains: {domain_text}\n"
                            f"Findings:\n" + "\n".join(lines)
                        ),
                    )
            except Exception:
                pass
        return WebResearchResult(return_code=0, report=report, auto_ingest_record_id=auto_id)
