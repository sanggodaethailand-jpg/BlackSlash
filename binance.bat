@echo off
rem Download Binance BTCUSDT H1 bars with market-buy volume (public data, SHA-256 checked) for the lab.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task binance %*
pause
