param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $ArchivePath)) {
    throw "Archive not found: $ArchivePath"
}

Expand-Archive -Path $ArchivePath -DestinationPath $repoRoot -Force
Write-Output "restore_complete_from=$ArchivePath"

