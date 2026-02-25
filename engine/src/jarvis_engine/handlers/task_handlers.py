"""Task handler classes -- adapter shims delegating to existing functions."""

from __future__ import annotations

import logging
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path

logger = logging.getLogger(__name__)

from jarvis_engine.commands.task_commands import (
    QueryCommand,
    QueryResult,
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
        self._store: object | None = None
        self._orchestrator: object | None = None

    def _get_orchestrator(self) -> object:
        """Lazily create and cache MemoryStore + TaskOrchestrator."""
        if self._orchestrator is None:
            from jarvis_engine.memory_store import MemoryStore
            from jarvis_engine.task_orchestrator import TaskOrchestrator

            self._store = MemoryStore(self._root)
            self._orchestrator = TaskOrchestrator(self._store, self._root)
        return self._orchestrator

    def handle(self, cmd: RunTaskCommand) -> RunTaskResult:
        from jarvis_engine.task_orchestrator import TaskRequest

        orchestrator = self._get_orchestrator()
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
        except Exception as exc:
            logger.debug("Auto-ingest failed for task %s: %s", cmd.task_type, exc)
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
    def __init__(self, root: Path, classifier: object | None = None) -> None:
        self._root = root
        self._classifier = classifier

    def handle(self, cmd: RouteCommand) -> RouteResult:
        # New path: query-based routing via IntentClassifier
        if cmd.query and self._classifier is not None:
            route_name, model_name, confidence = self._classifier.classify(cmd.query)
            return RouteResult(
                provider=model_name,
                reason=f"Intent: {route_name} (confidence={confidence:.2f})",
            )

        # Legacy path: risk/complexity routing via ModelRouter
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
            logger.warning("Web research failed for query %r", cleaned, exc_info=True)
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
            except Exception as exc:
                logger.debug("Auto-ingest failed for web research: %s", exc)
        return WebResearchResult(return_code=0, report=report, auto_ingest_record_id=auto_id)


class QueryHandler:
    """Handle QueryCommand by dispatching through ModelGateway with optional auto-routing."""

    def __init__(self, gateway: object, classifier: object | None = None) -> None:
        self._gateway = gateway
        self._classifier = classifier

    def handle(self, cmd: QueryCommand) -> QueryResult:
        from jarvis_engine.gateway.models import ModelGateway, GatewayResponse

        gateway: ModelGateway = self._gateway  # type: ignore[assignment]

        # Determine model
        route_reason = ""
        if cmd.model is not None:
            model = cmd.model
            route_reason = f"Explicit model: {model}"
        elif self._classifier is not None:
            route_name, model, confidence = self._classifier.classify(cmd.query)
            route_reason = f"Intent: {route_name} (confidence={confidence:.2f})"
        else:
            # Fallback default model when no classifier is wired in.
            from jarvis_engine.config import load_config
            model = load_config().default_query_model
            route_reason = "Default: no classifier available"

        # Build messages with optional conversation history
        messages: list[dict[str, str]] = []
        if cmd.system_prompt:
            messages.append({"role": "system", "content": cmd.system_prompt})
        # Inject conversation history for multi-turn context
        if cmd.history:
            for role, content in cmd.history:
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": cmd.query})

        # Call gateway
        try:
            resp: GatewayResponse = gateway.complete(
                messages=messages,
                model=model,
                max_tokens=cmd.max_tokens,
                route_reason=route_reason,
            )
        except (ConnectionError, RuntimeError, OSError, Exception) as exc:
            logger.error("QueryHandler gateway.complete failed: %s", exc, exc_info=True)
            return QueryResult(
                text=f"error: query failed ({type(exc).__name__})",
                route_reason=route_reason,
                return_code=2,
            )

        return QueryResult(
            text=resp.text,
            model=resp.model,
            provider=resp.provider,
            route_reason=route_reason,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd,
            fallback_used=resp.fallback_used,
            fallback_reason=resp.fallback_reason,
            return_code=0,
        )
