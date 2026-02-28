param(
    [switch]$ForceRestart,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$engineDir = Join-Path $repoRoot "engine"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
$pythonw = if (Test-Path $venvPythonw) { $venvPythonw } elseif (Get-Command pythonw -ErrorAction SilentlyContinue) { "pythonw" } else { $python }

# Kill any existing widget processes (any Python interpreter) for a clean start.
$targets = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and $_.CommandLine -match "jarvis_engine\.main\s+desktop-widget"
})
if ($targets.Count -gt 0) {
    foreach ($proc in $targets) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 350
}
# Clean stale PID/lock files.
$pidDir = Join-Path $repoRoot ".planning\runtime\pids"
Remove-Item -Path (Join-Path $pidDir "widget.pid") -Force -ErrorAction SilentlyContinue
Remove-Item -Path (Join-Path $pidDir "widget.lock") -Force -ErrorAction SilentlyContinue

$widgetArgs = @("-m", "jarvis_engine.main", "desktop-widget")
if ($Detached) {
    $env:PYTHONPATH = "src"
    Start-Process -FilePath $pythonw -ArgumentList $widgetArgs -WorkingDirectory $engineDir -WindowStyle Hidden
    Write-Output "widget_started=true"
    exit 0
}

Set-Location $engineDir
$env:PYTHONPATH = "src"
& $pythonw @widgetArgs
