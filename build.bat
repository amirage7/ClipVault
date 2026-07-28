@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r app\requirements.txt
.venv\Scripts\python.exe -m pip install -q pyinstaller
if exist release rmdir /s /q release
if exist build rmdir /s /q build
if exist ClipboardManager.spec del /q ClipboardManager.spec
set "PKG_TMP=%TEMP%\ClipVault-build-%RANDOM%%RANDOM%"
.venv\Scripts\python.exe -m PyInstaller --noconsole --onefile --name ClipVault --distpath "%PKG_TMP%\dist" --workpath "%PKG_TMP%\work" --add-data "app/static;app/static" --hidden-import webview --hidden-import clr --hidden-import pythonnet --collect-submodules webview --collect-submodules pystray --collect-submodules pythonnet run.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
mkdir release
copy /y "%PKG_TMP%\dist\ClipVault.exe" "release\ClipVault.exe"
if errorlevel 1 (
    echo Could not copy the packaged EXE to release.
    pause
    exit /b 1
)
rmdir /s /q "%PKG_TMP%"
echo Build complete: release\ClipVault.exe
