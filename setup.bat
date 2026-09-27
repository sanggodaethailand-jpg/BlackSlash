@echo off
rem One-click setup: Python venv, packages, config, MT5 indicator (copy + compile), tests, doctor.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
pause
