@echo off
setlocal
set "LINGERLENS_DATA_DIR=%LOCALAPPDATA%\LingerLens-SubtitleValidation-20260924"
set "LINGERLENS_BACKEND_LOG=0"
if not exist "%~dp0release\win-unpacked\LingerLens.exe" (
  echo The tested desktop build is missing. Keep this launcher beside the release folder.
  pause
  exit /b 1
)
if not exist "%LINGERLENS_DATA_DIR%\runtime\providers.json" (
  echo The isolated validation profile has not been prepared on this computer.
  echo This launcher does not change your installed LingerLens settings.
  pause
  exit /b 1
)
start "" "%~dp0release\win-unpacked\LingerLens.exe"
endlocal
