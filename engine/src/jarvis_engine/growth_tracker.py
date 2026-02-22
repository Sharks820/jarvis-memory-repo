from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


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


def load_golden_tasks(path: Path) -> list[GoldenTask]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks: list[GoldenTask] = []
    for item in raw:
        tasks.append(
            GoldenTask(
                task_id=str(item["id"]),
                prompt=str(item["prompt"]),
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
    with urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
        except URLError as exc:
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
        eval_count = int(raw.get("eval_count", 0))
        eval_duration_s = float(raw.get("eval_duration", 0)) / 1e9
        total_duration_s = float(raw.get("total_duration", 0)) / 1e9
        prompt_sha256 = hashlib.sha256(task.prompt.encode("utf-8")).hexdigest()
        response_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
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
        for line in history_path.read_text(encoding="utf-8").splitlines():
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
