@echo off
setlocal
set "WATCHER=%~dp0watch-mission-control.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%WATCHER%"
