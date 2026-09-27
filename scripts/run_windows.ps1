# ANON runner (Windows PowerShell 5.1 or PowerShell 7).
#   all      doctor -> math on the real account -> backtest on MT5 history -> dry-run loop (default)
#   doctor   readiness check only
#   math     risk numbers from the real account
#   backtest backtest on the last -Days of MT5 H1 history (journal\backtest.jsonl)
#   report   Quant report of the live journal
#   research auto-levels backtest on MT5 history (default 3 years), min RR 1.0 vs 1.5, summary only
#   dryrun   live loop in dry-run: computes everything, sends nothing (Ctrl+C to stop)
# This script never sends real orders. Real orders need: dry_run = false, confirmed_by set,
# and a manual "anon live --confirm-live".
param(
    [ValidateSet("all", "doctor", "math", "backtest", "report", "dryrun", "research")]
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
    "research" {
        $span = if ($PSBoundParameters.ContainsKey("Days")) { $Days } else { 1095 }
        foreach ($rr in @("1.0", "1.5")) {
            [void](Anon @("backtest", "--mt5", "--days", "$span", "--auto", "--min-rr", $rr, "--summary",
                          "--journal", "journal/research_rr$rr.jsonl"))
        }
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
