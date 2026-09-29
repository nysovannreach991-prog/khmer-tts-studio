@echo off
chcp 65001 >nul
cd /d "%~dp0"
pip install -q -r requirements.txt
start "" pythonw gui.py
