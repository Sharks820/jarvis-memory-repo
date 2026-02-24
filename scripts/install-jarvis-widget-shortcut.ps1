param(
    [string]$ShortcutName = "Jarvis Widget",
    [string]$Hotkey = "CTRL+ALT+K"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $scriptDir "start-jarvis-services.ps1"
if (-not (Test-Path $launcher)) {
    throw "Missing launcher script: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop ("$ShortcutName.lnk")
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($lnkPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -StartWidget -BindHost 127.0.0.1"
$shortcut.WorkingDirectory = Split-Path -Parent $scriptDir
$iconPath = Join-Path $scriptDir "jarvis.ico"
if (Test-Path $iconPath) {
    $shortcut.IconLocation = $iconPath
} else {
    $shortcut.IconLocation = "%SystemRoot%\\System32\\SHELL32.dll,220"
}
$shortcut.Hotkey = $Hotkey
$shortcut.Save()

Write-Output "shortcut=$lnkPath"
Write-Output "hotkey=$Hotkey"
