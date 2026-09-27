param(
    [int]$MissionPort = 51764,
    [int]$ApiPort = 8643,
    [string]$HostName = "127.0.0.1",
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA 'hermes'),
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectDir "start-mission-control.ps1"
$WatchLog = Join-Path $ProjectDir "mission-control-watchdog.log"
$MissionHealth = "http://${HostName}:$MissionPort/api/snapshot"
$ApiHealth = "http://${HostName}:$ApiPort/health"
$ApiModels = "http://${HostName}:$ApiPort/v1/models"
$EnvPath = Join-Path $HermesHome ".env"
$SpecialistProfiles = @("orchestrator", "scout", "scribe", "reach", "dev", "lumen", "antigravity", "chatgpt", "rank")
$MutexName = "Local\AgentOSMissionControlWatchdog-$MissionPort"
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $MutexName, [ref]$createdNew)
if (-not $createdNew) { exit 0 }

function Write-WatchLog([string]$Message) {
    $stamp = (Get-Date).ToString("s")
    Add-Content -Path $WatchLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Get-EnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content -Path $Path) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.+?)\s*$" -and -not $line.TrimStart().StartsWith("#")) {
            return $Matches[1].Trim('"').Trim("'")
        }
    }
    return $null
}

function Test-UrlJson([string]$Url, [hashtable]$Headers = @{}) {
    try {
        $r = Invoke-WebRequest -Uri $Url -Headers $Headers -UseBasicParsing -TimeoutSec 8
        if ($r.StatusCode -lt 200 -or $r.StatusCode -ge 300) { return $null }
        return ($r.Content | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Test-MissionHealthy {
    $json = Test-UrlJson -Url $MissionHealth
    if ($null -eq $json -or $null -eq $json.generated_at) { return $false }
    $apiState = $null
    try { $apiState = $json.gateway.platforms.api_server.state } catch {}
    return ($apiState -eq "connected")
}

function Test-ApiHealthy {
    $apiKey = Get-EnvValue -Path $EnvPath -Key "API_SERVER_KEY"
    if (-not $apiKey) { return $false }
    $headers = @{ Authorization = "Bearer $apiKey" }
    $health = Test-UrlJson -Url $ApiHealth -Headers $headers
    if ($null -eq $health -or $health.status -ne "ok") { return $false }
    $models = Test-UrlJson -Url $ApiModels -Headers $headers
    if ($null -eq $models -or $models.object -ne "list" -or $null -eq $models.data) { return $false }
    return $true
}

function Get-UnhealthyProfiles {
    # Advisory only: the launcher repair restarts mission control and the API
    # server, not per-profile gateways, so a failing profile can never be fixed
    # by Invoke-Repair. Reporting instead of repairing prevents the endless
    # repair loop this used to cause.
    $hermesExe = Join-Path $HermesHome "hermes-agent\venv\Scripts\hermes.exe"
    if (-not (Test-Path $hermesExe)) { return @("hermes.exe missing") }
    $bad = @()
    foreach ($profile in $SpecialistProfiles) {
        try {
            $output = & $hermesExe --profile $profile gateway status 2>&1 | Out-String
            if ($output -notmatch "Gateway is running") { $bad += $profile }
        } catch {
            $bad += $profile
        }
    }
    return $bad
}

function Invoke-Repair([string]$Reason) {
    Write-WatchLog "$Reason; running launcher repair"
    try {
        $repairLog = Join-Path $ProjectDir "mission-control-watchdog-repair.log"
        $repairErr = Join-Path $ProjectDir "mission-control-watchdog-repair.err.log"
        $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Launcher, "-MissionPort", $MissionPort, "-ApiPort", $ApiPort, "-HostName", $HostName, "-HermesHome", $HermesHome)
        $repair = Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Hidden -PassThru -RedirectStandardOutput $repairLog -RedirectStandardError $repairErr
        if (-not $repair.WaitForExit(120000)) {
            try { Stop-Process -Id $repair.Id -Force -ErrorAction SilentlyContinue } catch {}
            Write-WatchLog "Repair command timed out after 120s"
        }
        Start-Sleep -Seconds 2
        if ((Test-MissionHealthy) -and (Test-ApiHealthy)) {
            Write-WatchLog "Repair successful"
        } else {
            Write-WatchLog "Repair attempted but strict health checks still failed"
        }
    } catch {
        Write-WatchLog "Repair command failed: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    }
}

Write-WatchLog "Watchdog started for Mission=$MissionHealth API=$ApiHealth"

$lastProfileWarn = [datetime]::MinValue
while ($true) {
    $missionOk = Test-MissionHealthy
    $apiOk = Test-ApiHealthy
    if (-not $missionOk -or -not $apiOk) {
        Invoke-Repair "Unhealthy strict check mission=$missionOk api=$apiOk"
    }
    $badProfiles = Get-UnhealthyProfiles
    if ($badProfiles.Count -gt 0 -and ((Get-Date) - $lastProfileWarn).TotalMinutes -ge 30) {
        Write-WatchLog "Advisory: profile gateways not running (no auto-repair): $($badProfiles -join ', ')"
        $lastProfileWarn = Get-Date
    }
    Start-Sleep -Seconds $IntervalSeconds
}
