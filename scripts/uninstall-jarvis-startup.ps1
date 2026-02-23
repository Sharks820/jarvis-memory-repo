param(
    [string]$TaskName = "JarvisDaemon"
)

$ErrorActionPreference = "Stop"
$startupLauncherPath = Join-Path ([Environment]::GetFolderPath("Startup")) "$TaskName.cmd"
$removedTask = $false
$removedLauncher = $false

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    $removedTask = $true
}

if (Test-Path $startupLauncherPath) {
    Remove-Item -Path $startupLauncherPath -Force -ErrorAction Stop
    $removedLauncher = $true
}

Write-Output "removed_task=$removedTask"
Write-Output "removed_startup_launcher=$removedLauncher"
Write-Output "startup_launcher=$startupLauncherPath"
Write-Output "task_name=$TaskName"
