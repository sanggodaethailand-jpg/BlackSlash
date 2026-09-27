# ANON runner (Windows PowerShell 5.1 or PowerShell 7).
#   all      doctor -> math on the real account -> backtest on MT5 history -> dry-run loop (default)
#   doctor   readiness check only
#   math     risk numbers from the real account
#   backtest backtest on the last -Days of MT5 H1 history (journal\backtest.jsonl)
#   report   Quant report of the live journal
#   research robustness sweep on MT5 history (default 5 years): auto levels, min RR 1.0-2.0 x lookback 48/72/96
#   dryrun   live loop in dry-run: computes everything, sends nothing (Ctrl+C to stop)
#   golive   REAL MONEY (live.bat): asks the owner to confirm, writes config\anon.live.toml,
#            runs doctor on it, then the live loop; every trade still needs the typed approval
#   levels   set this week's levels and their last day in config\anon.toml (levels.bat)
#   forward  export the newest H1 bars, then the forward watch report (forward.bat)
# Only the golive task can send real orders, and only after the owner types the confirmation.
param(
    [ValidateSet("all", "doctor", "math", "backtest", "report", "dryrun", "research", "golive", "levels", "forward")]
    [string]$Task = "all",
    [int]$Days = 365
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Py = Join-Path (Join-Path (Join-Path $Root ".venv") "Scripts") "python.exe"
if (-not (Test-Path $Py)) { throw "Run setup.bat first (.venv not found)" }
$Config = Join-Path (Join-Path $Root "config") "anon.toml"
if (-not (Test-Path $Config)) { $Config = Join-Path (Join-Path $Root "config") "anon.example.toml" }

# Start-Process keeps python attached to this console (output and the approval prompt
# stay visible); calling it inside a function with & would capture its output instead.
function Anon([string[]]$Arguments, [switch]$AllowFail) {
    Write-Host "`n>> anon $($Arguments -join ' ')" -ForegroundColor Cyan
    $argList = @("-m", "anon", "--config", "`"$Config`"") + $Arguments
    $proc = Start-Process -FilePath $Py -ArgumentList $argList -NoNewWindow -PassThru
    $null = $proc.Handle  # makes ExitCode reliable on Windows PowerShell 5.1
    $proc.WaitForExit()
    $code = $proc.ExitCode
    if ($code -ne 0 -and -not $AllowFail) { throw "anon $($Arguments[0]) failed (exit code $code)" }
    return $code
}

$BacktestArgs = @("backtest", "--mt5", "--days", "$Days", "--journal", "journal/backtest.jsonl")

switch ($Task) {
    "doctor"   { [void](Anon @("doctor") -AllowFail) }
    "math"     { [void](Anon @("math", "--mt5", "--usdthb", "33.42")) }
    "backtest" { [void](Anon $BacktestArgs) }
    "report"   { [void](Anon @("report", "--journal", "journal/anon_journal.jsonl")) }
    "dryrun"   { [void](Anon @("live")) }
    "golive"   { [void](Anon @("golive") -AllowFail) }
    "levels"   { [void](Anon @("levels") -AllowFail) }
    "forward"  {
        [void](Anon @("export", "--days", "3650", "--out", "data/BTCUSDc_H1.csv"))
        [void](Anon @("lab", "--forward", "--csv", "data/BTCUSDc_H1.csv"))
    }
    "research" {
        $span = if ($PSBoundParameters.ContainsKey("Days")) { $Days } else { 1825 }
        [void](Anon @("sweep", "--mt5", "--days", "$span"))
    }
    "all" {
        $code = Anon @("doctor") -AllowFail
        if ($code -ne 0) {
            Write-Host "`nanon doctor found problems ([XX] above). Fix them first (usually: open MT5 and log in)." -ForegroundColor Yellow
            exit 1
        }
        [void](Anon @("math", "--mt5", "--usdthb", "33.42"))
        [void](Anon $BacktestArgs)
        Write-Host "`nStarting the dry-run loop: it logs what it WOULD do on every closed H1 bar. Ctrl+C to stop." -ForegroundColor Green
        [void](Anon @("live"))
    }
}
