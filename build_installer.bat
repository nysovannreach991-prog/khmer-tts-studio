@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python build_installer.py
if not errorlevel 1 explorer "%~dp0dist"
pause
