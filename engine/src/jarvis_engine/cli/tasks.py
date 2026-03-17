"""Task and research CLI command handlers.

Extracted from main.py to improve file health and separation of concerns.
Contains: route, run-task, web-research.
"""

from __future__ import annotations

from jarvis_engine._bus import get_bus as _get_bus
from jarvis_engine._cli_helpers import cli_dispatch
from jarvis_engine.voice.extractors import escape_response

from jarvis_engine.commands.task_commands import (
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
)


def cmd_route(risk: str, complexity: str) -> int:
    result, _ = cli_dispatch(RouteCommand(risk=risk, complexity=complexity))
    print(f"provider={result.provider}")
    print(f"reason={result.reason}")
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


def cmd_web_research(query: str, *, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    cleaned = query.strip()
    if not cleaned:
        print("error: query is required for web research.")
        return 2
    result = _get_bus().dispatch(WebResearchCommand(
        query=cleaned, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
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
        print("response=" + escape_response("Here's what I found: " + " | ".join(summary_parts)))
    else:
        _query = report.get("query", "")
        print("response=" + escape_response(f"I searched the web for '{_query}' but couldn't find clear results."))

    if result.auto_ingest_record_id:
        print(f"auto_ingest_record_id={result.auto_ingest_record_id}")
    return 0
