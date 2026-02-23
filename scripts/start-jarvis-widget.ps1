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

$targets = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and $_.CommandLine -match "jarvis_engine\.main\s+desktop-widget"
})
if ($targets.Count -gt 0 -and -not $ForceRestart) {
    Write-Output "widget_running=true"
    Write-Output "next=Use existing launcher bubble (JU) or Ctrl+Space."
    exit 0
}
if ($targets.Count -gt 0 -and $ForceRestart) {
    foreach ($proc in $targets) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 350
}

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
