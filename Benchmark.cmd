@echo off
call "%~dp0Search.cmd" --benchmark %*
exit /b %errorlevel%
