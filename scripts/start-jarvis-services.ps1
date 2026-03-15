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

# Kill existing Jarvis processes FROM THIS REPO for a clean start.
# Scoped to current repo root to avoid killing processes from other installations.
$repoRootNorm = $repoRoot.ToLowerInvariant()
$allJarvis = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
    $_.CommandLine -match "jarvis_engine\.main\s+(daemon-run|serve-mobile)" -and
    ([string]$_.CommandLine).ToLowerInvariant() -like "*$repoRootNorm*"
})
foreach ($proc in $allJarvis) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

# Also kill anything holding our API port (leftover from crashed processes).
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($entry in $listeners) {
    $procId = [int]$entry.OwningProcess
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

# Clean stale PID files.
$pidDir = Join-Path $repoRoot ".planning\runtime\pids"
New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
Remove-Item -Path (Join-Path $pidDir "*.pid") -Force -ErrorAction SilentlyContinue
Remove-Item -Path (Join-Path $pidDir "*.lock") -Force -ErrorAction SilentlyContinue

# Brief pause for port release after kills.
if ($allJarvis.Count -gt 0 -or $listeners.Count -gt 0) {
    Start-Sleep -Milliseconds 500
}

# Start daemon (always fresh — we killed everything above).
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

# Start mobile API (always fresh — we killed everything above).
$mobileRunning = $false
$mobileArgs = @("-m", "jarvis_engine.main", "serve-mobile", "--host", $BindHost, "--port", "$Port", "--config-file", $configPath)
if ($BindHost -ne "127.0.0.1") {
    $mobileArgs += "--allow-insecure-bind"
}
$env:PYTHONPATH = Join-Path $engineDir "src"
Start-Process -FilePath $python -ArgumentList $mobileArgs -WorkingDirectory $engineDir -RedirectStandardOutput $mobileLogPath -RedirectStandardError $mobileErrPath -WindowStyle Hidden

# Determine scheme based on whether TLS certs exist (server auto-detects the same way).
$tlsCert = Join-Path $securityDir "tls_cert.pem"
$tlsKey = Join-Path $securityDir "tls_key.pem"
$scheme = if ((Test-Path $tlsCert) -and (Test-Path $tlsKey)) { "https" } else { "http" }

if ($StartWidget) {
    $widgetScript = Join-Path $scriptDir "start-jarvis-widget.ps1"
    if (Test-Path $widgetScript) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $widgetScript, "-Detached") -WindowStyle Hidden
    }
}

Write-Output "daemon_started=True"
Write-Output "mobile_started=True"
Write-Output "mobile_api_url=$scheme`://$BindHost`:$Port"
Write-Output "quick_panel=$scheme`://$BindHost`:$Port/quick"
Write-Output "tls=$($scheme -eq 'https')"
Write-Output "mobile_config=$configPath"
Write-Output "mobile_log=$mobileLogPath"
Write-Output "mobile_err_log=$mobileErrPath"
Write-Output "widget_started=$StartWidget"
Write-Output "next=Open /quick and store token+signing key in Secure Session once per trusted device."
