param()

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pidDir = Join-Path $repoRoot ".planning\runtime\pids"
$stopped = 0

# Try PID files first (reliable, no false positives)
foreach ($svc in @("daemon", "mobile_api", "widget")) {
    $pidFile = Join-Path $pidDir "$svc.pid"
    if (Test-Path $pidFile) {
        try {
            $pidData = Get-Content $pidFile -Raw | ConvertFrom-Json
            $pid = [int]$pidData.pid
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            $stopped++
            Write-Output "stopped_${svc}=true (pid=$pid)"
        } catch {
            Write-Output "stopped_${svc}=failed"
        }
    }
}

# Fallback: scan for any remaining jarvis processes not covered by PID files
$targets = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and (
        $_.CommandLine -match "jarvis_engine\.main\s+daemon-run" -or
        $_.CommandLine -match "jarvis_engine\.main\s+serve-mobile" -or
        $_.CommandLine -match "jarvis_engine\.main\s+desktop-widget"
    )
})
foreach ($proc in $targets) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    $stopped++
}
Write-Output "stopped_count=$stopped"
