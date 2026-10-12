@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" %*
set "iotdesign_exit=%ERRORLEVEL%"
if not "%iotdesign_exit%"=="0" (
  echo.
  echo IoTDesign setup failed. Read the message above and data\logs.
  pause
)
exit /b %iotdesign_exit%
