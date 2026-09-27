param(
    [int]$MissionPort = 51764,
    [int]$ApiPort = 8643,
    [string]$HostName = "127.0.0.1",
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA 'hermes')
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$HermesExe = Join-Path $HermesHome "hermes-agent\venv\Scripts\hermes.exe"
$EnvPath = Join-Path $HermesHome ".env"
$GatewayLog = Join-Path $ProjectDir "hermes-gateway-api-server-8643.log"
$MissionLog = Join-Path $ProjectDir "mission-control-51764.log"
$SpecialistProfiles = @("orchestrator", "scout", "scribe", "reach", "dev", "lumen", "antigravity", "chatgpt", "rank")

function Ensure-EnvLine([string]$Path, [string]$Key, [string]$Value) {
    if (-not (Test-Path $Path)) { New-Item -ItemType File -Path $Path -Force | Out-Null }
    $lines = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($Key))\s*=" -and -not $lines[$i].TrimStart().StartsWith("#")) {
            $lines[$i] = "$Key=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) { $lines += "$Key=$Value" }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
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

function New-ApiKey {
    $bytes = New-Object byte[] 32
    # Windows PowerShell 5.1 / older .NET Framework does not support
    # RandomNumberGenerator.Fill(). Use the compatible instance API instead.
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return "mc_" + [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
}

function Test-Url([string]$Url, [hashtable]$Headers = @{}) {
    try {
        $r = Invoke-WebRequest -Uri $Url -Headers $Headers -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    } catch { return $false }
}

function Wait-Url([string]$Url, [hashtable]$Headers = @{}, [int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url -Url $Url -Headers $Headers) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-ListeningPids([int]$Port) {
    $foundPids = @()
    $lines = netstat -ano -p tcp | Select-String ":$Port\s+.*LISTENING"
    foreach ($line in $lines) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ }
        if ($parts.Count -gt 0) { $foundPids += [int]$parts[-1] }
    }
    return $foundPids | Sort-Object -Unique
}

function Test-ProfileGatewayRunning([string]$Profile) {
    try {
        $output = & $HermesExe --profile $Profile gateway status 2>&1 | Out-String
        return ($output -match "Gateway is running")
    } catch {
        return $false
    }
}

function Ensure-ProfileGateways {
    foreach ($profile in $SpecialistProfiles) {
        if (Test-ProfileGatewayRunning -Profile $profile) {
            Write-Host "Profile gateway already running: $profile"
            continue
        }
        $profileLog = Join-Path $ProjectDir "hermes-gateway-$profile.log"
        $profileErr = Join-Path $ProjectDir "hermes-gateway-$profile.err.log"
        Write-Host "Starting profile gateway: $profile"
        Start-Process -FilePath $HermesExe -ArgumentList @("--profile", $profile, "gateway", "run") -WorkingDirectory $HermesHome -WindowStyle Minimized -RedirectStandardOutput $profileLog -RedirectStandardError $profileErr
        Start-Sleep -Seconds 2
    }
}

if (-not (Test-Path $HermesExe)) { throw "Hermes executable not found: $HermesExe" }
if (-not (Test-Path (Join-Path $ProjectDir "server.py"))) { throw "server.py not found in $ProjectDir" }

$apiKey = Get-EnvValue -Path $EnvPath -Key "API_SERVER_KEY"
if (-not $apiKey) {
    $apiKey = New-ApiKey
    Write-Host "Created API_SERVER_KEY in $EnvPath"
}
Ensure-EnvLine -Path $EnvPath -Key "API_SERVER_KEY" -Value $apiKey
Ensure-EnvLine -Path $EnvPath -Key "API_SERVER_ENABLED" -Value "true"
Ensure-EnvLine -Path $EnvPath -Key "API_SERVER_HOST" -Value $HostName
Ensure-EnvLine -Path $EnvPath -Key "API_SERVER_PORT" -Value "$ApiPort"
Ensure-EnvLine -Path $EnvPath -Key "API_SERVER_CORS_ORIGINS" -Value "http://127.0.0.1:$MissionPort,http://localhost:$MissionPort"

$env:HERMES_HOME = $HermesHome
$env:API_SERVER_KEY = $apiKey
$env:API_SERVER_ENABLED = "true"
$env:API_SERVER_HOST = $HostName
$env:API_SERVER_PORT = "$ApiPort"
$env:API_SERVER_CORS_ORIGINS = "http://127.0.0.1:$MissionPort,http://localhost:$MissionPort"
$env:MISSION_CONTROL_HOST = $HostName
$env:MISSION_CONTROL_PORT = "$MissionPort"
$env:MISSION_CONTROL_GATEWAY_CHAT_URL = "http://localhost:$ApiPort/v1/chat/completions"
$env:MISSION_CONTROL_CONTENT_ROOT = Join-Path $ProjectDir "content"

$headers = @{ Authorization = "Bearer $apiKey" }
$apiHealth = "http://${HostName}:$ApiPort/health"
$apiModels = "http://${HostName}:$ApiPort/v1/models"
$missionHealth = "http://${HostName}:$MissionPort/api/snapshot"

if (-not (Test-Url -Url $apiHealth -Headers $headers)) {
    Write-Host "Starting Hermes API Server on http://${HostName}:$ApiPort ..."
    Start-Process -FilePath $HermesExe -ArgumentList @("gateway", "run") -WorkingDirectory $HermesHome -WindowStyle Minimized -RedirectStandardOutput $GatewayLog -RedirectStandardError (Join-Path $ProjectDir "hermes-gateway-api-server-8643.err.log")
    if (-not (Wait-Url -Url $apiHealth -Headers $headers -Seconds 90)) {
        throw "Hermes API Server did not become healthy on $apiHealth. See $GatewayLog"
    }
} else {
    Write-Host "Hermes API Server already healthy on $apiHealth"
}

Ensure-ProfileGateways

$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating local Python venv..."
    python -m venv .venv
}
if (Test-Path (Join-Path $ProjectDir "requirements.txt")) {
    & $venvPython -m pip install -r requirements.txt
}

if (-not (Test-Url -Url $missionHealth)) {
    Write-Host "Starting AgentOS Mission Control on http://${HostName}:$MissionPort/ ..."
    Start-Process -FilePath $venvPython -ArgumentList @("server.py") -WorkingDirectory $ProjectDir -WindowStyle Minimized -RedirectStandardOutput $MissionLog -RedirectStandardError (Join-Path $ProjectDir "mission-control-51764.err.log")
    if (-not (Wait-Url -Url $missionHealth -Seconds 60)) {
        throw "Mission Control did not become healthy on $missionHealth. See $MissionLog"
    }
} else {
    Write-Host "Mission Control already healthy on $missionHealth"
}

if (-not (Test-Url -Url $apiModels -Headers $headers)) {
    throw "Hermes API Server /v1/models failed on $apiModels"
}

Write-Host ""
Write-Host "AgentOS Mission Control is ready: http://${HostName}:$MissionPort/"
Write-Host "Hermes API Server is ready:      http://${HostName}:$ApiPort/v1"
Write-Host "Mission Control PID(s):          $((Get-ListeningPids $MissionPort) -join ', ')"
Write-Host "API Server PID(s):               $((Get-ListeningPids $ApiPort) -join ', ')"
Write-Host ""
Write-Host "You can close this window. The server processes keep running."
