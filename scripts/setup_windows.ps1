# ANON one-shot setup for Windows + MetaTrader 5 (Windows PowerShell 5.1 or PowerShell 7).
#  1. creates .venv and installs the package (MetaTrader5, anthropic, pytest)
#  2. copies config\anon.example.toml to config\anon.toml if missing (dry_run = true)
#  3. copies ANON_Levels_v2.mq5 into every MT5 data folder and compiles it with MetaEditor
#  4. runs the tests
#  5. runs "anon doctor"
# It never places orders.
param(
    [switch]$SkipPython,       # only (re)install the indicator
    [string]$TerminalRoot = "" # override %APPDATA%\MetaQuotes\Terminal (for testing)
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Step([string]$Text) { Write-Host "`n== $Text ==" -ForegroundColor Cyan }
function Check([string]$What) { if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" } }

function Install-Indicator([string]$DataRoot) {
    $source = Join-Path (Join-Path (Join-Path $Root "mql5") "Indicators") "ANON_Levels_v2.mq5"
    $installed = 0
    if (-not (Test-Path $DataRoot)) {
        Write-Host "MT5 data folder not found: $DataRoot" -ForegroundColor Yellow
        return 0
    }
    foreach ($dir in Get-ChildItem $DataRoot -Directory) {
        $indicators = Join-Path (Join-Path $dir.FullName "MQL5") "Indicators"
        if (-not (Test-Path $indicators)) { continue }
        $target = Join-Path $indicators "ANON_Levels_v2.mq5"
        Copy-Item $source $target -Force
        $installed++
        Write-Host "copied  -> $target"

        $editor = $null
        $origin = Join-Path $dir.FullName "origin.txt"
        if (Test-Path $origin) {
            $installDir = (Get-Content $origin -Raw).Trim()
            $candidate = Join-Path $installDir "MetaEditor64.exe"
            if (Test-Path $candidate) { $editor = $candidate }
        }
        if ($null -eq $editor) {
            Write-Host "MetaEditor64.exe not found for this terminal: open the file in MetaEditor and press F7" -ForegroundColor Yellow
            continue
        }
        $log = Join-Path $indicators "ANON_Levels_v2.compile.log"
        $ex5 = [System.IO.Path]::ChangeExtension($target, ".ex5")
        $before = Get-Date
        if (Test-Path $log) { Remove-Item $log -Force }
        $run = @{ FilePath = $editor; ArgumentList = @("/compile:`"$target`"", "/log:`"$log`""); Wait = $true }
        if ($PSVersionTable.PSEdition -eq "Desktop" -or $IsWindows) { $run.WindowStyle = "Hidden" }
        Start-Process @run
        if ((Test-Path $ex5) -and ((Get-Item $ex5).LastWriteTime -ge $before.AddSeconds(-2))) {
            Write-Host "compiled -> $ex5" -ForegroundColor Green
        } else {
            Write-Host "compile did not produce a fresh .ex5 - log: $log" -ForegroundColor Yellow
            if (Test-Path $log) { Get-Content $log | Select-Object -Last 15 | ForEach-Object { Write-Host "  $_" } }
        }
    }
    if ($installed -eq 0) {
        Write-Host "No MT5 terminal found. In MT5: File > Open Data Folder > MQL5\Indicators, copy $source there, then F7 in MetaEditor." -ForegroundColor Yellow
    }
    return $installed
}

if ($TerminalRoot -eq "") {
    $TerminalRoot = Join-Path (Join-Path $env:APPDATA "MetaQuotes") "Terminal"
}

if (-not $SkipPython) {
    Step "1/5 Python virtual environment"
    $Py = Join-Path (Join-Path (Join-Path $Root ".venv") "Scripts") "python.exe"
    if (-not (Test-Path $Py)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv .venv
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv .venv
        } else {
            throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run setup.bat again."
        }
        Check "create .venv"
    }
    & $Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required. Delete the .venv folder, install a newer Python, run setup.bat again." }
    & $Py -m pip install --upgrade pip --quiet
    Check "pip upgrade"
    & $Py -m pip install -e ".[mt5,ai,dev]" --quiet
    Check "pip install"

    Step "2/5 Config"
    $config = Join-Path (Join-Path $Root "config") "anon.toml"
    if (Test-Path $config) {
        Write-Host "config\anon.toml already exists - left unchanged"
    } else {
        Copy-Item (Join-Path (Join-Path $Root "config") "anon.example.toml") $config
        Write-Host "created config\anon.toml (dry_run = true, confirmed_by empty)"
    }
}

Step "3/5 MT5 indicator"
$count = Install-Indicator $TerminalRoot
Write-Host "indicator installed in $count terminal(s)"

if (-not $SkipPython) {
    Step "4/5 Tests"
    & $Py -m pytest -q
    Check "tests"

    Step "5/5 anon doctor"
    & $Py -m anon --config config\anon.toml doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nFix the [XX] items above (usually: open MT5 and log in), then run run.bat" -ForegroundColor Yellow
    } else {
        Write-Host "`nSetup done. Next: run.bat (doctor -> math -> backtest on MT5 history -> dry-run)" -ForegroundColor Green
    }
}
