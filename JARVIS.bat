@echo off
title Run J.A.R.V.I.S.
cd /d "%~dp0"
color 0B
echo.
echo   RUN J.A.R.V.I.S.
echo   Starts if offline  ^|  Stops if already running
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Jarvis-Launcher.ps1" %*
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% NEQ 0 (
  echo.
  echo   Something went wrong. Press any key to close.
  pause >nul
)
exit /b %EXITCODE%
