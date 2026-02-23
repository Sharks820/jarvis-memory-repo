param(
    [string]$TaskName = "JarvisDaemon",
    [int]$IntervalSeconds = 180,
    [int]$IdleIntervalSeconds = 900,
    [int]$IdleAfterSeconds = 300,
    [int]$DaemonMaxCycles = 0,
    [int]$RestartCount = 999,
    [int]$RestartIntervalSeconds = 60,
    [int]$LauncherRestartDelaySeconds = 15,
    [int]$LauncherMaxRestarts = 0,
    [string]$LogPath = "",
    [switch]$RunElevated,
    [switch]$Execute,
    [switch]$ApprovePrivileged,
    [switch]$AutoOpenConnectors,
    [switch]$SkipMissions,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupScript = Join-Path $scriptDir "start-jarvis-daemon.ps1"

if (-not (Test-Path $startupScript)) {
    throw "Missing startup script: $startupScript"
}

$argParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$startupScript`"",
    "-IntervalSeconds", "$IntervalSeconds",
    "-IdleIntervalSeconds", "$IdleIntervalSeconds",
    "-IdleAfterSeconds", "$IdleAfterSeconds",
    "-MaxCycles", "$DaemonMaxCycles",
    "-RestartDelaySeconds", "$LauncherRestartDelaySeconds",
    "-MaxRestarts", "$LauncherMaxRestarts"
)
if ($Execute) { $argParts += "-Execute" }
if ($ApprovePrivileged) { $argParts += "-ApprovePrivileged" }
if ($AutoOpenConnectors) { $argParts += "-AutoOpenConnectors" }
if ($SkipMissions) { $argParts += "-SkipMissions" }
if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
    $argParts += "-LogPath"
    $argParts += "`"$LogPath`""
}
$arguments = ($argParts -join " ")
$startupLauncherPath = Join-Path ([Environment]::GetFolderPath("Startup")) "$TaskName.cmd"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$userId = if ([string]::IsNullOrWhiteSpace($env:USERDOMAIN)) { $env:USERNAME } else { "$($env:USERDOMAIN)\$($env:USERNAME)" }
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $userId
$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$runLevel = if ($RunElevated) { "Highest" } else { "Limited" }
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel $runLevel

$restartInterval = New-TimeSpan -Seconds ([Math]::Max(30, $RestartIntervalSeconds))
try {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
        -RestartCount ([Math]::Max(0, $RestartCount)) `
        -RestartInterval $restartInterval
} catch {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Days 3650)
}

Remove-Item -Path $startupLauncherPath -Force -ErrorAction SilentlyContinue
$installMethod = ""
$installWarning = ""

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($triggerLogon, $triggerStartup) -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    $installMethod = "scheduled_task"
    if ($StartNow) {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
} catch {
    $installWarning = $_.Exception.Message
    $launcherContent = @(
        "@echo off",
        "powershell.exe $arguments"
    ) -join "`r`n"
    Set-Content -Path $startupLauncherPath -Value $launcherContent -Encoding Ascii -NoNewline
    $installMethod = "startup_folder"
    if ($StartNow) {
        Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden
    }
}

Write-Output "installed_task=$TaskName"
Write-Output "startup_script=$startupScript"
Write-Output "run_level=$runLevel"
Write-Output "install_method=$installMethod"
Write-Output "startup_launcher=$startupLauncherPath"
if (-not [string]::IsNullOrWhiteSpace($installWarning)) {
    Write-Output "install_warning=$installWarning"
}
Write-Output "interval_seconds=$IntervalSeconds"
Write-Output "idle_interval_seconds=$IdleIntervalSeconds"
Write-Output "idle_after_seconds=$IdleAfterSeconds"
Write-Output "daemon_max_cycles=$DaemonMaxCycles"
Write-Output "restart_count=$RestartCount"
Write-Output "restart_interval_seconds=$RestartIntervalSeconds"
