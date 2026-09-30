@echo off
title AI Team #1
cd /d "%~dp0"
echo Starting AI Team #1 ... please wait, do not close this window.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
if errorlevel 1 pause
