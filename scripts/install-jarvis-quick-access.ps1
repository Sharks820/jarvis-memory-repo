param(
    [string]$ShortcutName = "Jarvis Quick Access",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8787,
    [string]$Hotkey = "CTRL+ALT+J"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $scriptDir "open-jarvis-quick-panel.ps1"
if (-not (Test-Path $launcher)) {
    throw "Missing launcher script: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop ("$ShortcutName.lnk")

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($lnkPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -BindHost $BindHost -Port $Port"
$shortcut.WorkingDirectory = Split-Path -Parent $scriptDir
$shortcut.IconLocation = "%SystemRoot%\\System32\\SHELL32.dll,220"
$shortcut.Hotkey = $Hotkey
$shortcut.Save()

Write-Output "shortcut=$lnkPath"
Write-Output "hotkey=$Hotkey"
Write-Output "note=Hotkeys on .lnk files require Explorer running and this shortcut to exist on Desktop or Start Menu."
