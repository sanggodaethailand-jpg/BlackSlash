@echo off
rem One-click run: doctor, math, backtest on MT5 history, then the dry-run loop. Never sends real orders.
rem Single task: run.bat -Task doctor ^| math ^| backtest ^| report ^| dryrun
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" %*
pause
