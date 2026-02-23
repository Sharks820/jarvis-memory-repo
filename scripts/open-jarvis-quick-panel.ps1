param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$url = "http://$BindHost`:$Port/quick"
Start-Process $url
Write-Output "opened=$url"
Write-Output "tip=Pin this script or URL as desktop/start shortcut for one-click Jarvis access."
