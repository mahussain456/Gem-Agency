$ErrorActionPreference = 'Continue'
$ProjectDir = $PSScriptRoot
$WatchScript = Join-Path $ProjectDir 'watch-mission-control.ps1'
$StartScript = Join-Path $ProjectDir 'start-mission-control.ps1'

$watch = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watch-mission-control.ps1*' }
foreach ($p in $watch) {
    Write-Host "Stopping watchdog PID $($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

$missionPids = netstat -ano -p tcp | Select-String ':51764\s+.*LISTENING' | ForEach-Object {
    $parts = ($_.ToString() -split '\s+') | Where-Object { $_ }
    if ($parts.Count -gt 0) { $parts[-1] }
} | Sort-Object -Unique
foreach ($pidText in $missionPids) {
    Write-Host "Stopping Mission Control PID $pidText"
    Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
Write-Host 'Starting updated watchdog...'
Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$WatchScript) -WindowStyle Hidden
Start-Sleep -Seconds 5
Write-Host 'Running launcher verification...'
& $StartScript
