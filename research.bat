@echo off
rem Research: auto-levels backtest on 3 years of MT5 H1 history, min RR 1.0 vs 1.5. Never sends orders.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task research %*
pause
