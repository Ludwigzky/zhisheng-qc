@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv-asr\Scripts\python.exe" (
  ".venv-asr\Scripts\python.exe" tools\check_asr.py %*
) else (
  python tools\check_asr.py %*
)
pause
