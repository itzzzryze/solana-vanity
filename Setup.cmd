@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0.venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv "%~dp0.venv"
    ) else (
        py -3 -m venv "%~dp0.venv"
    )
    if errorlevel 1 goto failed
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
if /i "%~1"=="--cpu-only" goto ready
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-opencl.txt"
if errorlevel 1 goto failed
:ready
echo Setup complete. Edit address.txt and strings.txt, then run Search.cmd.
exit /b 0
:failed
echo Setup could not finish. Use 64-bit Python 3.12 or 3.13 and rerun Setup.cmd.
exit /b 1
