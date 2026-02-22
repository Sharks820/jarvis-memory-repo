# Capability Growth Tracking

Use this to prove Jarvis is getting stronger over time instead of guessing.

## What It Measures
- Reasoning/task quality: keyword coverage on `golden_tasks.json`.
- Speed: average tokens/second.
- Responsiveness: average total latency.
- Trend: score delta vs previous run and rolling window average.

## Run an Evaluation
From `engine/`:

```powershell
$env:PYTHONPATH = "src"
python -m jarvis_engine.main growth-eval --model qwen3:latest --think off
```

Optional:
```powershell
python -m jarvis_engine.main growth-eval --model deepseek-r1:8b --think on
# If you want to count thinking-text fallback when final response is empty:
python -m jarvis_engine.main growth-eval --model qwen3:latest --think off --accept-thinking
```

## Show Trend
```powershell
$env:PYTHONPATH = "src"
python -m jarvis_engine.main growth-report --last 20
```

## Audit Proof (Anti-Fake)
Show exact prompts/responses and hashes for a run:
```powershell
$env:PYTHONPATH = "src"
python -m jarvis_engine.main growth-audit --run-index -1
```

This prints:
- required tokens
- matched tokens
- prompt SHA256
- response SHA256
- full response text used for scoring

So you can verify scores against raw evidence.

History file:
- `.planning/capability_history.jsonl`

## Interpreting Improvement
- `latest_score_pct` up: reasoning/task alignment improved.
- `delta_vs_prev_pct` positive for multiple runs: clear capability gains.
- `window_avg_pct` rising over weeks: sustained learning effect.
- If score rises while latency/tps remains acceptable, promote model/profile.

## Recommended Loop
1. Run learning updates (memory distillation + adapter tune).
2. Run `growth-eval` on the same golden tasks.
3. Run `growth-audit` to verify no scoring fraud.
4. Promote only if score increases and safety tests pass.
5. Keep every run in history for regression detection.
