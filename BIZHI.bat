@echo off
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" main.py %*
if errorlevel 1 pause
