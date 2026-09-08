@echo off
cd /d "%~dp0"
start "" pythonw main.py 2>nul || start "" pyw main.py 2>nul || start "" python main.py
