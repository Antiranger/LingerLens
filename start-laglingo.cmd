@echo off
setlocal
cd /d "%~dp0"

rem The root launcher is the desktop entry point. Keep the source Companion
rem available only as an explicit developer fallback so a stale port cannot
rem make the Electron build appear to be broken.
set "ELECTRON_EXE=%CD%\release\win-unpacked\LagLingo.exe"

if /I "%~1"=="-Prototype" goto prototype
if /I "%~1"=="--prototype" goto prototype

if /I "%~1"=="-CheckOnly" (
    if exist "%ELECTRON_EXE%" (
        echo [LagLingo] Electron unpacked build found: "%ELECTRON_EXE%"
        exit /b 0
    )
    echo [LagLingo] Electron unpacked build not found: "%ELECTRON_EXE%"
    echo [LagLingo] Run: npm run desktop:backend ^&^& npm run desktop:pack
    exit /b 2
)

if not exist "%ELECTRON_EXE%" (
    echo [LagLingo] Electron unpacked build not found: "%ELECTRON_EXE%"
    echo [LagLingo] Run: npm run desktop:backend ^&^& npm run desktop:pack
    echo [LagLingo] For the source Companion use: start-laglingo.cmd -Prototype
    exit /b 2
)

if /I "%~1"=="--smoke-test" (
    "%ELECTRON_EXE%" %*
    exit /b %ERRORLEVEL%
)

start "LagLingo" "%ELECTRON_EXE%" %*
exit /b 0

:prototype
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "prototype\hls-companion\scripts\restart-companion.ps1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%
