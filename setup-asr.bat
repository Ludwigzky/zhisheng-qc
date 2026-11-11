@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python tools\setup_asr.py
pause
