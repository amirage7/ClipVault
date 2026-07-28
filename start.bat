@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r app\requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)
.venv\Scripts\python.exe run.py
if errorlevel 1 (
    echo Application exited with an error.
    pause
    exit /b 1
)
