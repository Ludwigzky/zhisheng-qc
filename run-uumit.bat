@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launch.ps1" -Mode http -Port 8870 -BindAddress 127.0.0.1 -NoBrowser
pause
