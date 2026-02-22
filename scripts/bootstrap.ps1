param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"

if (-not (Test-Path $venvPath)) {
    python -m venv $venvPath
}

$pythonExe = Join-Path $venvPath "Scripts\\python.exe"

if (-not $SkipInstall) {
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -e (Join-Path $repoRoot "engine")
}

& $pythonExe -m jarvis_engine.main status

