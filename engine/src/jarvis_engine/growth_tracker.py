from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import logging
from jarvis_engine._shared import sha256_hex
from jarvis_engine.security.net_policy import is_safe_ollama_endpoint

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memory-recall golden task dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryRecallTask:
    """A golden task that tests memory recall by querying the memory engine."""

    task_id: str = ""
    query: str = ""
    must_find_branches: list[str] = field(default_factory=list)
    min_results: int = 1
    must_include_in_results: list[str] = field(default_factory=list)


@dataclass
class MemoryRecallResult:
    """Evaluation result for a single memory-recall golden task."""

    task_id: str = ""
    query: str = ""
    results_found: int = 0
    branches_found: list[str] = field(default_factory=list)
    branch_coverage: float = 0.0
    keyword_coverage: float = 0.0
    overall_score: float = 0.0


DEFAULT_MEMORY_TASKS = [
    # ops (2)
    MemoryRecallTask("ops_recall", "What are the owner's upcoming tasks?", ["ops"], 1, ["task"]),
    MemoryRecallTask("ops_routine_recall", "What does the owner's daily routine look like?", ["ops"], 1, ["routine"]),
    # coding (2)
    MemoryRecallTask("coding_recall", "What programming projects is the owner working on?", ["coding"], 1, ["code"]),
    MemoryRecallTask("coding_tools_recall", "What programming languages and tools does the owner use?", ["coding"], 1, ["language"]),
    # health (2)
    MemoryRecallTask("health_recall", "What medications does the owner take?", ["health"], 1, ["medication"]),
    MemoryRecallTask("health_goals_recall", "What are the owner's health and fitness goals?", ["health"], 1, ["health"]),
    # finance (2)
    MemoryRecallTask("finance_expenses_recall", "What are my monthly expenses?", ["finance"], 1, ["expense"]),
    MemoryRecallTask("finance_goals_recall", "What financial goals am I tracking?", ["finance"], 1, ["financial"]),
    # security (2)
    MemoryRecallTask("security_practices_recall", "What security practices do I follow?", ["security"], 1, ["security"]),
    MemoryRecallTask("security_devices_recall", "What are my trusted devices?", ["security"], 1, ["device"]),
    # learning (2)
    MemoryRecallTask("learning_topics_recall", "What topics am I currently learning about?", ["learning"], 1, ["learning"]),
    MemoryRecallTask("learning_missions_recall", "What learning missions are active?", ["learning"], 1, ["mission"]),
    # family (2)
    MemoryRecallTask("family_recall", "Tell me about the owner's family", ["family"], 1, ["family"]),
    MemoryRecallTask("family_events_recall", "What family events are coming up?", ["family"], 1, ["event"]),
    # communications (2)
    MemoryRecallTask("comms_contacts_recall", "Who do I communicate with most?", ["communications"], 1, ["communicate"]),
    MemoryRecallTask("comms_channels_recall", "What are my preferred communication channels?", ["communications"], 1, ["channel"]),
    # gaming (2)
    MemoryRecallTask("gaming_recall", "What games does the owner play?", ["gaming"], 1, ["game"]),
    MemoryRecallTask("gaming_progress_recall", "What is my progress in current games?", ["gaming"], 1, ["progress"]),
]

# Maps each branch name to its golden task IDs for per-branch evaluation.
BRANCH_TASK_MAP: dict[str, list[str]] = {
    "ops": ["ops_recall", "ops_routine_recall"],
    "coding": ["coding_recall", "coding_tools_recall"],
    "health": ["health_recall", "health_goals_recall"],
    "finance": ["finance_expenses_recall", "finance_goals_recall"],
    "security": ["security_practices_recall", "security_devices_recall"],
    "learning": ["learning_topics_recall", "learning_missions_recall"],
    "family": ["family_recall", "family_events_recall"],
    "communications": ["comms_contacts_recall", "comms_channels_recall"],
    "gaming": ["gaming_recall", "gaming_progress_recall"],
}

# Index DEFAULT_MEMORY_TASKS by task_id for fast lookup.
_TASK_INDEX: dict[str, MemoryRecallTask] = {t.task_id: t for t in DEFAULT_MEMORY_TASKS}


