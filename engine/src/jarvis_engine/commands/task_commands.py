"""Command dataclasses for task operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunTaskCommand:
    task_type: str
    prompt: str
    execute: bool = False
    approve_privileged: bool = False
    model: str = "qwen3-coder:30b"
    endpoint: str = "http://127.0.0.1:11434"
    quality_profile: str = "max_quality"
    output_path: str | None = None


@dataclass
class RunTaskResult:
    allowed: bool = False
    provider: str = ""
    plan: str = ""
    reason: str = ""
    output_path: str | None = None
    output_text: str | None = None
    return_code: int = 0
    auto_ingest_record_id: str = ""


@dataclass(frozen=True)
class RouteCommand:
    risk: str = "low"
    complexity: str = "normal"
    query: str = ""


@dataclass
class RouteResult:
    provider: str = ""
    reason: str = ""


@dataclass(frozen=True)
class QueryCommand:
    query: str
    model: str | None = None
    max_tokens: int = 1024
    system_prompt: str = ""
    history: tuple[tuple[str, str], ...] = ()  # ((role, content), ...) for multi-turn


@dataclass
class QueryResult:
    text: str = ""
    model: str = ""
    provider: str = ""
    route_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    fallback_used: bool = False
    fallback_reason: str = ""
    return_code: int = 0


@dataclass(frozen=True)
class WebResearchCommand:
    query: str
    max_results: int = 8
    max_pages: int = 6
    auto_ingest: bool = True


@dataclass
class WebResearchResult:
    return_code: int = 0
    report: dict[str, Any] = field(default_factory=dict)
    auto_ingest_record_id: str = ""
