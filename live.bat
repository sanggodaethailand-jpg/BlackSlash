@echo off
rem REAL MONEY: the owner confirms, doctor checks everything, then the live loop runs. Every trade still needs the typed approval.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -Task golive %*
pause