def evaluate_memory_recall(
    task: MemoryRecallTask,
    engine: Any,
    embed_service: Any,
) -> MemoryRecallResult:
    """Evaluate a single memory-recall golden task.

    Scoring:
        has_results  -> 0.3 weight
        branch_coverage -> 0.3 weight
        keyword_coverage -> 0.4 weight
    """
    embedding = embed_service.embed(task.query, prefix="search_query")
    vec_results = engine.search_vec(embedding, limit=10)

    if not vec_results:
        return MemoryRecallResult(
            task_id=task.task_id,
            query=task.query,
        )

    # Fetch full records to inspect branch + content
    record_ids = [rid for rid, _dist in vec_results]
    records = engine.get_records_batch(record_ids)

    # Detect branches
    branches_found: list[str] = []
    seen_branches: set[str] = set()
    for rec in records:
        branch = rec.get("branch", "general")
        if branch not in seen_branches:
            seen_branches.add(branch)
            branches_found.append(branch)

    # Branch coverage
    if task.must_find_branches:
        matched_branches = sum(
            1 for b in task.must_find_branches if b in seen_branches
        )
        branch_cov = matched_branches / len(task.must_find_branches)
    else:
        branch_cov = 1.0

    # Keyword coverage -- search in combined summary text
    combined_text = " ".join(
        rec.get("summary", "").lower() for rec in records
    )
    if task.must_include_in_results:
        matched_kw = sum(
            1 for kw in task.must_include_in_results if kw.lower() in combined_text
        )
        keyword_cov = matched_kw / len(task.must_include_in_results)
    else:
        keyword_cov = 1.0

    has_results_score = 0.3 if len(vec_results) >= task.min_results else 0.0
    branch_score = 0.3 * branch_cov
    keyword_score = 0.4 * keyword_cov
    overall = has_results_score + branch_score + keyword_score

    return MemoryRecallResult(
        task_id=task.task_id,
        query=task.query,
        results_found=len(vec_results),
        branches_found=branches_found,
        branch_coverage=round(branch_cov, 4),
        keyword_coverage=round(keyword_cov, 4),
        overall_score=round(overall, 4),
    )


def run_memory_eval(
    tasks: list[MemoryRecallTask],
    engine: Any,
    embed_service: Any,
) -> list[MemoryRecallResult]:
    """Evaluate all memory-recall golden tasks and return results.

    Raises RuntimeError if engine or embed_service is None.
    """
    if engine is None:
        raise RuntimeError("engine is required for memory evaluation")
    if embed_service is None:
        raise RuntimeError("embed_service is required for memory evaluation")

    results: list[MemoryRecallResult] = []
    for task in tasks:
        try:
            result = evaluate_memory_recall(task, engine, embed_service)
        except Exception as exc:
            _logger.warning("Memory recall task %s failed: %s", task.task_id, exc)
            result = MemoryRecallResult(task_id=task.task_id, query=task.query)
        results.append(result)
    return results


def eval_branch(
    branch: str,
    engine: Any,
    embed_service: Any,
) -> dict:
    """Evaluate only the golden tasks for a specific branch.

    Args:
        branch: Branch name (must be a key in BRANCH_TASK_MAP).
        engine: MemoryEngine instance.
        embed_service: Embedding service instance.

    Returns:
        Dict with ``branch``, ``task_ids``, ``results`` list, and
        ``avg_score`` (average overall_score across the branch's tasks).

    Raises:
        ValueError: If the branch is not found in BRANCH_TASK_MAP.
    """
    if branch not in BRANCH_TASK_MAP:
        raise ValueError(
            f"Unknown branch {branch!r}. Valid branches: {sorted(BRANCH_TASK_MAP)}"
        )
    task_ids = BRANCH_TASK_MAP[branch]
    tasks = [_TASK_INDEX[tid] for tid in task_ids if tid in _TASK_INDEX]
    results = run_memory_eval(tasks, engine, embed_service)
    scores = [r.overall_score for r in results]
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    return {
        "branch": branch,
        "task_ids": task_ids,
        "results": results,
        "avg_score": avg_score,
    }


@dataclass
class GoldenTask:
    task_id: str
    prompt: str
    must_include: list[str]


