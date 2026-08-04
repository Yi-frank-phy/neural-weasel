@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-neural-weasel.ps1"
if errorlevel 1 (
  echo.
  echo Neural Weasel failed to start. Review the message above.
  pause
  exit /b 1
)
endlocal
