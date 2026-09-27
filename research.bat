@echo off
rem Research: robustness sweep on 5 years of MT5 H1 history (auto levels, min RR x lookback). Never sends orders.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task research %*
pause