@dataclass
class TaskEval:
    task_id: str
    matched: int
    total: int
    coverage: float
    matched_tokens: list[str]
    required_tokens: list[str]
    prompt: str
    response: str
    prompt_sha256: str
    response_sha256: str
    response_source: str
    eval_count: int
    eval_duration_s: float
    total_duration_s: float


@dataclass
class EvalRun:
    ts: str
    model: str
    tasks: int
    score_pct: float
    avg_coverage_pct: float
    avg_tps: float
    avg_latency_s: float
    results: list[TaskEval]
    prev_run_sha256: str = ""
    run_sha256: str = ""


_history_lock = threading.RLock()


def _is_safe_ollama_endpoint(endpoint: str) -> bool:
    return is_safe_ollama_endpoint(endpoint)


def load_golden_tasks(path: Path) -> list[GoldenTask]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Golden tasks file must contain a JSON array.")
    tasks: list[GoldenTask] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not task_id or not prompt:
            continue
        tasks.append(
            GoldenTask(
                task_id=task_id,
                prompt=prompt,
                must_include=[str(x).lower() for x in item.get("must_include", [])],
            )
        )
    return tasks


def score_text(text: str, required_tokens: list[str]) -> tuple[int, int, float, list[str]]:
    lowered = text.lower()
    total = len(required_tokens)
    if total == 0:
        return 0, 0, 1.0, []
    matched_tokens = []
    for token in required_tokens:
        pattern = rf"\b{re.escape(token)}\b"
        if re.search(pattern, lowered):
            matched_tokens.append(token)
    matched = len(matched_tokens)
    return matched, total, matched / total, matched_tokens


