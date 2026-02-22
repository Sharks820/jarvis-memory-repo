param(
    [string]$GoldenFile = "C:\Users\Conner\jarvis-memory-repo\.planning\golden_tasks.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $GoldenFile)) {
    throw "Golden task file not found: $GoldenFile"
}

$tasks = Get-Content -Path $GoldenFile -Raw | ConvertFrom-Json

Write-Output "eval_gate_status=placeholder"
Write-Output ("golden_task_count=" + $tasks.Count)
Write-Output "note=Wire this script to your real evaluator before promotion."

