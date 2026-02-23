param(
    [int]$IntervalSeconds = 180,
    [int]$IdleIntervalSeconds = 900,
    [int]$IdleAfterSeconds = 300,
    [int]$RestartDelaySeconds = 15,
    [int]$MaxRestarts = 0,
    [int]$MaxCycles = 0,
    [string]$LogPath = "",
    [switch]$Execute,
    [switch]$ApprovePrivileged,
    [switch]$AutoOpenConnectors,
    [switch]$SkipMissions
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$engineDir = Join-Path $repoRoot "engine"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $repoRoot ".planning\logs\jarvis-daemon.log"
}
$logDir = Split-Path -Parent $LogPath
if (-not [string]::IsNullOrWhiteSpace($logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Set-Location $engineDir
$env:PYTHONPATH = "src"

$argsList = @(
    "-m", "jarvis_engine.main",
    "daemon-run",
    "--interval-s", "$IntervalSeconds",
    "--idle-interval-s", "$IdleIntervalSeconds",
    "--idle-after-s", "$IdleAfterSeconds",
    "--max-cycles", "$MaxCycles"
)
if ($Execute) { $argsList += "--execute" }
if ($ApprovePrivileged) { $argsList += "--approve-privileged" }
if ($AutoOpenConnectors) { $argsList += "--auto-open-connectors" }
if ($SkipMissions) { $argsList += "--skip-missions" }

$restartCount = 0
$exitCode = 0
while ($true) {
    Add-Content -Path $LogPath -Value "[$((Get-Date).ToString('o'))] start restart_count=$restartCount args=$($argsList -join ' ')" -Encoding utf8
    & $python @argsList 2>&1 | Out-File -FilePath $LogPath -Append -Encoding utf8
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    Add-Content -Path $LogPath -Value "[$((Get-Date).ToString('o'))] exit exit_code=$exitCode" -Encoding utf8

    if ($exitCode -eq 0) {
        break
    }

    $restartCount++
    if ($MaxRestarts -gt 0 -and $restartCount -ge $MaxRestarts) {
        break
    }

    Start-Sleep -Seconds ([Math]::Max(5, $RestartDelaySeconds))
}

exit $exitCode
