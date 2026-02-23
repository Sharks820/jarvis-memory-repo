param()

$ErrorActionPreference = "Stop"
$targets = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and (
        $_.CommandLine -match "jarvis_engine\.main\s+daemon-run" -or
        $_.CommandLine -match "jarvis_engine\.main\s+serve-mobile"
    )
}
foreach ($proc in $targets) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Output "stopped_count=$($targets.Count)"
