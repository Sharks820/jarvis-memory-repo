param(
    [string]$OutputDir = "C:\Users\Conner\jarvis-memory-repo\backups"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipPath = Join-Path $OutputDir ("jarvis-state-" + $timestamp + ".zip")
$manifestPath = Join-Path $OutputDir "manifest.jsonl"

$includePaths = @(
    (Join-Path $repoRoot ".planning"),
    (Join-Path $repoRoot "engine"),
    (Join-Path $repoRoot "docs"),
    (Join-Path $repoRoot "JARVIS_MASTERPLAN.md"),
    (Join-Path $repoRoot "AGENTS.md")
)

Compress-Archive -Path $includePaths -DestinationPath $zipPath -CompressionLevel Optimal -Force

$hash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash
$entry = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("o")
    archive = $zipPath
    sha256 = $hash
}
$entry | ConvertTo-Json -Compress | Add-Content -Path $manifestPath

Write-Output "backup_created=$zipPath"
Write-Output "sha256=$hash"

