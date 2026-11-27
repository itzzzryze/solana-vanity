@echo off
setlocal DisableDelayedExpansion
set "VANITY_PY=%~dp0runtime\python.exe"
if not exist "%VANITY_PY%" set "VANITY_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VANITY_PY%" (
    echo Run Setup.cmd first, or extract the complete portable Windows ZIP.
    exit /b 1
)
"%VANITY_PY%" -B "%~dp0search.py" %*
exit /b %errorlevel%
