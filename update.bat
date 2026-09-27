@echo off
rem Update in place: download the latest version over THIS folder (config, journal, .venv kept), then setup.
rem Stop live.bat first (Ctrl+C). Open trades keep their SL/TP at the broker.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_windows.ps1" %*
pause
