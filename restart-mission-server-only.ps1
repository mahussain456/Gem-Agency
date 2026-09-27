$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$StartScript = Join-Path $ProjectDir 'start-mission-control.ps1'
$MissionPort = 51764

$missionPids = netstat -ano -p tcp | Select-String ":$MissionPort\s+.*LISTENING" | ForEach-Object {
    $parts = ($_.ToString() -split '\s+') | Where-Object { $_ }
    if ($parts.Count -gt 0) { $parts[-1] }
} | Sort-Object -Unique
foreach ($pidText in $missionPids) {
    Write-Host "Stopping Mission Control PID $pidText"
    Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
& $StartScript
