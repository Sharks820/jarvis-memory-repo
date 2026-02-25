param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8787,
    [int]$IntervalSeconds = 120,
    [int]$IdleIntervalSeconds = 900,
    [int]$IdleAfterSeconds = 300,
    [switch]$Execute,
    [switch]$ApprovePrivileged,
    [switch]$AutoOpenConnectors,
    [switch]$SkipMissions,
    [switch]$StartWidget
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$engineDir = Join-Path $repoRoot "engine"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$securityDir = Join-Path $repoRoot ".planning\security"
$logsDir = Join-Path $repoRoot ".planning\logs"
$configPath = Join-Path $securityDir "mobile_api.json"
$mobileLogPath = Join-Path $logsDir "mobile-api.log"
$mobileErrPath = Join-Path $logsDir "mobile-api.err.log"

New-Item -ItemType Directory -Path $securityDir -Force | Out-Null
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

if (-not (Test-Path $configPath)) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $b1 = New-Object byte[] 48
    $b2 = New-Object byte[] 64
    $rng.GetBytes($b1)
    $rng.GetBytes($b2)
    $payload = @{
        token = [Convert]::ToBase64String($b1)
        signing_key = [Convert]::ToBase64String($b2)
        created_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    $payload | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$token = [string]$config.token
$signingKey = [string]$config.signing_key
if ([string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($signingKey)) {
    throw "Invalid mobile API config: $configPath"
}

# Cull stale daemon/mobile processes from other repos/interpreters first.
$repoRootNorm = $repoRoot.ToLowerInvariant()

$daemonTargets = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -match "jarvis_engine\.main\s+daemon-run"
})
foreach ($proc in $daemonTargets) {
    $cmd = [string]$proc.CommandLine
    $cmdNorm = $cmd.ToLowerInvariant()
    if ($cmdNorm -notlike "*$repoRootNorm*") {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

$mobileTargets = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -match "jarvis_engine\.main\s+serve-mobile"
})
foreach ($proc in $mobileTargets) {
    $cmd = [string]$proc.CommandLine
    $cmdNorm = $cmd.ToLowerInvariant()
    if ($cmdNorm -notlike "*$repoRootNorm*") {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

# If anything still holds our configured API port and is not our current repo runtime, stop it.
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($entry in $listeners) {
    $pid = [int]$entry.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid" -ErrorAction SilentlyContinue
    if ($null -eq $proc) { continue }
    $cmd = [string]$proc.CommandLine
    $cmdNorm = $cmd.ToLowerInvariant()
    if ($proc.Name -eq "python.exe" -and $cmd -match "jarvis_engine\.main\s+serve-mobile") {
        if ($cmdNorm -notlike "*$repoRootNorm*") {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
}

# Start daemon if not already running from this repo/interpreter.
$daemonRunning = @(
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -match "jarvis_engine\.main\s+daemon-run" -and
        [string]$_.CommandLine -like "*$repoRoot*"
    }
).Count -gt 0
if (-not $daemonRunning) {
    $daemonArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", (Join-Path $scriptDir "start-jarvis-daemon.ps1"),
        "-IntervalSeconds", "$IntervalSeconds",
        "-IdleIntervalSeconds", "$IdleIntervalSeconds",
        "-IdleAfterSeconds", "$IdleAfterSeconds"
    )
    if ($Execute) { $daemonArgs += "-Execute" }
    if ($ApprovePrivileged) { $daemonArgs += "-ApprovePrivileged" }
    if ($AutoOpenConnectors) { $daemonArgs += "-AutoOpenConnectors" }
    if ($SkipMissions) { $daemonArgs += "-SkipMissions" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $daemonArgs -WindowStyle Hidden
}

# Start mobile API if not already running (PID file check, then fallback to process scan).
$pidDir = Join-Path $repoRoot ".planning\runtime\pids"
New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
$mobilePid = Join-Path $pidDir "mobile_api.pid"
$mobileRunning = $false
if (Test-Path $mobilePid) {
    try {
        $pidData = Get-Content $mobilePid -Raw | ConvertFrom-Json
        $proc = Get-Process -Id $pidData.pid -ErrorAction SilentlyContinue
        if ($null -ne $proc -and -not $proc.HasExited) { $mobileRunning = $true }
    } catch { }
}
if (-not $mobileRunning) {
    # Fallback: scan processes
    $mobileRunning = @(
        Get-CimInstance Win32_Process | Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -match "jarvis_engine\.main\s+serve-mobile" -and
            [string]$_.CommandLine -like "*$repoRoot*"
        }
    ).Count -gt 0
}
if (-not $mobileRunning) {
    # Use --config-file to avoid exposing token/signing-key in process CommandLine
    $mobileArgs = @("-m", "jarvis_engine.main", "serve-mobile", "--host", $BindHost, "--port", "$Port", "--config-file", $configPath)
    if ($BindHost -ne "127.0.0.1") {
        $mobileArgs += "--allow-insecure-bind"
    }
    $env:PYTHONPATH = Join-Path $engineDir "src"
    Start-Process -FilePath $python -ArgumentList $mobileArgs -WorkingDirectory $engineDir -RedirectStandardOutput $mobileLogPath -RedirectStandardError $mobileErrPath -WindowStyle Hidden
}

if ($StartWidget) {
    $widgetScript = Join-Path $scriptDir "start-jarvis-widget.ps1"
    if (Test-Path $widgetScript) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $widgetScript, "-Detached") -WindowStyle Hidden
    }
}

Write-Output "daemon_running=$daemonRunning"
Write-Output "mobile_running=$mobileRunning"
Write-Output "mobile_api_url=http://$BindHost`:$Port"
Write-Output "quick_panel=http://$BindHost`:$Port/quick"
Write-Output "mobile_config=$configPath"
Write-Output "mobile_log=$mobileLogPath"
Write-Output "mobile_err_log=$mobileErrPath"
Write-Output "widget_started=$StartWidget"
Write-Output "next=Open /quick and store token+signing key in Secure Session once per trusted device."
