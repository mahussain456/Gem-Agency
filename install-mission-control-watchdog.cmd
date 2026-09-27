@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "TASK_NAME=AgentOS Mission Control Watchdog"
set "WATCHER=%PROJECT_DIR%watch-mission-control.ps1"
set "STARTER=%PROJECT_DIR%start-watchdog-hidden.cmd"

if not exist "%WATCHER%" (
  echo Missing watchdog script: %WATCHER%
  exit /b 1
)

schtasks.exe //Create //TN "%TASK_NAME%" //SC ONLOGON //F //TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%WATCHER%\"" >nul 2>nul
if errorlevel 1 (
  echo Scheduled task creation denied or unavailable; installing Startup-folder fallback.
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$startup=[Environment]::GetFolderPath('Startup'); Copy-Item -Path '%STARTER%' -Destination (Join-Path $startup 'AgentOS Mission Control Watchdog.cmd') -Force"
) else (
  schtasks.exe //Run //TN "%TASK_NAME%" >nul 2>nul
  echo Installed scheduled task: %TASK_NAME%
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','%WATCHER%') -WindowStyle Hidden"
echo Watchdog launch requested.
