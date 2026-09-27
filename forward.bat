@echo off
rem Forward watch: export the newest H1 bars from MT5, then judge the watched ideas on bars nobody had seen.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task forward %*
pause
