@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "prototype\hls-companion\scripts\restart-companion.ps1" %*
exit /b %ERRORLEVEL%