def _generate(
    endpoint: str,
    model: str,
    prompt: str,
    *,
    num_predict: int,
    temperature: float,
    think: bool | None,
    timeout_s: int,
) -> dict[str, Any]:
    if not _is_safe_ollama_endpoint(endpoint):
        raise ValueError(f"Unsafe Ollama endpoint: {endpoint}")

    effective_prompt = prompt
    if think is False:
        effective_prompt = "/nothink\n" + prompt
    elif think is True:
        effective_prompt = "/think\n" + prompt

    options: dict[str, Any] = {
        "num_ctx": 4096,
        "num_predict": num_predict,
        "temperature": temperature,
    }

    payload = {
        "model": model,
        "prompt": effective_prompt,
        "stream": False,
        "options": options,
    }
    req = Request(
        url=f"{endpoint.rstrip('/')}/api/generate",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with urlopen(req, timeout=timeout_s) as resp:  # nosec B310
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from Ollama")
    return data


def run_eval(
    *,
    endpoint: str,
    model: str,
    tasks: list[GoldenTask],
    num_predict: int = 256,
    temperature: float = 0.0,
    think: bool | None = None,
    accept_thinking: bool = False,
    timeout_s: int = 120,
) -> EvalRun:
    results: list[TaskEval] = []

    for task in tasks:
        try:
            raw = _generate(
                endpoint=endpoint,
                model=model,
                prompt=task.prompt,
                num_predict=num_predict,
                temperature=temperature,
                think=think,
                timeout_s=timeout_s,
            )
        except (URLError, ValueError, TimeoutError) as exc:
            raise RuntimeError(f"Failed to reach Ollama at {endpoint}: {exc}") from exc

        response_text = str(raw.get("response", ""))
        thinking_text = str(raw.get("thinking", ""))
        if response_text.strip():
            output = response_text
            response_source = "response"
        elif accept_thinking and thinking_text.strip():
            output = thinking_text
            response_source = "thinking"
        else:
            output = ""
            response_source = "empty"
        matched, total, coverage, matched_tokens = score_text(output, task.must_include)
        eval_count = int(raw.get("eval_count") or 0)
        eval_duration_s = float(raw.get("eval_duration") or 0) / 1e9
        total_duration_s = float(raw.get("total_duration") or 0) / 1e9
        prompt_sha256 = sha256_hex(task.prompt)
        response_sha256 = sha256_hex(output)
        results.append(
            TaskEval(
                task_id=task.task_id,
                matched=matched,
                total=total,
                coverage=coverage,
                matched_tokens=matched_tokens,
                required_tokens=task.must_include,
                prompt=task.prompt,
                response=output,
                prompt_sha256=prompt_sha256,
                response_sha256=response_sha256,
                response_source=response_source,
                eval_count=eval_count,
                eval_duration_s=eval_duration_s,
                total_duration_s=total_duration_s,
            )
        )

    if not results:
        raise RuntimeError("No tasks were evaluated.")

    avg_coverage = sum(r.coverage for r in results) / len(results)
    score_pct = round(avg_coverage * 100.0, 2)

    tps_values = []
    for r in results:
        if r.eval_duration_s > 0:
            tps_values.append(r.eval_count / r.eval_duration_s)
    avg_tps = round(sum(tps_values) / len(tps_values), 2) if tps_values else 0.0
    avg_latency = round(sum(r.total_duration_s for r in results) / len(results), 3)

    return EvalRun(
        ts=datetime.now(UTC).isoformat(),
        model=model,
        tasks=len(results),
        score_pct=score_pct,
        avg_coverage_pct=round(avg_coverage * 100.0, 2),
        avg_tps=avg_tps,
        avg_latency_s=avg_latency,
        results=results,
    )


def append_history(history_path: Path, run: EvalRun) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with _history_lock:
        rows = read_history(history_path)
        prev_hash = str(rows[-1].get("run_sha256", "")) if rows else ""
        row = asdict(run)
        row["results"] = [asdict(r) for r in run.results]
        row["prev_run_sha256"] = prev_hash
        row["run_sha256"] = ""
        row["run_sha256"] = compute_run_sha256(row)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def read_history(history_path: Path) -> list[dict[str, Any]]:
    with _history_lock:
        if not history_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with history_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


def summarize_history(rows: list[dict[str, Any]], last: int = 10) -> dict[str, Any]:
    if not rows:
        return {
            "runs": 0,
            "latest_score_pct": 0.0,
            "delta_vs_prev_pct": 0.0,
            "latest_model": "",
            "latest_ts": "",
            "window_avg_pct": 0.0,
        }

    window = rows[-last:]
    latest = window[-1]
    prev = window[-2] if len(window) > 1 else None
    latest_score = float(latest.get("score_pct", 0.0))
    prev_score = float(prev.get("score_pct", latest_score)) if prev else latest_score
    delta = round(latest_score - prev_score, 2)
    window_avg = round(sum(float(x.get("score_pct", 0.0)) for x in window) / len(window), 2)

    return {
        "runs": len(rows),
        "latest_score_pct": latest_score,
        "delta_vs_prev_pct": delta,
        "latest_model": str(latest.get("model", "")),
        "latest_ts": str(latest.get("ts", "")),
        "window_avg_pct": window_avg,
    }


def audit_run(rows: list[dict[str, Any]], run_index: int = -1) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("No history runs found.")
    if run_index >= len(rows) or run_index < -len(rows):
        raise RuntimeError(f"Invalid run index: {run_index}")
    validate_history_chain(rows)
    run = rows[run_index]
    return {
        "ts": run.get("ts", ""),
        "model": run.get("model", ""),
        "score_pct": run.get("score_pct", 0.0),
        "tasks": run.get("tasks", 0),
        "results": run.get("results", []),
        "run_sha256": run.get("run_sha256", ""),
        "prev_run_sha256": run.get("prev_run_sha256", ""),
    }


def compute_run_sha256(row: dict[str, Any]) -> str:
    canonical = dict(row)
    canonical["run_sha256"] = ""
    payload = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_history_chain(rows: list[dict[str, Any]]) -> None:
    prev_hash = ""
    for idx, row in enumerate(rows):
        current = str(row.get("run_sha256", ""))
        if not current:
            # Legacy rows (pre-chain format) are allowed; restart chain from next sealed row.
            prev_hash = ""
            continue
        row_prev = str(row.get("prev_run_sha256", ""))
        if row_prev != prev_hash:
            raise RuntimeError(f"History chain mismatch at index {idx}: prev hash mismatch.")
        expected = compute_run_sha256(row)
        if current != expected:
            raise RuntimeError(f"History chain mismatch at index {idx}: run hash mismatch.")
        prev_hash = current
