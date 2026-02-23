param(
    [int]$KeepRecent = 1800,
    [string]$SnapshotNote = "nightly"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$engineDir = Join-Path $repoRoot "engine"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
$logDir = Join-Path $repoRoot ".planning\logs"
$logPath = Join-Path $logDir "nightly-maintenance.log"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Set-Location $engineDir
$env:PYTHONPATH = "src"

$cmd = @(
    "-m", "jarvis_engine.main",
    "memory-maintenance",
    "--keep-recent", "$KeepRecent",
    "--snapshot-note", "$SnapshotNote"
)

Add-Content -Path $logPath -Value "[$((Get-Date).ToString('o'))] nightly_maintenance_start" -Encoding utf8
& $python @cmd 2>&1 | Out-File -FilePath $logPath -Append -Encoding utf8
$exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
Add-Content -Path $logPath -Value "[$((Get-Date).ToString('o'))] nightly_maintenance_exit=$exitCode" -Encoding utf8
exit $exitCode
