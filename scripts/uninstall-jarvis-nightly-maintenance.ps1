param(
    [string]$TaskName = "JarvisNightlyMaintenance"
)

$ErrorActionPreference = "Stop"
$removed = $false
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    $removed = $true
} catch {
    $removed = $false
}
Write-Output "removed_task=$removed"
Write-Output "task_name=$TaskName"
