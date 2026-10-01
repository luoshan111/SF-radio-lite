@echo off
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" goto fallback
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto fallback
goto run

:fallback
    rem Prefer the installed CPython 3.12; the venv launcher may be stale.
    set "PY=C:\Users\35640\AppData\Local\Programs\Python\Python312\python.exe"
    set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
if exist "%PY%" goto run

    rem Last resort for machines with the MSYS2 Python runtime.
    set "PY=D:\env\msys2\mingw64\bin\python.exe"
if not exist "%PY%" set "PY=python"

:run

"%PY%" main.py %*
if errorlevel 1 pause
