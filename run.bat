@echo off
title AI Team #1 (web)
cd /d "%~dp0"
echo Starting AI Team #1 web version ... please wait.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Web
pause
