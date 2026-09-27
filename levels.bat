@echo off
rem Set this week's levels (A zone, gray, TP1...) and the last day they may open trades.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task levels %*
pause
