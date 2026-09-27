# ANON updater: download the latest main branch and copy it over THIS folder, then run setup.
# Kept as they are: .venv, journal, state, data, config\anon.toml, config\anon.live.toml, update.bat.
# Stop live.bat (Ctrl+C) before updating; open trades keep their SL/TP at the broker.
param(
    [string]$ZipUrl = "https://github.com/sanggodaethailand-jpg/BlackSlash/archive/refs/heads/main.zip",
    [switch]$NoSetup
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("anon_update_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work "main.zip"
    Write-Host "== Downloading $ZipUrl ==" -ForegroundColor Cyan
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $work -Force
    $src = Get-ChildItem $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName "anon") } | Select-Object -First 1
    if ($null -eq $src) { throw "the download does not look like the ANON repo" }

    Write-Host "== Copying into $Root ==" -ForegroundColor Cyan
    $skipDirs = @(".venv", "journal", "state", "data")
    $skipFiles = @("anon.toml", "anon.live.toml", "update.bat")
    $copied = 0
    foreach ($file in Get-ChildItem $src.FullName -Recurse -File) {
        $rel = $file.FullName.Substring($src.FullName.Length).TrimStart('\', '/')
        $top = ($rel -split '[\\/]')[0]
        if ($skipDirs -contains $top) { continue }
        if ($skipFiles -contains $file.Name) { continue }
        $dest = Join-Path $Root $rel
        $destDir = Split-Path -Parent $dest
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item $file.FullName $dest -Force
        $copied++
    }
    Write-Host "updated $copied files (config, journal, state, data and .venv kept)" -ForegroundColor Green
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $NoSetup) {
    & (Join-Path $PSScriptRoot "setup_windows.ps1")
}
